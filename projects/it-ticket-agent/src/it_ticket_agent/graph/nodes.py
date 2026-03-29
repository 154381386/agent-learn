from __future__ import annotations

import logging
from typing import Any, Dict, Mapping

from ..agents.base import BaseDomainAgent
from ..approval import ApprovalCoordinator
from ..approval.adapters import (
    approval_request_to_legacy_payload,
    legacy_decision_to_record,
    legacy_payload_to_approval_request,
)
from ..approval_store import ApprovalStore
from ..mcp import MCPClient, MCPConnectionManager
from ..runtime.contracts import AgentResult
from ..runtime.supervisor import RuleBasedSupervisor
from ..schemas import ApprovalDecisionRequest, TicketRequest
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
        connection_manager: MCPConnectionManager,
        agents: Mapping[str, BaseDomainAgent],
        approval_coordinator: ApprovalCoordinator | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.approval_store = approval_store
        self.connection_manager = connection_manager
        self.agents = agents
        self.approval_coordinator = approval_coordinator or ApprovalCoordinator()

    async def ingest(self, state: TicketGraphState) -> Dict[str, Any]:
        request = state["request"]
        incident_state = state.get("incident_state") or build_initial_incident_state(request)
        logger.info("graph.ingest ticket_id=%s", request.ticket_id)
        return {
            "incident_state": incident_state,
            "pending_node": "supervisor_route",
        }

    async def supervisor_route(self, state: TicketGraphState) -> Dict[str, Any]:
        request = state["request"]
        decision = await self.supervisor.route(request)
        task = self.supervisor.build_task(request, decision)
        incident_state = state.get("incident_state") or build_initial_incident_state(request)
        incident_state.routing = decision.model_dump()
        incident_state.status = "routed"
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
            approval_request = approval_request_to_legacy_payload(gate_result.approval_request)
            approval_request.setdefault("params", {})
            snapshot = next_incident_state.model_dump()
            approval_request["params"]["incident_state"] = snapshot
            for proposal in approval_request["params"].get("proposals", []):
                if isinstance(proposal, dict):
                    proposal.setdefault("params", {})
                    proposal["params"]["incident_state"] = snapshot
            approval_request = self.approval_store.create(approval_request)
            next_incident_state.metadata["approval_request"] = approval_request
            transition_notes.append("approval request is persisted through ApprovalStore facade backed by ApprovalStoreV2")
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
        approval_request = legacy_payload_to_approval_request(approval)
        incident_state, restore_note = self._restore_incident_state_for_resume(approval, approval_request)
        transition_notes = list(state.get("transition_notes") or [])
        transition_notes.append(restore_note)
        logger.info("graph.resume approval_id=%s", approval["approval_id"])
        return {
            "approval_request_domain": approval_request if isinstance(approval_request, dict) else approval_request.model_dump(),
            "incident_state": incident_state,
            "transition_notes": transition_notes,
            "pending_node": "approval_decision",
        }

    async def approval_decision(self, state: ApprovalGraphState) -> Dict[str, Any]:
        approval = state["approval_record"]
        request = state["approval_decision_request"]
        approval_request = state.get("approval_request_domain") or legacy_payload_to_approval_request(approval).model_dump()
        incident_state = state["incident_state"]
        approval_request_id = approval_request["approval_id"] if isinstance(approval_request, dict) else approval_request.approval_id
        decision_record = legacy_decision_to_record(request, approval_id=approval_request_id)
        next_incident_state = apply_approval_resume_result_to_state(
            incident_state,
            approval_request,
            decision_record,
        )

        if not request.approved:
            return {
                "approval_request_domain": approval_request if isinstance(approval_request, dict) else approval_request.model_dump(),
                "approval_decision_record": decision_record.model_dump(),
                "incident_state": next_incident_state,
                "approval_result": self._build_rejection_response(approval),
                "resume_action": "finalize",
                "pending_node": "finalize_approval_decision",
            }

        transition_notes = list(state.get("transition_notes") or [])
        proposals = approval_request.get("proposals", []) if isinstance(approval_request, dict) else approval_request.proposals
        if len(proposals) > 1:
            transition_notes.append(
                "multiple proposals were approved; transitional executor will execute only the primary proposal and mark the rest as skipped"
            )
        return {
            "approval_request_domain": approval_request if isinstance(approval_request, dict) else approval_request.model_dump(),
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
        decision_record = dict(state.get("approval_decision_record") or {})
        proposals = list(approval_request_domain.get("proposals") or [])
        primary_proposal = proposals[0] if proposals else {}

        result = await self._execute_approved_action_transition(approval, request)
        transition_notes = list(state.get("transition_notes") or [])
        transition_notes.append(
            "approved action execution is still handled by the transitional graph node and should move to AI-4 executor later"
        )

        execution_results: list[dict[str, Any]] = []
        primary_execution_state = execution_result_to_state(
            result,
            action=approval.get("action"),
            risk=approval.get("risk"),
            metadata={
                "approval_id": approval.get("approval_id"),
                "proposal_id": primary_proposal.get("proposal_id"),
                "executor": "execute_approved_action_transition",
            },
        )
        execution_results.append(primary_execution_state.model_dump())

        for proposal in proposals[1:]:
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
                    "approval_id": approval.get("approval_id"),
                    "proposal_id": proposal.get("proposal_id"),
                    "executor": "execute_approved_action_transition",
                    "skip_reason": "transitional_executor_single_proposal_limit",
                },
            )
            execution_results.append(skipped_state.model_dump())

        next_incident_state = apply_execution_results_to_state(incident_state, execution_results)

        approval_result = dict(result)
        diagnosis = dict(approval_result.get("diagnosis") or {})
        diagnosis["execution_limit"] = {
            "transitional_executor_mode": "single_primary_execution",
            "approved_proposal_count": len(proposals),
            "executed_proposal_count": 1 if proposals else 0,
            "skipped_proposal_count": max(len(proposals) - 1, 0),
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
    def route_after_approval_decision(state: ApprovalGraphState) -> str:
        return state.get("resume_action") or "finalize"

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

    def _restore_incident_state_for_resume(self, approval: Dict[str, Any], approval_request) -> tuple[IncidentState, str]:
        params = dict(approval.get("params") or {})
        snapshot = params.get("incident_state")
        if isinstance(snapshot, dict):
            restored = IncidentState.model_validate(snapshot)
            restored.metadata.setdefault("graph", {})
            restored.metadata["graph"]["resume_restore_mode"] = "approval_payload_snapshot"
            return restored, "incident_state restored from approval payload snapshot"

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
    def _build_rejection_response(approval: Dict[str, Any]) -> Dict[str, Any]:
        action = approval.get("action", "")
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
        approval: Dict[str, Any],
        request: ApprovalDecisionRequest,
    ) -> Dict[str, Any]:
        params = approval.get("params", {})
        action = approval.get("action", "")
        if not request.approved:
            return OrchestratorGraphNodes._build_rejection_response(approval)

        tool_params = {
            key: value
            for key, value in params.items()
            if key not in {"orchestration_mode", "mcp_server", "agent_name", "source_agent", "proposal_count", "proposals", "incident_state"}
        }
        mcp_server = params.get("mcp_server")
        if not mcp_server:
            raise ValueError("approval params missing mcp_server")

        client = MCPClient(str(mcp_server))
        execution = await client.call_tool(str(action), tool_params)
        execution_payload = execution.get("structuredContent", {})
        summary = execution.get("content", [{}])[0].get("text", "高风险动作已执行。")
        if execution_payload.get("status") == "pending_approval":
            summary = "已向执行系统提交高风险动作，请继续跟踪执行状态。"
        return {
            "ticket_id": approval["ticket_id"],
            "status": "completed",
            "message": f"审批已通过；{summary}",
            "diagnosis": {
                "approval": {
                    "approval_id": approval["approval_id"],
                    "action": action,
                    "status": "approved",
                },
                "execution": execution_payload,
            },
        }
