from __future__ import annotations

import logging
from typing import Any, Dict, Mapping

from ..agents.base import BaseDomainAgent
from ..approval import ApprovalCoordinator
from ..approval.adapters import (
    approval_request_to_legacy_payload,
    legacy_decision_to_record,
)
from ..approval.models import ApprovalRequest
from ..approval_store import ApprovalStore
from ..interrupt_store import InterruptStore
from ..checkpoint_store import CheckpointStore
from ..execution_store import ExecutionStore
from ..execution.security import ExecutionSafetyError, validate_execution_binding
from ..memory_store import ProcessMemoryStore
from ..mcp import MCPClient, MCPConnectionManager
from ..context.models import ExecutionContext
from ..runtime.contracts import AgentResult
from ..runtime.supervisor import RuleBasedSupervisor
from ..schemas import ApprovalDecisionRequest, TicketRequest
from ..session.models import utc_now
from ..system_event_store import SystemEventStore
from ..session_store import SessionStore
from ..state.approval_transformers import (
    apply_approval_gate_result_to_state,
    apply_approval_resume_result_to_state,
    apply_execution_results_to_state,
    build_approval_gate_input_from_state,
    execution_result_to_state,
)
from ..state.incident_state import IncidentState
from ..state.transformers import build_initial_incident_state, incident_state_from_legacy
from .state import ApprovalGraphState, TicketGraphState


logger = logging.getLogger(__name__)


class OrchestratorGraphNodes:
    def __init__(
        self,
        supervisor: RuleBasedSupervisor,
        approval_store: ApprovalStore,
        session_store: SessionStore,
        interrupt_store: InterruptStore,
        process_memory_store: ProcessMemoryStore,
        connection_manager: MCPConnectionManager,
        agents: Mapping[str, BaseDomainAgent],
        approval_coordinator: ApprovalCoordinator | None = None,
        execution_store: ExecutionStore | None = None,
        system_event_store: SystemEventStore | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.approval_store = approval_store
        self.session_store = session_store
        self.interrupt_store = interrupt_store
        self.process_memory_store = process_memory_store
        self.connection_manager = connection_manager
        self.agents = agents
        self.approval_coordinator = approval_coordinator or ApprovalCoordinator()
        self.checkpoint_store = CheckpointStore(getattr(session_store, 'db_path', '')) if getattr(session_store, 'db_path', '') else None
        self.execution_store = execution_store or (ExecutionStore(getattr(session_store, 'db_path', '')) if getattr(session_store, 'db_path', '') else None)
        self.system_event_store = system_event_store or (SystemEventStore(getattr(session_store, 'db_path', '')) if getattr(session_store, 'db_path', '') else None)

    @staticmethod
    def _require_approval_request_domain(state: ApprovalGraphState) -> ApprovalRequest:
        approval_request = state.get("approval_request_domain")
        if approval_request is None:
            raise ValueError("approval_request_domain is required for approval graph execution")
        return approval_request if isinstance(approval_request, ApprovalRequest) else ApprovalRequest.model_validate(approval_request)

    async def ingest(self, state: TicketGraphState) -> Dict[str, Any]:
        request = state["request"]
        incident_state = state.get("incident_state") or build_initial_incident_state(request)
        logger.info("graph.ingest ticket_id=%s", request.ticket_id)
        return {
            "incident_state": incident_state,
            "pending_node": "supervisor_route",
        }

    def _append_process_entry(
        self,
        *,
        session_id: str,
        thread_id: str,
        ticket_id: str,
        event_type: str,
        stage: str,
        source: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        refs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.process_memory_store.append(
            {
                "session_id": session_id,
                "thread_id": thread_id,
                "ticket_id": ticket_id,
                "event_type": event_type,
                "stage": stage,
                "source": source,
                "summary": summary,
                "payload": dict(payload or {}),
                "refs": dict(refs or {}),
            }
        )

    def _append_system_event(
        self,
        *,
        session_id: str,
        thread_id: str,
        ticket_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if self.system_event_store is None:
            return None
        return self.system_event_store.create(
            {
                "session_id": session_id,
                "thread_id": thread_id,
                "ticket_id": ticket_id,
                "event_type": event_type,
                "payload": dict(payload or {}),
                "metadata": dict(metadata or {}),
            }
        )

    async def supervisor_route(self, state: TicketGraphState) -> Dict[str, Any]:
        request = state["request"]
        decision = await self.supervisor.route(request)
        execution_context = state.get("execution_context")
        task = self.supervisor.build_task(request, decision, execution_context=execution_context)
        incident_state = state.get("incident_state") or build_initial_incident_state(request)
        incident_state.routing = decision.model_dump()
        incident_state.status = "routed"
        self._append_process_entry(
            session_id=str(state.get("session_id") or state.get("thread_id") or request.ticket_id),
            thread_id=str(state.get("thread_id") or request.ticket_id),
            ticket_id=request.ticket_id,
            event_type="routing_decision",
            stage="routing",
            source="graph.supervisor_route",
            summary=f"路由已选择 {decision.agent_name}，模式 {decision.mode}，来源 {decision.route_source}",
            payload={
                "agent_name": decision.agent_name,
                "mode": decision.mode,
                "route_source": decision.route_source,
                "reason": decision.reason,
                "confidence": decision.confidence,
            },
            refs={},
        )
        return {
            "incident_state": incident_state,
            "routing_decision": decision,
            "task": task,
            "pending_node": "domain_agent",
        }

    async def domain_agent(self, state: TicketGraphState) -> Dict[str, Any]:
        request = state["request"]
        decision = state["routing_decision"]
        task = state["task"]
        agent = self.agents.get(decision.agent_name)
        if agent is None:
            raise ValueError(f"agent not configured: {decision.agent_name}")

        result = await agent.run(task)
        logger.info(
            "supervisor_router_resolved ticket_id=%s agent=%s mode=%s confidence=%.2f",
            request.ticket_id,
            decision.agent_name,
            decision.mode,
            decision.confidence,
        )
        logger.info(
            "subagent_completed ticket_id=%s agent=%s domain=%s",
            request.ticket_id,
            result.agent_name,
            result.domain,
        )
        incident_state = incident_state_from_legacy(
            request,
            routing=decision,
            agent_result=result,
        )
        return {
            "incident_state": incident_state,
            "agent_result": result,
            "pending_node": "approval_gate",
        }

    async def clarification_gate(self, state: TicketGraphState) -> Dict[str, Any]:
        incident_state = state["incident_state"]
        request = state["request"]
        transition_notes = list(state.get("transition_notes") or [])
        service = (request.service or incident_state.service or "").strip()
        if service:
            transition_notes.append("clarification gate passed without blocking interrupt")
            return {
                "incident_state": incident_state,
                "transition_notes": transition_notes,
                "pending_node": "approval_gate",
            }

        interrupt_record = self.interrupt_store.create_clarification_interrupt(
            session_id=str(state.get("session_id") or state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
            ticket_id=incident_state.ticket_id,
            reason="缺少明确的 service 信息，无法继续诊断",
            question="请补充需要排查的 service 名称。",
            expected_input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
            metadata={
                "thread_id": str(state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
                "field_name": "service",
                "resume_kind": "clarification",
            },
        )
        incident_state.status = "awaiting_clarification"
        incident_state.open_questions = ["请补充需要排查的 service 名称。"]
        incident_state.metadata["clarification_interrupt"] = interrupt_record
        self._append_process_entry(
            session_id=str(state.get("session_id") or state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
            thread_id=str(state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
            ticket_id=incident_state.ticket_id,
            event_type="clarification_created",
            stage="awaiting_clarification",
            source="graph.clarification_gate",
            summary="由于缺少 service 信息，已创建 clarification interrupt",
            payload={
                "reason": "缺少明确的 service 信息，无法继续诊断",
                "question": "请补充需要排查的 service 名称。",
                "field_name": "service",
            },
            refs={
                "interrupt_id": interrupt_record.get("interrupt_id"),
            },
        )
        self._append_system_event(
            session_id=str(state.get("session_id") or state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
            thread_id=str(state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
            ticket_id=incident_state.ticket_id,
            event_type="interrupt.created",
            payload={
                "interrupt_id": interrupt_record.get("interrupt_id"),
                "interrupt_type": "clarification",
                "question": interrupt_record.get("question"),
            },
            metadata={"source": "graph.clarification_gate"},
        )
        transition_notes.append("clarification gate materialized a persisted clarification interrupt")
        return {
            "incident_state": incident_state,
            "transition_notes": transition_notes,
            "approval_request": None,
            "response": {
                "ticket_id": request.ticket_id,
                "status": "awaiting_clarification",
                "message": "缺少关键信息，请先补充 service 名称后再继续。",
                "diagnosis": {
                    "summary": "当前诊断缺少 service 信息，已发起 clarification interrupt。",
                    "incident_state": incident_state.model_dump(),
                    "graph": {"transition_notes": transition_notes},
                },
            },
            "pending_node": None,
        }

    async def approval_gate(self, state: TicketGraphState) -> Dict[str, Any]:
        incident_state = state["incident_state"]
        routing_decision = state["routing_decision"]
        gate_input = self._build_approval_gate_input(incident_state)
        gate_result = self.approval_coordinator.build_gate_result(gate_input)
        next_incident_state = apply_approval_gate_result_to_state(incident_state, gate_result)

        approval_request = None
        transition_notes = list(state.get("transition_notes") or [])
        transition_notes.append("approval gate is routed through ApprovalCoordinator")

        if gate_result.approval_request is not None:
            snapshot = next_incident_state.model_dump()
            domain_request = gate_result.approval_request.model_copy(
                update={
                    "context": {
                        **dict(gate_result.approval_request.context),
                        "incident_state": snapshot,
                    }
                }
            )
            saved_request = self.approval_store.create_request(domain_request)
            approval_request = approval_request_to_legacy_payload(saved_request)
            interrupt_record = self.interrupt_store.create_approval_interrupt(
                session_id=str(state.get("session_id") or state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
                ticket_id=incident_state.ticket_id,
                reason=str(approval_request.get("reason") or approval_request.get("action") or "需要审批后继续执行"),
                question="是否批准执行该高风险动作？",
                expected_input_schema={
                    "type": "object",
                    "properties": {
                        "approved": {"type": "boolean"},
                        "approver_id": {"type": "string"},
                        "comment": {"type": "string"},
                    },
                    "required": ["approved", "approver_id"],
                },
                metadata={
                    "approval_id": approval_request.get("approval_id"),
                    "thread_id": approval_request.get("thread_id"),
                },
            )
            approval_request["interrupt_id"] = interrupt_record["interrupt_id"]
            next_incident_state.metadata["approval_request"] = approval_request
            self._append_process_entry(
                session_id=str(state.get("session_id") or state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
                thread_id=str(state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
                ticket_id=incident_state.ticket_id,
                event_type="approval_requested",
                stage="awaiting_approval",
                source="graph.approval_gate",
                summary=f"高风险动作 {approval_request.get('action') or 'unknown'} 已进入审批",
                payload={
                    "action": approval_request.get("action"),
                    "risk": approval_request.get("risk"),
                    "reason": approval_request.get("reason"),
                },
                refs={
                    "approval_id": approval_request.get("approval_id"),
                    "interrupt_id": interrupt_record.get("interrupt_id"),
                },
            )
            self._append_system_event(
                session_id=str(state.get("session_id") or state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
                thread_id=str(state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
                ticket_id=incident_state.ticket_id,
                event_type="interrupt.created",
                payload={
                    "interrupt_id": interrupt_record.get("interrupt_id"),
                    "interrupt_type": "approval",
                    "approval_id": approval_request.get("approval_id"),
                },
                metadata={"source": "graph.approval_gate"},
            )
            self._append_system_event(
                session_id=str(state.get("session_id") or state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
                thread_id=str(state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
                ticket_id=incident_state.ticket_id,
                event_type="approval.pending",
                payload={
                    "approval_id": approval_request.get("approval_id"),
                    "action": approval_request.get("action"),
                    "risk": approval_request.get("risk"),
                },
                metadata={"source": "graph.approval_gate", "interrupt_id": interrupt_record.get("interrupt_id")},
            )
            transition_notes.append("approval request is persisted through ApprovalStore facade backed by ApprovalStoreV2")
            transition_notes.append("approval wait is materialized as a persisted approval interrupt")
        else:
            transition_notes.append("approval gate completed without pending approval request")

        next_incident_state.metadata["graph"] = {
            "approval_gate": "approval_coordinator",
            "routing_agent": routing_decision.agent_name,
        }

        pending_node = "approval_decision" if approval_request is not None else "finalize"
        return {
            "incident_state": next_incident_state,
            "approval_request": approval_request,
            "transition_notes": transition_notes,
            "pending_node": pending_node,
        }

    async def finalize(self, state: TicketGraphState) -> Dict[str, Any]:
        request = state["request"]
        result = state["agent_result"]
        decision = state["routing_decision"]
        approval_request = state.get("approval_request")
        incident_state = state.get("incident_state")
        transition_notes = list(state.get("transition_notes") or [])
        diagnosis = self._render_diagnosis(
            result,
            decision,
            incident_state=incident_state,
            transition_notes=transition_notes,
        )

        if approval_request is not None:
            response = {
                "ticket_id": request.ticket_id,
                "status": "awaiting_approval",
                "message": "检测到高风险动作，需审批后才能继续执行。",
                "approval_request": approval_request,
                "diagnosis": diagnosis,
            }
            if incident_state is not None:
                incident_state.final_message = response["message"]
                incident_state.metadata["approval_request"] = approval_request
        else:
            response = {
                "ticket_id": request.ticket_id,
                "status": "completed",
                "message": self._render_response(result),
                "diagnosis": diagnosis,
            }
            if incident_state is not None:
                incident_state.final_summary = result.summary
                incident_state.final_message = response["message"]

        return {
            "incident_state": incident_state,
            "response": response,
            "pending_node": None,
        }

    async def ingest_approval_decision(self, state: ApprovalGraphState) -> Dict[str, Any]:
        approval = state["approval_record"]
        approval_request_model = self._require_approval_request_domain(state)
        incident_state, restore_note = self._restore_incident_state_for_resume(
            approval,
            approval_request_model,
            thread_id=str(state.get("thread_id") or approval_request_model.thread_id),
        )
        transition_notes = list(state.get("transition_notes") or [])
        transition_notes.append(restore_note)
        logger.info("graph.resume approval_id=%s", approval["approval_id"])
        return {
            "approval_request_domain": approval_request_model.model_dump(),
            "incident_state": incident_state,
            "transition_notes": transition_notes,
            "pending_node": "approval_decision",
        }

    async def approval_decision(self, state: ApprovalGraphState) -> Dict[str, Any]:
        approval = state["approval_record"]
        request = state["approval_decision_request"]
        approval_request = self._require_approval_request_domain(state)
        incident_state = state["incident_state"]
        approval_request_id = approval_request.approval_id
        decision_record = legacy_decision_to_record(request, approval_id=approval_request_id)
        next_incident_state = apply_approval_resume_result_to_state(
            incident_state,
            approval_request,
            decision_record,
        )

        if not request.approved:
            return {
                "approval_request_domain": approval_request.model_dump(),
                "approval_decision_record": decision_record.model_dump(),
                "incident_state": next_incident_state,
                "approval_result": self._build_rejection_response(approval, approval_request),
                "resume_action": "finalize",
                "pending_node": "finalize_approval_decision",
            }

        transition_notes = list(state.get("transition_notes") or [])
        proposals = approval_request.proposals
        if len(proposals) > 1:
            transition_notes.append(
                "multiple proposals were approved; transitional executor will execute only the primary proposal and mark the rest as skipped"
            )
        return {
            "approval_request_domain": approval_request.model_dump(),
            "approval_decision_record": decision_record.model_dump(),
            "incident_state": next_incident_state,
            "transition_notes": transition_notes,
            "resume_action": "execute_approved_action",
            "pending_node": "execute_approved_action_transition",
        }

    async def execute_approved_action_transition(self, state: ApprovalGraphState) -> Dict[str, Any]:
        approval = state["approval_record"]
        request = state["approval_decision_request"]
        approval_request_domain = dict(state.get("approval_request_domain") or {})
        incident_state = state["incident_state"]
        proposals = list(approval_request_domain.get("proposals") or [])
        primary_proposal = proposals[0] if proposals else {}

        execution_plan = None
        created_steps: list[dict[str, Any]] = []
        execution_binding: Dict[str, Any] | None = None
        session_id = str(state.get("session_id") or approval_request_domain.get("thread_id") or approval.get("thread_id") or approval.get("ticket_id") or "")
        thread_id = str(approval_request_domain.get("thread_id") or approval.get("thread_id") or session_id)
        ticket_id = str(approval_request_domain.get("ticket_id") or approval.get("ticket_id") or session_id)
        approval_id = approval_request_domain.get("approval_id") or approval.get("approval_id")
        primary_action = str(primary_proposal.get("action") or approval.get("action") or "")
        primary_step = None

        if primary_proposal and self.execution_store is not None:
            execution_plan = self.execution_store.create_plan(
                {
                    "session_id": session_id,
                    "thread_id": thread_id,
                    "ticket_id": ticket_id,
                    "status": "running",
                    "steps": [],
                    "summary": f"执行已批准动作：{primary_action}",
                    "metadata": {
                        "approval_id": approval_id,
                        "proposal_count": len(proposals),
                        "source": "approval_resume",
                    },
                }
            )
            primary_step = self.execution_store.create_step(
                {
                    "plan_id": execution_plan["plan_id"],
                    "session_id": session_id,
                    "action": primary_action,
                    "tool_name": primary_action,
                    "params": dict(primary_proposal.get("params") or {}),
                    "status": "running",
                    "result_summary": "执行中",
                    "evidence": [],
                    "metadata": {
                        "approval_id": approval_id,
                        "proposal_id": primary_proposal.get("proposal_id"),
                        "executor": "execute_approved_action_transition",
                        "approval_snapshot": dict((execution_binding or {}).get("snapshot") or {}),
                    },
                    "started_at": utc_now(),
                }
            )
            created_steps.append(primary_step)
            self.execution_store.update_plan(
                execution_plan["plan_id"],
                steps=[step["step_id"] for step in created_steps],
                metadata={
                    **dict(execution_plan.get("metadata") or {}),
                    "primary_step_id": primary_step["step_id"],
                },
            )
            if self.checkpoint_store is not None:
                self.checkpoint_store.create(
                    {
                        "session_id": session_id,
                        "thread_id": thread_id,
                        "ticket_id": ticket_id,
                        "stage": "execution_started",
                        "next_action": "execute_primary_step",
                        "state_snapshot": incident_state.model_dump(),
                        "metadata": {
                            "plan_id": execution_plan["plan_id"],
                            "step_id": primary_step["step_id"],
                            "approval_id": approval_id,
                            "action": primary_action,
                        },
                    }
                )
            self._append_system_event(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=ticket_id,
                event_type="execution.started",
                payload={
                    "plan_id": execution_plan["plan_id"],
                    "step_id": primary_step["step_id"],
                    "action": primary_action,
                },
                metadata={"source": "graph.execute_approved_action_transition", "approval_id": approval_id},
            )

        transition_notes = list(state.get("transition_notes") or [])
        try:
            if primary_proposal:
                execution_binding = validate_execution_binding(primary_proposal, approval_request_domain)
                transition_notes.append("execution safety validation passed before external tool call")
            result = await self._execute_approved_action_transition(
                approval_request_domain,
                request,
                execution_binding=execution_binding,
            )
        except Exception as exc:
            transition_notes.append(
                "approved action execution failed before finalize; latest execution checkpoint can be used for recovery"
            )
            failure_summary = f"审批已通过，但执行失败：{exc}"
            if isinstance(exc, ExecutionSafetyError):
                transition_notes.append("execution safety validation blocked external tool execution")
            failure_result = execution_result_to_state(
                {
                    "action": primary_action,
                    "status": "failed",
                    "summary": failure_summary,
                    "payload": {"error": str(exc), "error_type": type(exc).__name__},
                    "evidence": [str(exc)],
                },
                action=primary_action,
                risk=primary_proposal.get("risk") or approval.get("risk"),
                metadata={
                    "approval_id": approval_id,
                    "proposal_id": primary_proposal.get("proposal_id"),
                    "executor": "execute_approved_action_transition",
                    "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                    "step_id": primary_step.get("step_id") if primary_step is not None else None,
                },
            )
            if primary_step is not None and self.execution_store is not None:
                self.execution_store.update_step(
                    primary_step["step_id"],
                    status="failed",
                    result_summary=failure_result.summary,
                    evidence=list(failure_result.evidence),
                    metadata={
                        **dict(primary_step.get("metadata") or {}),
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                    finished_at=utc_now(),
                )
            if execution_plan is not None and self.execution_store is not None:
                self.execution_store.update_plan(
                    execution_plan["plan_id"],
                    status="failed",
                    steps=[step["step_id"] for step in created_steps],
                    summary=failure_result.summary,
                    metadata={
                        **dict(execution_plan.get("metadata") or {}),
                        "failed_step_id": primary_step.get("step_id") if primary_step is not None else None,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
            if self.checkpoint_store is not None:
                self.checkpoint_store.create(
                    {
                        "session_id": session_id,
                        "thread_id": thread_id,
                        "ticket_id": ticket_id,
                        "stage": "execution_failed",
                        "next_action": "retry_execution_step",
                        "state_snapshot": incident_state.model_dump(),
                        "metadata": {
                            "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                            "step_id": primary_step.get("step_id") if primary_step is not None else None,
                            "approval_id": approval_id,
                            "action": primary_action,
                            "step_status": "failed",
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        },
                    }
                )
            self._append_system_event(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=ticket_id,
                event_type="execution.step_finished",
                payload={
                    "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                    "step_id": primary_step.get("step_id") if primary_step is not None else None,
                    "action": primary_action,
                    "status": "failed",
                },
                metadata={"source": "graph.execute_approved_action_transition", "approval_id": approval_id},
            )
            next_incident_state = apply_execution_results_to_state(incident_state, [failure_result.model_dump()])
            approval_result = {
                "ticket_id": ticket_id,
                "status": "failed",
                "message": failure_summary,
                "diagnosis": {
                    "approval": {
                        "approval_id": approval_id,
                        "action": primary_action,
                        "status": "approved",
                    },
                    "execution": {
                        "status": "failed",
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                },
            }
            diagnosis = dict(approval_result.get("diagnosis") or {})
            diagnosis["execution_limit"] = {
                "transitional_executor_mode": "single_primary_execution",
                "approved_proposal_count": len(proposals),
                "executed_proposal_count": 0,
                "skipped_proposal_count": max(len(proposals) - 1, 0),
                "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                "step_ids": [step.get("step_id") for step in created_steps],
                "recovery_action": "retry_execution_step",
            }
            approval_result["diagnosis"] = diagnosis
            return {
                "incident_state": next_incident_state,
                "approval_result": approval_result,
                "transition_notes": transition_notes,
                "pending_node": "finalize_approval_decision",
            }

        transition_notes.append(
            "approved action execution is still handled by the transitional graph node and should move to AI-4 executor later"
        )
        execution_results: list[dict[str, Any]] = []
        result_payload = dict(result)
        result_payload.setdefault("evidence", self._extract_execution_evidence(result_payload))
        primary_execution_state = execution_result_to_state(
            result_payload,
            action=primary_action,
            risk=primary_proposal.get("risk") or approval.get("risk"),
            metadata={
                "approval_id": approval_id,
                "proposal_id": primary_proposal.get("proposal_id"),
                "executor": "execute_approved_action_transition",
                "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                "step_id": primary_step.get("step_id") if primary_step is not None else None,
            },
        )
        execution_results.append(primary_execution_state.model_dump())
        if primary_step is not None and self.execution_store is not None:
            self.execution_store.update_step(
                primary_step["step_id"],
                status=primary_execution_state.status,
                result_summary=primary_execution_state.summary,
                evidence=list(primary_execution_state.evidence),
                metadata={
                    **dict(primary_step.get("metadata") or {}),
                    "payload": dict(primary_execution_state.payload),
                    "risk": primary_execution_state.risk,
                },
                finished_at=utc_now(),
            )

        for proposal in proposals[1:]:
            skipped_step = None
            if execution_plan is not None and self.execution_store is not None:
                skipped_step = self.execution_store.create_step(
                    {
                        "plan_id": execution_plan["plan_id"],
                        "session_id": session_id,
                        "action": str(proposal.get("action") or ""),
                        "tool_name": str(proposal.get("action") or ""),
                        "params": dict(proposal.get("params") or {}),
                        "status": "skipped",
                        "result_summary": "当前过渡执行节点仅执行首个已批准 proposal，其余已批准动作待正式执行器接管。",
                        "evidence": [],
                        "metadata": {
                            "approval_id": approval_id,
                            "proposal_id": proposal.get("proposal_id"),
                            "executor": "execute_approved_action_transition",
                            "skip_reason": "transitional_executor_single_proposal_limit",
                        },
                        "started_at": utc_now(),
                        "finished_at": utc_now(),
                    }
                )
                created_steps.append(skipped_step)
            skipped_state = execution_result_to_state(
                {
                    "action": proposal.get("action"),
                    "status": "skipped",
                    "summary": "当前过渡执行节点仅执行首个已批准 proposal，其余已批准动作待正式执行器接管。",
                    "payload": {},
                    "metadata": {
                        "skip_reason": "transitional_executor_single_proposal_limit",
                    },
                },
                action=proposal.get("action"),
                risk=proposal.get("risk"),
                metadata={
                    "approval_id": approval_id,
                    "proposal_id": proposal.get("proposal_id"),
                    "executor": "execute_approved_action_transition",
                    "skip_reason": "transitional_executor_single_proposal_limit",
                    "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                    "step_id": skipped_step.get("step_id") if skipped_step is not None else None,
                },
            )
            execution_results.append(skipped_state.model_dump())

        if execution_plan is not None and self.execution_store is not None:
            plan_status = "completed" if primary_execution_state.status != "failed" else "failed"
            next_action = "finalize_execution" if plan_status == "completed" else "retry_execution_step"
            self.execution_store.update_plan(
                execution_plan["plan_id"],
                status=plan_status,
                steps=[step["step_id"] for step in created_steps],
                summary=primary_execution_state.summary,
                metadata={
                    **dict(execution_plan.get("metadata") or {}),
                    "completed_step_count": len(created_steps),
                },
            )
            if self.checkpoint_store is not None:
                self.checkpoint_store.create(
                    {
                        "session_id": session_id,
                        "thread_id": thread_id,
                        "ticket_id": ticket_id,
                        "stage": "execution_step_finished",
                        "next_action": next_action,
                        "state_snapshot": incident_state.model_dump(),
                        "metadata": {
                            "plan_id": execution_plan["plan_id"],
                            "step_ids": [step["step_id"] for step in created_steps],
                            "approval_id": approval_id,
                            "action": primary_execution_state.action,
                            "step_status": primary_execution_state.status,
                        },
                    }
                )
            self._append_system_event(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=ticket_id,
                event_type="execution.step_finished",
                payload={
                    "plan_id": execution_plan["plan_id"],
                    "step_id": primary_step.get("step_id") if primary_step is not None else None,
                    "action": primary_execution_state.action,
                    "status": primary_execution_state.status,
                },
                metadata={"source": "graph.execute_approved_action_transition", "approval_id": approval_id},
            )

        next_incident_state = apply_execution_results_to_state(incident_state, execution_results)
        approval_result = dict(result)
        diagnosis = dict(approval_result.get("diagnosis") or {})
        diagnosis["execution_limit"] = {
            "transitional_executor_mode": "single_primary_execution",
            "approved_proposal_count": len(proposals),
            "executed_proposal_count": 1 if proposals else 0,
            "skipped_proposal_count": max(len(proposals) - 1, 0),
            "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
            "step_ids": [step.get("step_id") for step in created_steps],
            "recovery_action": "finalize_execution" if primary_execution_state.status != "failed" else "retry_execution_step",
        }
        approval_result["diagnosis"] = diagnosis

        return {
            "incident_state": next_incident_state,
            "approval_result": approval_result,
            "transition_notes": transition_notes,
            "pending_node": "finalize_approval_decision",
        }

    async def finalize_approval_decision(self, state: ApprovalGraphState) -> Dict[str, Any]:
        response = dict(state["approval_result"])
        transition_notes = list(state.get("transition_notes") or [])
        incident_state = state.get("incident_state")
        diagnosis = dict(response.get("diagnosis") or {})
        if incident_state is not None:
            diagnosis["incident_state"] = incident_state.model_dump()
        if transition_notes:
            diagnosis["graph"] = {
                "transition_notes": transition_notes,
            }
        response["diagnosis"] = diagnosis
        return {
            "incident_state": incident_state,
            "response": response,
            "pending_node": None,
        }

    @staticmethod
    def route_after_clarification_gate(state: TicketGraphState) -> str:
        pending_node = state.get("pending_node")
        if pending_node == "approval_gate":
            return "approval_gate"
        return "end"

    @staticmethod
    def route_after_approval_decision(state: ApprovalGraphState) -> str:
        return state.get("resume_action") or "finalize"

    @staticmethod
    def _extract_execution_evidence(result: Dict[str, Any]) -> list[str]:
        evidence: list[str] = []
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                    if text:
                        evidence.append(text)
        message = str(result.get("message") or "").strip()
        if message:
            evidence.append(message)
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            for key in ("job_id", "pipeline_url", "runbook", "status"):
                value = structured.get(key)
                if value:
                    evidence.append(f"{key}={value}")
        diagnosis = result.get("diagnosis")
        execution = dict(diagnosis.get("execution") or {}) if isinstance(diagnosis, dict) else {}
        for key in ("job_id", "pipeline_url", "runbook", "status"):
            value = execution.get(key)
            if value:
                evidence.append(f"{key}={value}")
        return evidence[:5]

    @staticmethod
    def _render_response(result: AgentResult) -> str:
        response_parts = [result.summary]
        if result.evidence:
            response_parts.append(f"关键证据：{'; '.join(result.evidence[:2])}")
        if result.open_questions:
            response_parts.append(f"待确认：{result.open_questions[0]}")
        return "；".join(response_parts)

    @staticmethod
    def _render_diagnosis(
        result: AgentResult,
        decision,
        *,
        incident_state=None,
        transition_notes: list[str] | None = None,
    ) -> Dict[str, object]:
        diagnosis = result.model_dump()
        diagnosis["routing"] = decision.model_dump()
        if incident_state is not None:
            diagnosis["incident_state"] = incident_state.model_dump()
        if transition_notes:
            diagnosis["graph"] = {
                "transition_notes": list(transition_notes),
            }
        return diagnosis

    def _build_approval_gate_input(self, incident_state: IncidentState):
        gate_input = build_approval_gate_input_from_state(incident_state)
        proposals = []
        for proposal in gate_input.proposals:
            params = dict(proposal.params)
            agent_name = proposal.agent
            mcp_servers = self.connection_manager.servers_for_agent(agent_name)
            if mcp_servers and not params.get("mcp_server"):
                params["mcp_server"] = mcp_servers[0]
            params.setdefault("agent_name", agent_name)
            params.setdefault("source_agent", agent_name)
            params.setdefault("orchestration_mode", "supervisor_graph")
            proposals.append(proposal.model_copy(update={"params": params}))
        return gate_input.model_copy(update={"proposals": proposals})

    def _restore_incident_state_for_resume(
        self,
        approval: Dict[str, Any],
        approval_request,
        *,
        thread_id: str,
    ) -> tuple[IncidentState, str]:
        if thread_id:
            session = self.session_store.get_by_thread_id(thread_id)
            if session is not None:
                last_checkpoint_id = session.get("last_checkpoint_id")
                if self.checkpoint_store is not None and last_checkpoint_id:
                    checkpoint = self.checkpoint_store.get(str(last_checkpoint_id))
                    if checkpoint is not None:
                        snapshot = checkpoint.get("state_snapshot")
                        if isinstance(snapshot, dict):
                            restored = IncidentState.model_validate(snapshot)
                            restored.metadata.setdefault("graph", {})
                            restored.metadata["graph"]["resume_restore_mode"] = "checkpoint"
                            return restored, "incident_state restored from latest session checkpoint"
                if self.checkpoint_store is not None:
                    checkpoint = self.checkpoint_store.get_latest(str(session.get("session_id") or ""))
                    if checkpoint is not None:
                        snapshot = checkpoint.get("state_snapshot")
                        if isinstance(snapshot, dict):
                            restored = IncidentState.model_validate(snapshot)
                            restored.metadata.setdefault("graph", {})
                            restored.metadata["graph"]["resume_restore_mode"] = "checkpoint"
                            return restored, "incident_state restored from latest checkpoint lookup"
                snapshot = session.get("incident_state")
                if isinstance(snapshot, dict):
                    restored = IncidentState.model_validate(snapshot)
                    restored.metadata.setdefault("graph", {})
                    restored.metadata["graph"]["resume_restore_mode"] = "session_snapshot"
                    return restored, "incident_state restored from session snapshot"

        approval_context = approval_request.context if isinstance(approval_request, ApprovalRequest) else approval_request.get("context", {})
        snapshot = dict(approval_context.get("incident_state") or {}) if isinstance(approval_context, dict) else {}
        if not snapshot:
            params = dict(approval.get("params") or {})
            snapshot = params.get("incident_state")
        if isinstance(snapshot, dict):
            restored = IncidentState.model_validate(snapshot)
            restored.metadata.setdefault("graph", {})
            restored.metadata["graph"]["resume_restore_mode"] = "approval_payload_snapshot"
            return restored, "incident_state restored from approval payload snapshot"

        params = dict(approval.get("params") or {})
        proposals = approval_request.get("proposals", []) if isinstance(approval_request, dict) else approval_request.proposals
        primary = proposals[0] if proposals else None
        service = ""
        if primary is not None:
            service = primary.resource or str(primary.params.get("service") or primary.params.get("target") or "")
        message = approval_request.summary or (primary.reason if primary is not None else "审批恢复") or "审批恢复"
        request = TicketRequest(
            ticket_id=approval_request.ticket_id,
            user_id=str(params.get("user_id") or params.get("initiator_id") or "system"),
            message=message,
            service=service or None,
            cluster=str(params.get("cluster") or "prod-shanghai-1"),
            namespace=str(params.get("namespace") or "default"),
            channel=str(params.get("channel") or "feishu"),
        )
        incident_state = build_initial_incident_state(request)
        incident_state.thread_id = approval_request.thread_id
        incident_state = apply_approval_gate_result_to_state(
            incident_state,
            {
                "approval_request": approval_request.model_dump(),
                "approved_actions": [],
                "rejected_proposals": [],
                "auto_approved_proposals": [],
                "policy_results": [],
            },
        )
        incident_state.metadata.setdefault("graph", {})
        incident_state.metadata["graph"]["resume_restore_mode"] = "minimal_from_approval_record"
        return incident_state, "incident_state reconstructed from approval record because no original snapshot was available"

    @staticmethod
    def _build_rejection_response(approval: Dict[str, Any], approval_request: Dict[str, Any] | ApprovalRequest | None = None) -> Dict[str, Any]:
        proposals = approval_request.get("proposals", []) if isinstance(approval_request, dict) else approval_request.proposals if approval_request is not None else []
        primary = proposals[0] if proposals else None
        action = primary.get("action", "") if isinstance(primary, dict) else getattr(primary, "action", "") or approval.get("action", "")
        return {
            "ticket_id": approval["ticket_id"],
            "status": "completed",
            "message": "审批未通过，未执行任何高风险动作。",
            "diagnosis": {
                "approval": {
                    "approval_id": approval["approval_id"],
                    "action": action,
                    "status": "rejected",
                }
            },
        }

    @staticmethod
    async def _execute_approved_action_transition(
        approval_request: Dict[str, Any],
        request: ApprovalDecisionRequest,
        *,
        execution_binding: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        proposals = list(approval_request.get("proposals") or [])
        primary_proposal = proposals[0] if proposals else {}
        params = dict(primary_proposal.get("params") or {})
        action = str(primary_proposal.get("action") or "")
        if not request.approved:
            return OrchestratorGraphNodes._build_rejection_response(approval_request, approval_request)

        validated_binding = execution_binding or validate_execution_binding(primary_proposal, approval_request)
        tool_params = dict(validated_binding.get("tool_params") or {})
        action = str(validated_binding.get("action") or action)
        mcp_server = validated_binding.get("mcp_server") or params.get("mcp_server")
        if not mcp_server:
            raise ValueError("approval params missing mcp_server")

        client = MCPClient(str(mcp_server))
        execution = await client.call_tool(str(action), tool_params)
        execution_payload = execution.get("structuredContent", {})
        summary = execution.get("content", [{}])[0].get("text", "高风险动作已执行。")
        response_status = "completed"
        if execution_payload.get("status") == "pending_approval":
            summary = "已向执行系统提交高风险动作，请继续跟踪执行状态。"
        elif execution_payload.get("status") == "failed":
            response_status = "failed"
            summary = execution_payload.get("error") or summary or "高风险动作执行失败。"
        return {
            "ticket_id": approval_request["ticket_id"],
            "status": response_status,
            "message": f"审批已通过；{summary}",
            "diagnosis": {
                "approval": {
                    "approval_id": approval_request["approval_id"],
                    "action": action,
                    "status": "approved",
                },
                "execution": execution_payload,
            },
        }
