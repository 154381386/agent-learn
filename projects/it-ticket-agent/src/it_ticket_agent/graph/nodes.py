from __future__ import annotations

import logging
from uuid import uuid5, NAMESPACE_URL
from typing import Any, Dict

from ..approval import ApprovalCoordinator
from ..approval.adapters import (
    approval_request_to_legacy_payload,
    legacy_decision_to_record,
)
from ..case_retrieval import CaseRetriever, infer_failure_mode
from ..approval.models import ApprovalRequest
from ..approval_store import ApprovalStore
from ..interrupt_store import InterruptStore
from ..checkpoint_store import CheckpointStore
from ..execution import default_compensation_policy, default_retry_policy, retry_state_for_attempt
from ..execution_store import ExecutionStore
from ..execution.security import ExecutionSafetyError, validate_execution_binding
from ..memory_store import IncidentCaseStore, ProcessMemoryStore
from ..mcp import MCPClient, MCPConnectionManager
from ..observability import get_observability
from ..orchestration import RetrievalPlanner
from ..playbook_retrieval import PlaybookRetriever
from ..knowledge import KnowledgeService
from ..runtime.contracts import SmartRouterDecision
from ..runtime.smart_router import SmartRouter
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
from ..state.models import (
    ApprovalProposal,
    ContextSnapshot,
    Hypothesis,
    RAGContextBundle,
    RankedResult,
    SimilarIncidentCase,
    VerificationResult,
)
from ..state.transformers import build_initial_incident_state
from ..tools.runtime import LocalToolRuntime
from .state import ApprovalGraphState, TicketGraphState


logger = logging.getLogger(__name__)

TOOL_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "k8s": ("pod", "k8s", "oom", "container", "重启", "副本", "资源"),
    "cicd": ("deploy", "release", "pipeline", "rollback", "发布", "变更", "构建"),
    "network": ("dns", "ingress", "gateway", "vpc", "timeout", "502", "网络"),
    "db": ("mysql", "postgres", "数据库", "slow query", "deadlock", "连接池"),
    "monitor": ("alert", "slo", "burn rate", "日志", "监控", "告警"),
    "sde": ("quota", "bootstrap", "provision", "资源开通", "配额", "容量"),
    "finops": ("cost", "budget", "账单", "费用", "降本"),
}


class OrchestratorGraphNodes:
    def __init__(
        self,
        approval_store: ApprovalStore,
        session_store: SessionStore,
        interrupt_store: InterruptStore,
        process_memory_store: ProcessMemoryStore,
        incident_case_store: IncidentCaseStore | None,
        connection_manager: MCPConnectionManager,
        approval_coordinator: ApprovalCoordinator | None = None,
        execution_store: ExecutionStore | None = None,
        system_event_store: SystemEventStore | None = None,
        smart_router: SmartRouter | None = None,
        case_retriever: CaseRetriever | None = None,
        playbook_retriever: PlaybookRetriever | None = None,
        knowledge_service: KnowledgeService | None = None,
        retrieval_planner: RetrievalPlanner | None = None,
    ) -> None:
        self.approval_store = approval_store
        self.session_store = session_store
        self.interrupt_store = interrupt_store
        self.process_memory_store = process_memory_store
        self.incident_case_store = incident_case_store
        self.connection_manager = connection_manager
        self.approval_coordinator = approval_coordinator or ApprovalCoordinator()
        self.smart_router_impl = smart_router
        self.case_retriever = case_retriever
        self.playbook_retriever = playbook_retriever
        self.knowledge_service = knowledge_service
        self.retrieval_planner = retrieval_planner
        self.checkpoint_store = CheckpointStore(getattr(session_store, 'db_path', '')) if getattr(session_store, 'db_path', '') else None
        self.execution_store = execution_store or (ExecutionStore(getattr(session_store, 'db_path', '')) if getattr(session_store, 'db_path', '') else None)
        self.system_event_store = system_event_store or (SystemEventStore(getattr(session_store, 'db_path', '')) if getattr(session_store, 'db_path', '') else None)

    @staticmethod
    def _infer_tool_domains_from_haystack(haystack: str, *, has_service: bool, incremental: list[str]) -> list[str]:
        matched = [
            category
            for category, keywords in TOOL_DOMAIN_KEYWORDS.items()
            if any(keyword.lower() in haystack for keyword in keywords)
        ]
        if matched:
            if has_service:
                matched = sorted(set(matched) | {"monitor", "k8s"})
            return sorted(set(matched) | set(incremental or []))
        base = ["k8s", "cicd", "monitor"] if has_service else ["monitor", "cicd"]
        return sorted(set(base) | set(incremental or []))

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
            "pending_node": "smart_router",
        }

    async def smart_router(self, state: TicketGraphState) -> Dict[str, Any]:
        request = state["request"]
        incident_state = state.get("incident_state") or build_initial_incident_state(request)
        if self.smart_router_impl is None:
            raise ValueError("smart router is not configured")
        observability = get_observability()
        with observability.start_span(
            name="graph.smart_router",
            as_type="span",
            input={"ticket_id": request.ticket_id, "message": request.message},
            metadata={"node": "smart_router"},
        ) as span:
            decision = self.smart_router_impl.route(
                request,
                rag_context=incident_state.rag_context,
            )
            incident_state.routing = decision.model_dump()
            incident_state.status = "routed"
            self._append_process_entry(
                session_id=str(state.get("session_id") or state.get("thread_id") or request.ticket_id),
                thread_id=str(state.get("thread_id") or request.ticket_id),
                ticket_id=request.ticket_id,
                event_type="routing_decision",
                stage="routing",
                source="graph.smart_router",
                summary=f"Smart Router 已选择 {decision.intent}，来源 {decision.route_source}",
                payload=decision.model_dump(),
                refs={},
            )
            self._append_system_event(
                session_id=str(state.get("session_id") or state.get("thread_id") or request.ticket_id),
                thread_id=str(state.get("thread_id") or request.ticket_id),
                ticket_id=request.ticket_id,
                event_type="routing.decided",
                payload=decision.model_dump(),
                metadata={"source": "graph.smart_router"},
            )
            span.update(output=decision.model_dump())
            return {
                "incident_state": incident_state,
                "route_decision": decision,
                "pending_node": decision.intent,
            }

    def route_after_smart_router(self, state: TicketGraphState) -> str:
        decision = state["route_decision"]
        return decision.intent

    async def rag_direct_answer(self, state: TicketGraphState) -> Dict[str, Any]:
        request = state["request"]
        incident_state = state["incident_state"]
        decision = state["route_decision"]
        if self.smart_router_impl is None:
            raise ValueError("smart router is not configured")
        answer = await self.smart_router_impl.generate_direct_answer(
            request,
            rag_context=incident_state.rag_context,
        )
        citations = list(incident_state.rag_context.citations if incident_state.rag_context is not None else [])
        incident_state.status = "completed"
        incident_state.final_summary = "RAG FAQ fast path 已直接回答。"
        incident_state.final_message = answer
        transition_notes = ["smart router routed request to direct_answer", "rag direct answer completed without entering diagnosis graph"]
        self._append_process_entry(
            session_id=str(state.get("session_id") or state.get("thread_id") or request.ticket_id),
            thread_id=str(state.get("thread_id") or request.ticket_id),
            ticket_id=request.ticket_id,
            event_type="run_summary",
            stage="finalize",
            source="graph.rag_direct_answer",
            summary="FAQ / 知识咨询已通过 RAG fast path 直接回答",
            payload={"citations": citations, "route_decision": decision.model_dump()},
            refs={},
        )
        self._append_system_event(
            session_id=str(state.get("session_id") or state.get("thread_id") or request.ticket_id),
            thread_id=str(state.get("thread_id") or request.ticket_id),
            ticket_id=request.ticket_id,
            event_type="direct_answer.completed",
            payload={"citations": citations},
            metadata={"source": "graph.rag_direct_answer"},
        )
        return {
            "incident_state": incident_state,
            "response": {
                "ticket_id": request.ticket_id,
                "status": "completed",
                "message": answer,
                "diagnosis": {
                    "summary": incident_state.final_summary,
                    "conclusion": answer,
                    "routing": decision.model_dump(),
                    "sources": citations,
                    "incident_state": incident_state.model_dump(),
                    "graph": {"transition_notes": transition_notes},
                },
            },
            "pending_node": None,
        }

    async def context_collector(self, state: TicketGraphState) -> Dict[str, Any]:
        request = state["request"]
        incident_state = state["incident_state"]
        observability = get_observability()
        with observability.start_span(
            name="graph.context_collector",
            as_type="span",
            input={"ticket_id": request.ticket_id, "service": request.service, "message": request.message},
            metadata={"node": "context_collector"},
        ) as span:
            service = str(request.service or incident_state.service or "")
            cluster = str(request.cluster or incident_state.cluster or "")
            namespace = str(request.namespace or incident_state.namespace or "")
            message = str(request.message or "")
            session_id = str(state.get("session_id") or "")
            playbook_lookup = await self._load_diagnosis_playbooks(
                service=service,
                cluster=cluster,
                namespace=namespace,
                environment=str(getattr(request, "environment", "") or incident_state.environment or ""),
                message=message,
            )
            diagnosis_playbooks = list(playbook_lookup.get("diagnosis_playbooks") or [])
            playbook_recall = dict(playbook_lookup.get("playbook_recall") or {})
            case_recall_lookup = await self._load_similar_cases(
                service=service,
                cluster=cluster,
                namespace=namespace,
                message=message,
                session_id=session_id,
                diagnosis_playbooks=diagnosis_playbooks,
            )
            similar_cases = list(case_recall_lookup.get("similar_cases") or [])
            case_recall = dict(case_recall_lookup.get("case_recall") or {})
            matched_categories = self._match_tool_domains(
                request=request,
                incident_state=incident_state,
                similar_cases=similar_cases,
            )
            retrieval_expansion = await self._expand_context_retrieval(
                request=request,
                incident_state=incident_state,
                similar_cases=similar_cases,
                matched_categories=matched_categories,
                session_id=session_id,
                defer_case_lookup=bool(case_recall.get("deferred_by_playbook")),
            )
            if retrieval_expansion["added_rag_context"] is not None:
                incident_state.rag_context = retrieval_expansion["added_rag_context"]
            similar_cases = retrieval_expansion["similar_cases"]
            available_tools: list[dict[str, Any]] = []
            snapshot = ContextSnapshot(
                request=request.model_dump(),
                rag_context=self._normalize_rag_context(incident_state.rag_context),
                similar_cases=similar_cases,
                case_recall=case_recall,
                diagnosis_playbooks=diagnosis_playbooks,
                playbook_recall=playbook_recall,
                live_signals={},
                context_quality=self._score_context_quality(
                    request=request,
                    rag_context=incident_state.rag_context,
                    similar_cases=similar_cases,
                ),
                available_tools=available_tools,
                matched_tool_domains=matched_categories,
                retrieval_expansion=retrieval_expansion["expansion"],
            )
            incident_state.context_snapshot = snapshot
            incident_state.metadata["context_snapshot"] = snapshot.model_dump()
            incident_state.status = "context_collected"
            self._append_process_entry(
                session_id=str(state.get("session_id") or state.get("thread_id") or request.ticket_id),
                thread_id=str(state.get("thread_id") or request.ticket_id),
                ticket_id=request.ticket_id,
                event_type="run_summary",
                stage="context_collection",
                source="graph.context_collector",
                summary=f"已完成上下文采集，匹配到 {len(matched_categories)} 个工具域",
                payload={
                    "matched_tool_domains": matched_categories,
                    "available_tool_names": [],
                    "similar_case_count": len(similar_cases),
                    "playbook_count": len(diagnosis_playbooks),
                    "playbook_ids": [item.playbook_id for item in diagnosis_playbooks],
                    "case_recall_sources": [item.recall_source for item in similar_cases],
                    "case_prefetch_enabled": bool(case_recall.get("auto_prefetch_enabled")),
                    "case_prefetch_reason": str(case_recall.get("prefetch_reason") or ""),
                    "context_quality": snapshot.context_quality,
                    "retrieval_subquery_count": len(snapshot.retrieval_expansion.subqueries),
                    "added_rag_hits": snapshot.retrieval_expansion.added_rag_hits,
                    "added_case_hits": snapshot.retrieval_expansion.added_case_hits,
                },
                refs={},
            )
            self._append_system_event(
                session_id=str(state.get("session_id") or state.get("thread_id") or request.ticket_id),
                thread_id=str(state.get("thread_id") or request.ticket_id),
                ticket_id=request.ticket_id,
                event_type="context.collected",
                payload={
                    "matched_tool_domains": matched_categories,
                    "available_tool_names": [],
                    "similar_case_count": len(similar_cases),
                    "playbook_count": len(diagnosis_playbooks),
                    "playbook_ids": [item.playbook_id for item in diagnosis_playbooks],
                    "case_recall_sources": [item.recall_source for item in similar_cases],
                    "case_prefetch_enabled": bool(case_recall.get("auto_prefetch_enabled")),
                    "case_prefetch_reason": str(case_recall.get("prefetch_reason") or ""),
                    "context_quality": snapshot.context_quality,
                    "retrieval_subquery_count": len(snapshot.retrieval_expansion.subqueries),
                    "added_rag_hits": snapshot.retrieval_expansion.added_rag_hits,
                    "added_case_hits": snapshot.retrieval_expansion.added_case_hits,
                },
                metadata={"source": "graph.context_collector"},
            )
            span.update(
                output={
                    "matched_tool_domains": matched_categories,
                    "available_tool_count": len(available_tools),
                    "similar_case_count": len(similar_cases),
                    "playbook_count": len(diagnosis_playbooks),
                    "retrieval_subquery_count": len(snapshot.retrieval_expansion.subqueries),
                }
            )
            return {
                "incident_state": incident_state,
                "context_snapshot": snapshot,
                "pending_node": "hypothesis_generator",
            }

    async def hypothesis_generator(self, state: TicketGraphState) -> Dict[str, Any]:
        raise RuntimeError("legacy hypothesis_generator 已移除；请使用 react_tool_first 诊断链路。")

    async def parallel_verification(self, state: TicketGraphState) -> Dict[str, Any]:
        raise RuntimeError("legacy parallel_verification 已移除；请使用 react_tool_first 诊断链路。")

    async def ranker(self, state: TicketGraphState) -> Dict[str, Any]:
        raise RuntimeError("legacy ranker 已移除；请使用 react_tool_first 诊断链路。")

    async def hypothesis_graph(self, state: TicketGraphState) -> Dict[str, Any]:
        raise RuntimeError("legacy hypothesis_graph 已移除；请使用 react_tool_first 诊断链路。")

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

    def _create_feedback_interrupt(
        self,
        *,
        incident_state: IncidentState,
        session_id: str,
        thread_id: str,
        ticket_id: str,
    ) -> dict[str, Any] | None:
        if incident_state.ranked_result is None or incident_state.ranked_result.primary is None:
            return None
        feedback_policy = self._feedback_reopen_policy(incident_state)
        if not feedback_policy["can_reject_reopen"]:
            return None
        existing = incident_state.metadata.get("feedback_interrupt") if isinstance(incident_state.metadata, dict) else None
        if isinstance(existing, dict) and existing.get("interrupt_id"):
            return existing
        interrupt = self.interrupt_store.create_feedback_interrupt(
            session_id=session_id,
            ticket_id=ticket_id,
            reason="诊断已完成，已给出建议动作或审批结果，请确认是否接受。",
            question="请确认是否接受本次诊断结论与建议动作；如果不接受，可拒绝并重新分析。",
            expected_input_schema={
                "type": "object",
                "properties": {
                    "human_verified": {"type": "boolean"},
                    "actual_root_cause_hypothesis": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "required": ["human_verified"],
            },
            metadata={
                "thread_id": thread_id,
                "selected_hypothesis_id": incident_state.ranked_result.primary.hypothesis_id,
                "ranked_result": incident_state.ranked_result.model_dump(),
                **feedback_policy,
            },
        )
        incident_state.metadata["feedback_interrupt"] = interrupt
        self._append_process_entry(
            session_id=session_id,
            thread_id=thread_id,
            ticket_id=ticket_id,
            event_type="manual_intervention",
            stage="feedback",
            source="graph.feedback_gate",
            summary="诊断已完成，已创建 feedback interrupt 等待人工确认。",
            payload={"interrupt_id": interrupt.get("interrupt_id"), "selected_hypothesis_id": interrupt.get("metadata", {}).get("selected_hypothesis_id")},
            refs={"interrupt_id": interrupt.get("interrupt_id")},
        )
        self._append_system_event(
            session_id=session_id,
            thread_id=thread_id,
            ticket_id=ticket_id,
            event_type="feedback.requested",
            payload={"interrupt_id": interrupt.get("interrupt_id")},
            metadata={"source": "graph.feedback_gate"},
        )
        return interrupt

    @staticmethod
    def _feedback_reopen_policy(incident_state: IncidentState) -> dict[str, Any]:
        primary = incident_state.ranked_result.primary if incident_state.ranked_result is not None else None
        recommended_action = str(primary.recommended_action if primary is not None else "").strip()
        proposal_action = (
            str(incident_state.approval_proposals[0].action or "").strip()
            if incident_state.approval_proposals
            else ""
        )
        approved_action = (
            str(incident_state.approved_actions[0].action or "").strip()
            if incident_state.approved_actions
            else ""
        )
        approval_request = (
            dict(incident_state.metadata.get("approval_request") or {})
            if isinstance(incident_state.metadata, dict)
            else {}
        )
        approval_action = str(approval_request.get("action") or "").strip()
        action_name = recommended_action or proposal_action or approved_action or approval_action
        approval_present = bool(proposal_action or approved_action or approval_action)
        action_source = ""
        if recommended_action:
            action_source = "recommended_action"
        elif proposal_action:
            action_source = "approval_proposal"
        elif approved_action:
            action_source = "approved_action"
        elif approval_action:
            action_source = "approval_request"
        return {
            "can_reject_reopen": bool(action_name),
            "action_name": action_name,
            "action_source": action_source,
            "approval_present": approval_present,
        }

    @staticmethod
    def _normalize_rag_context(rag_context: RAGContextBundle | dict[str, Any] | None) -> RAGContextBundle:
        if isinstance(rag_context, RAGContextBundle):
            return rag_context
        if isinstance(rag_context, dict):
            return RAGContextBundle.model_validate(rag_context)
        return RAGContextBundle()


    async def _load_diagnosis_playbooks(
        self,
        *,
        service: str,
        cluster: str,
        namespace: str,
        environment: str,
        message: str,
    ) -> dict[str, Any]:
        if self.playbook_retriever is None:
            return {
                "diagnosis_playbooks": [],
                "playbook_recall": {
                    "enabled": False,
                    "status": "skipped",
                    "reason": "missing_playbook_retriever",
                    "hit_count": 0,
                },
            }
        try:
            playbooks = await self.playbook_retriever.recall(
                service=service,
                cluster=cluster,
                namespace=namespace,
                environment=environment,
                message=message,
                limit=2,
            )
            recall_metadata = dict(getattr(self.playbook_retriever, "last_recall_metadata", {}) or {})
        except Exception as exc:
            playbooks = []
            recall_metadata = {
                "status": "error",
                "reason": "playbook_recall_failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "hit_count": 0,
            }
        return {
            "diagnosis_playbooks": playbooks,
            "playbook_recall": {
                "enabled": True,
                "status": str(recall_metadata.get("status") or "completed"),
                "reason": str(recall_metadata.get("reason") or ""),
                "hit_count": len(playbooks),
                "playbook_ids": [item.playbook_id for item in playbooks],
                "failure_mode": str(recall_metadata.get("failure_mode") or ""),
                "service_type": str(recall_metadata.get("service_type") or ""),
                "error_type": str(recall_metadata.get("error_type") or ""),
                "error": str(recall_metadata.get("error") or ""),
            },
        }

    async def _load_similar_cases(
        self,
        *,
        service: str,
        cluster: str,
        namespace: str,
        message: str,
        session_id: str,
        diagnosis_playbooks: list[Any] | None = None,
    ) -> dict[str, Any]:
        if not service or self.case_retriever is None:
            return {
                "similar_cases": [],
                "case_recall": {
                    "auto_prefetch_enabled": False,
                    "prefetch_reason": "missing_service_or_retriever",
                    "prefetch_status": "skipped",
                    "prefetched_case_count": 0,
                    "case_memory_reason": "missing_service_or_retriever",
                },
            }
        should_prefetch, prefetch_reason = self._should_prefetch_similar_cases(
            message=message,
            service=service,
            cluster=cluster,
            namespace=namespace,
            diagnosis_playbooks=list(diagnosis_playbooks or []),
        )
        if not should_prefetch:
            playbook_ids = [str(getattr(item, "playbook_id", "") or "") for item in list(diagnosis_playbooks or [])]
            return {
                "similar_cases": [],
                "case_recall": {
                    "auto_prefetch_enabled": False,
                    "prefetch_reason": prefetch_reason,
                    "prefetch_status": "skipped",
                    "prefetched_case_count": 0,
                    "case_memory_reason": prefetch_reason,
                    "deferred_by_playbook": prefetch_reason == "deferred_by_playbook",
                    "deferred_by_playbook_ids": [item for item in playbook_ids if item],
                },
            }
        try:
            similar_cases = await self.case_retriever.recall(
                service=service,
                cluster=cluster,
                namespace=namespace,
                message=message,
                session_id=session_id,
            )
            recall_metadata = dict(getattr(self.case_retriever, "last_recall_metadata", {}) or {})
        except Exception as exc:
            similar_cases = []
            recall_metadata = {
                "status": "error",
                "reason": "case_memory_search_failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "hit_count": 0,
            }
        return {
            "similar_cases": similar_cases,
            "case_recall": {
                "auto_prefetch_enabled": True,
                "prefetch_reason": prefetch_reason,
                "prefetched_case_count": len(similar_cases),
                "prefetch_status": str(recall_metadata.get("status") or "completed"),
                "prefetch_error_type": str(recall_metadata.get("error_type") or ""),
                "prefetch_error": str(recall_metadata.get("error") or ""),
                "case_memory_reason": str(recall_metadata.get("reason") or ""),
            },
        }

    @staticmethod
    def _should_prefetch_similar_cases(
        *,
        message: str,
        service: str,
        cluster: str,
        namespace: str,
        diagnosis_playbooks: list[Any] | None = None,
    ) -> tuple[bool, str]:
        if not str(service or "").strip():
            return False, "missing_service"
        normalized_message = str(message or "").strip()
        if not normalized_message:
            return False, "empty_message"
        has_playbook = bool(diagnosis_playbooks)
        if has_playbook and not OrchestratorGraphNodes._has_strong_initial_case_trigger(normalized_message):
            return False, "deferred_by_playbook"
        if has_playbook:
            return True, "explicit_history_case_request"
        inferred_failure_mode = infer_failure_mode(normalized_message)
        if inferred_failure_mode:
            return True, f"failure_mode:{inferred_failure_mode}"
        lowered = normalized_message.lower()
        symptom_keywords = (
            "timeout",
            "超时",
            "502",
            "503",
            "504",
            "失败",
            "报错",
            "error",
            "告警",
            "慢查询",
            "连接池",
            "deadlock",
            "死锁",
            "pod",
            "重启",
            "oom",
            "cpu",
            "内存",
            "发布",
            "回滚",
            "pipeline",
            "dns",
            "gateway",
            "ingress",
            "依赖",
            "抖动",
        )
        matched = [keyword for keyword in symptom_keywords if keyword.lower() in lowered]
        if len(matched) >= 2:
            return True, "multiple_symptom_keywords"
        if matched and (str(cluster or "").strip() or str(namespace or "").strip()):
            return True, f"symptom_keyword:{matched[0]}"
        generic_phrases = (
            "出问题",
            "有问题",
            "异常了",
            "看看",
            "帮我看",
            "排查一下",
            "怎么了",
            "不太对",
        )
        if any(phrase in normalized_message for phrase in generic_phrases):
            return False, "query_too_generic"
        return False, "insufficient_symptom_precision"


    @staticmethod
    def _has_strong_initial_case_trigger(message: str) -> bool:
        normalized = str(message or "").lower()
        strong_keywords = (
            "历史案例",
            "历史工单",
            "类似案例",
            "相似案例",
            "之前有没有",
            "上次",
            "再次出现",
            "又出现",
            "复发",
            "复现",
            "reopen",
            "same incident",
            "similar case",
            "known issue",
        )
        return any(keyword in normalized for keyword in strong_keywords)

    async def _expand_context_retrieval(
        self,
        *,
        request: TicketRequest,
        incident_state: IncidentState,
        similar_cases: list[SimilarIncidentCase],
        matched_categories: list[str],
        session_id: str,
        defer_case_lookup: bool = False,
    ) -> dict[str, Any]:
        from ..state.models import RetrievalExpansion

        if self.retrieval_planner is None:
            return {
                "expansion": RetrievalExpansion(),
                "similar_cases": similar_cases,
                "added_rag_context": None,
            }

        rag_context = self._normalize_rag_context(incident_state.rag_context)
        expansion = await self.retrieval_planner.plan(
            request=request.model_dump(),
            rag_context=rag_context.model_dump(),
            similar_cases=similar_cases,
            matched_tool_domains=matched_categories,
        )
        if not expansion.subqueries:
            return {
                "expansion": expansion,
                "similar_cases": similar_cases,
                "added_rag_context": None,
            }

        merged_rag = rag_context.model_copy(deep=True)
        merged_cases = {case.case_id: case for case in similar_cases}
        added_rag_hits = 0
        added_case_hits = 0
        service = str(request.service or incident_state.service or "")
        cluster = str(request.cluster or incident_state.cluster or "")
        namespace = str(request.namespace or incident_state.namespace or "")

        for subquery in expansion.subqueries:
            subquery_added_rag_hits = 0
            subquery_added_case_hits = 0
            if subquery.target in {"knowledge", "both"} and self.knowledge_service is not None:
                extra_bundle = await self.knowledge_service.retrieve_query(
                    query=subquery.query,
                    service=service,
                    top_k=2,
                )
                merged_rag, delta = self._merge_rag_bundles(merged_rag, extra_bundle)
                added_rag_hits += delta
                subquery_added_rag_hits += delta
            if defer_case_lookup and subquery.target in {"cases", "both"}:
                missing = f"case memory search deferred by verified playbook for query: {subquery.query}"
                if missing not in expansion.missing_evidence:
                    expansion.missing_evidence.append(missing)
            elif subquery.target in {"cases", "both"} and self.case_retriever is not None:
                try:
                    extra_cases = await self.case_retriever.recall(
                        service=service,
                        cluster=cluster,
                        namespace=namespace,
                        message=subquery.query,
                        session_id=session_id,
                        limit=3,
                        failure_mode=subquery.failure_mode,
                        root_cause_taxonomy=subquery.root_cause_taxonomy,
                    )
                    recall_metadata = dict(getattr(self.case_retriever, "last_recall_metadata", {}) or {})
                    if recall_metadata.get("status") == "error":
                        missing = (
                            f"case memory search failed for query: {subquery.query} "
                            f"({recall_metadata.get('error_type') or 'case_memory_search_failed'})"
                        )
                        if missing not in expansion.missing_evidence:
                            expansion.missing_evidence.append(missing)
                except Exception as exc:
                    extra_cases = []
                    missing = f"case memory search failed for query: {subquery.query} ({type(exc).__name__})"
                    if missing not in expansion.missing_evidence:
                        expansion.missing_evidence.append(missing)
                for case in extra_cases:
                    existing = merged_cases.get(case.case_id)
                    if existing is None:
                        merged_cases[case.case_id] = case
                        added_case_hits += 1
                        subquery_added_case_hits += 1
                        continue
                    if case.recall_score > existing.recall_score:
                        merged_cases[case.case_id] = case
            subquery.added_rag_hits = subquery_added_rag_hits
            subquery.added_case_hits = subquery_added_case_hits
        expansion.added_rag_hits = added_rag_hits
        expansion.added_case_hits = added_case_hits
        return {
            "expansion": expansion,
            "similar_cases": list(merged_cases.values()),
            "added_rag_context": merged_rag,
        }

    @staticmethod
    def _merge_rag_bundles(base: RAGContextBundle, extra: RAGContextBundle) -> tuple[RAGContextBundle, int]:
        merged = base.model_copy(deep=True)
        seen = {(item.chunk_id, item.path, item.section) for item in list(merged.context or merged.hits)}
        added = 0
        for item in list(extra.context or extra.hits):
            key = (item.chunk_id, item.path, item.section)
            if key in seen:
                continue
            seen.add(key)
            merged.hits.append(item)
            merged.context.append(item)
            added += 1
        merged.citations = list(dict.fromkeys([*merged.citations, *extra.citations]))
        merged.facts = list(merged.facts) + [fact for fact in extra.facts if fact not in merged.facts]
        merged.index_info = {
            **dict(merged.index_info or {}),
            "agentic_expansion": True,
            "subquery_expansion_count": dict(merged.index_info or {}).get("subquery_expansion_count", 0) + 1,
        }
        return merged, added

    def _match_tool_domains(
        self,
        *,
        request: TicketRequest,
        incident_state: IncidentState,
        similar_cases: list[SimilarIncidentCase],
    ) -> list[str]:
        message_parts = [
            str(request.message or ""),
            str(request.service or ""),
            str(request.cluster or ""),
            str(request.namespace or ""),
        ]
        rag_context = self._normalize_rag_context(incident_state.rag_context)
        for item in list(rag_context.context or rag_context.hits)[:3]:
            message_parts.extend([str(item.title or ""), str(item.section or ""), str(item.snippet or "")])
        for case in similar_cases:
            message_parts.extend([case.symptom, case.root_cause, case.final_action, case.summary])
        haystack = " ".join(part.lower() for part in message_parts if part).strip()
        incremental = incident_state.shared_context.get("incremental_tool_domains") if isinstance(incident_state.shared_context, dict) else []
        return self._infer_tool_domains_from_haystack(
            haystack,
            has_service=bool(request.service),
            incremental=list(incremental or []),
        )

    @staticmethod
    def _score_context_quality(
        *,
        request: TicketRequest,
        rag_context: RAGContextBundle | dict[str, Any] | None,
        similar_cases: list[SimilarIncidentCase],
    ) -> float:
        bundle = rag_context if isinstance(rag_context, RAGContextBundle) else RAGContextBundle.model_validate(rag_context or {})
        score = 0.0
        if request.service:
            score += 0.35
        if bundle.hits or bundle.context:
            score += 0.4
        if similar_cases:
            score += 0.25
        return min(score, 1.0)

    @staticmethod
    def _build_primary_approval_proposals(ranked_result: RankedResult) -> list[ApprovalProposal]:
        primary = ranked_result.primary
        if primary is None or not str(primary.recommended_action or "").strip():
            return []
        proposal_id = str(uuid5(NAMESPACE_URL, f"{primary.hypothesis_id}:{primary.recommended_action}"))
        verification_plan = {
            "objective": f"验证主根因 {primary.root_cause} 对应动作后的恢复情况",
            "checks": list(primary.checks_passed or []),
            "success_criteria": list(primary.evidence or []),
        }
        return [
            ApprovalProposal(
                proposal_id=proposal_id,
                source_agent="ranker",
                action=primary.recommended_action,
                risk=primary.action_risk,
                reason=f"主根因 {primary.root_cause} 当前排序最高，建议优先执行该动作。",
                params=dict(primary.action_params),
                requires_approval=str(primary.action_risk).lower() in {"high", "critical"},
                title=primary.recommended_action,
                target=str(primary.action_params.get("service") or primary.action_params.get("target") or "") or None,
                evidence=list(primary.evidence[:5]),
                metadata={
                    "hypothesis_id": primary.hypothesis_id,
                    "root_cause": primary.root_cause,
                    "verification_plan": verification_plan,
                    "ranker_score": float(primary.metadata.get("ranker", {}).get("final_score", 0.0)),
                },
            )
        ]

    async def approval_gate(self, state: TicketGraphState) -> Dict[str, Any]:
        incident_state = state["incident_state"]
        route_decision = state.get("route_decision")
        ranked_result = state.get("ranked_result") or incident_state.ranked_result
        observability = get_observability()
        with observability.start_span(
            name="graph.approval_gate",
            as_type="span",
            input={"ticket_id": incident_state.ticket_id, "proposal_count": len(incident_state.approval_proposals)},
            metadata={"node": "approval_gate", "route_intent": getattr(route_decision, "intent", None)},
        ) as span:
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
                "route_intent": getattr(route_decision, "intent", None),
            }
            if approval_request is not None:
                pending_node = "end"
                response = {
                    "ticket_id": incident_state.ticket_id,
                    "status": "awaiting_approval",
                    "message": "检测到高风险动作，需审批后才能继续执行。",
                    "approval_request": approval_request,
                    "diagnosis": self._render_hypothesis_diagnosis(
                        route_decision=route_decision,
                        incident_state=next_incident_state,
                        transition_notes=transition_notes,
                        ranked_result=ranked_result,
                    ),
                }
                next_incident_state.final_message = response["message"]
                next_incident_state.status = "awaiting_approval"
            elif next_incident_state.approved_actions:
                pending_node = "execute"
                response = None
            else:
                pending_node = "hypothesis_graph"
                response = None
            span.update(
                output={
                    "pending_node": pending_node,
                    "approval_id": approval_request.get("approval_id") if isinstance(approval_request, dict) else None,
                }
            )
            return {
                "incident_state": next_incident_state,
                "approval_request": approval_request,
                "transition_notes": transition_notes,
                "response": response,
                "pending_node": pending_node,
            }

    async def execute(self, state: TicketGraphState) -> Dict[str, Any]:
        incident_state = state["incident_state"]
        route_decision = state.get("route_decision")
        approved_actions = list(incident_state.approved_actions or [])
        if not approved_actions:
            return {
                "incident_state": incident_state,
                "response": {
                    "ticket_id": incident_state.ticket_id,
                    "status": "completed",
                    "message": incident_state.final_message or "当前没有需要执行的动作。",
                    "diagnosis": self._render_hypothesis_diagnosis(
                        route_decision=route_decision,
                        incident_state=incident_state,
                        transition_notes=list(state.get("transition_notes") or []),
                        ranked_result=incident_state.ranked_result,
                    ),
                },
                "pending_node": None,
            }

        primary_action = approved_actions[0]
        approval_id = str(primary_action.approval_id or f"auto-approved-{primary_action.proposal_id or primary_action.action}")
        synthetic_approval_request = {
            "approval_id": approval_id,
            "ticket_id": incident_state.ticket_id,
            "thread_id": str(state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
            "proposals": [
                {
                    "proposal_id": primary_action.proposal_id,
                    "agent": "ranker",
                    "action": primary_action.action,
                    "resource": str(primary_action.params.get("service") or primary_action.params.get("target") or ""),
                    "params": dict(primary_action.params),
                    "risk": primary_action.risk,
                    "reason": primary_action.reason,
                    "expected_outcome": primary_action.metadata.get("expected_outcome", primary_action.action),
                    "verification_plan": primary_action.metadata.get("verification_plan", {}),
                    "source_refs": list(primary_action.metadata.get("source_refs", [])),
                    "metadata": dict(primary_action.metadata),
                }
            ],
        }
        approval_record = {
            "approval_id": approval_id,
            "ticket_id": incident_state.ticket_id,
            "thread_id": str(state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
            "action": primary_action.action,
            "risk": primary_action.risk,
            "params": dict(primary_action.params),
        }
        approval_state: ApprovalGraphState = {
            "approval_record": approval_record,
            "session_id": str(state.get("session_id") or incident_state.thread_id or incident_state.ticket_id),
            "thread_id": str(state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
            "approval_request_domain": synthetic_approval_request,
            "approval_decision_request": ApprovalDecisionRequest(
                approved=True,
                approver_id=str(primary_action.approved_by or "system"),
                comment=primary_action.comment,
            ),
            "incident_state": incident_state,
            "transition_notes": list(state.get("transition_notes") or []) + ["auto-approved primary action entered execute node"],
        }
        execute_state = await self.execute_approved_action_transition(approval_state)
        final_state = await self.finalize_approval_decision(execute_state)
        response = dict(final_state.get("response") or {})
        diagnosis = dict(response.get("diagnosis") or {})
        diagnosis.update(
            self._render_hypothesis_diagnosis(
                route_decision=route_decision,
                incident_state=final_state.get("incident_state") or incident_state,
                transition_notes=list(execute_state.get("transition_notes") or []),
                ranked_result=(final_state.get("incident_state") or incident_state).ranked_result
                if hasattr(final_state.get("incident_state") or incident_state, "ranked_result")
                else incident_state.ranked_result,
            )
        )
        response["diagnosis"] = diagnosis
        feedback_interrupt = self._create_feedback_interrupt(
            incident_state=final_state.get("incident_state") or incident_state,
            session_id=str(state.get("session_id") or incident_state.thread_id or incident_state.ticket_id),
            thread_id=str(state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
            ticket_id=incident_state.ticket_id,
        )
        return {
            "incident_state": final_state.get("incident_state"),
            "response": response,
            "feedback_interrupt": feedback_interrupt,
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
        primary_risk = str(primary_proposal.get("risk") or approval.get("risk") or "low")
        precheck_step = None
        primary_step = None
        finalize_step = None

        if primary_proposal and self.execution_store is not None:
            execution_plan = self.execution_store.create_plan(
                {
                    "session_id": session_id,
                    "thread_id": thread_id,
                    "ticket_id": ticket_id,
                    "status": "running",
                    "steps": [],
                    "current_step_id": None,
                    "summary": f"执行已批准动作：{primary_action}",
                    "recovery": {
                        "can_resume": True,
                        "recovery_action": "execute_primary_step",
                        "recovery_reason": "执行计划已创建，下一步进入执行前校验。",
                        "resume_from_step_id": None,
                        "failed_step_id": None,
                        "last_completed_step_id": None,
                        "suggested_retry_count": 0,
                        "hints": [
                            "执行计划采用 precheck -> primary_action -> finalize 三段式控制。",
                            "若高风险动作失败，应先参考 failed_step 和 recovery_hints，由人工确认资源状态与后续处置。",
                        ],
                    },
                    "metadata": {
                        "approval_id": approval_id,
                        "proposal_count": len(proposals),
                        "source": "approval_resume",
                        "executor_mode": "phase_m4_transitional_controlled_execution",
                    },
                }
            )
            precheck_step = self.execution_store.create_step(
                {
                    "plan_id": execution_plan["plan_id"],
                    "session_id": session_id,
                    "action": "execution.precheck_binding",
                    "tool_name": "internal.precheck_binding",
                    "params": {"action": primary_action},
                    "sequence": 10,
                    "dependencies": [],
                    "retry_policy": default_retry_policy(primary_action, risk=primary_risk, step_kind="precheck").model_dump(),
                    "compensation": None,
                    "attempt": 0,
                    "last_error": {},
                    "status": "pending",
                    "result_summary": "等待执行前安全校验。",
                    "evidence": [],
                    "metadata": {
                        "approval_id": approval_id,
                        "proposal_id": primary_proposal.get("proposal_id"),
                        "executor": "execute_approved_action_transition",
                        "step_kind": "precheck",
                    },
                }
            )
            created_steps.append(precheck_step)
            primary_step = self.execution_store.create_step(
                {
                    "plan_id": execution_plan["plan_id"],
                    "session_id": session_id,
                    "action": primary_action,
                    "tool_name": primary_action,
                    "params": dict(primary_proposal.get("params") or {}),
                    "sequence": 20,
                    "dependencies": [precheck_step["step_id"]],
                    "retry_policy": default_retry_policy(primary_action, risk=primary_risk, step_kind="tool").model_dump(),
                    "compensation": (
                        default_compensation_policy(primary_action, risk=primary_risk).model_dump()
                        if default_compensation_policy(primary_action, risk=primary_risk) is not None
                        else None
                    ),
                    "attempt": 0,
                    "last_error": {},
                    "status": "pending",
                    "result_summary": "等待执行主动作。",
                    "evidence": [],
                    "metadata": {
                        "approval_id": approval_id,
                        "proposal_id": primary_proposal.get("proposal_id"),
                        "executor": "execute_approved_action_transition",
                        "step_kind": "primary_action",
                    },
                }
            )
            created_steps.append(primary_step)
            finalize_step = self.execution_store.create_step(
                {
                    "plan_id": execution_plan["plan_id"],
                    "session_id": session_id,
                    "action": "execution.record_result",
                    "tool_name": "internal.record_execution_result",
                    "params": {"action": primary_action},
                    "sequence": 30,
                    "dependencies": [primary_step["step_id"]],
                    "retry_policy": default_retry_policy(primary_action, risk=primary_risk, step_kind="postcheck").model_dump(),
                    "compensation": None,
                    "attempt": 0,
                    "last_error": {},
                    "status": "pending",
                    "result_summary": "等待记录执行结果与证据。",
                    "evidence": [],
                    "metadata": {
                        "approval_id": approval_id,
                        "proposal_id": primary_proposal.get("proposal_id"),
                        "executor": "execute_approved_action_transition",
                        "step_kind": "postcheck",
                    },
                }
            )
            created_steps.append(finalize_step)
            self.execution_store.update_plan(
                execution_plan["plan_id"],
                steps=[step["step_id"] for step in created_steps],
                current_step_id=precheck_step["step_id"],
                metadata={
                    **dict(execution_plan.get("metadata") or {}),
                    "precheck_step_id": precheck_step["step_id"],
                    "primary_step_id": primary_step["step_id"],
                    "finalize_step_id": finalize_step["step_id"],
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
                            "step_id": precheck_step["step_id"],
                            "approval_id": approval_id,
                            "action": primary_action,
                            "current_step_id": precheck_step["step_id"],
                            "recovery_action": "execute_primary_step",
                        },
                    }
                )
            self._append_system_event(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=ticket_id,
                event_type="execution.plan_created",
                payload={
                    "plan_id": execution_plan["plan_id"],
                    "step_ids": [step["step_id"] for step in created_steps],
                    "action": primary_action,
                },
                metadata={"source": "graph.execute_approved_action_transition", "approval_id": approval_id},
            )

        transition_notes = list(state.get("transition_notes") or [])
        current_failed_step_id = precheck_step.get("step_id") if precheck_step is not None else None
        recovery_hints: list[str] = []
        try:
            if primary_proposal:
                if precheck_step is not None and self.execution_store is not None:
                    self.execution_store.update_step(
                        precheck_step["step_id"],
                        status="running",
                        result_summary="正在执行审批快照和参数绑定校验。",
                        attempt=1,
                        started_at=utc_now(),
                    )
                    self._append_system_event(
                        session_id=session_id,
                        thread_id=thread_id,
                        ticket_id=ticket_id,
                        event_type="execution.step_started",
                        payload={
                            "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                            "step_id": precheck_step["step_id"],
                            "action": precheck_step["action"],
                            "sequence": precheck_step["sequence"],
                        },
                        metadata={"source": "graph.execute_approved_action_transition", "approval_id": approval_id},
                    )
                execution_binding = validate_execution_binding(primary_proposal, approval_request_domain)
                transition_notes.append("execution safety validation passed before external tool call")
                if precheck_step is not None and self.execution_store is not None:
                    precheck_evidence = ["approval snapshot validated", "registered action policy matched"]
                    precheck_step = self.execution_store.update_step(
                        precheck_step["step_id"],
                        status="completed",
                        result_summary="执行前校验通过，允许进入主动作执行。",
                        evidence=precheck_evidence,
                        metadata={
                            **dict(precheck_step.get("metadata") or {}),
                            "approval_snapshot": dict(execution_binding.get("snapshot") or {}),
                        },
                        finished_at=utc_now(),
                    )
                    self._append_system_event(
                        session_id=session_id,
                        thread_id=thread_id,
                        ticket_id=ticket_id,
                        event_type="execution.step_finished",
                        payload={
                            "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                            "step_id": precheck_step["step_id"],
                            "action": precheck_step["action"],
                            "status": "completed",
                        },
                        metadata={"source": "graph.execute_approved_action_transition", "approval_id": approval_id},
                    )
                if execution_plan is not None and self.execution_store is not None and primary_step is not None and precheck_step is not None:
                    self.execution_store.update_plan(
                        execution_plan["plan_id"],
                        current_step_id=primary_step["step_id"],
                        recovery={
                            "can_resume": True,
                            "recovery_action": "execute_primary_step",
                            "recovery_reason": "执行前校验通过，可继续执行主动作。",
                            "resume_from_step_id": primary_step["step_id"],
                            "failed_step_id": None,
                            "last_completed_step_id": precheck_step["step_id"],
                            "suggested_retry_count": 0,
                            "hints": [
                                "若主动作失败，当前阶段统一转人工处理，并结合 retry_policy 与 compensation 做判断。",
                                "外部动作执行前已完成审批快照校验。",
                            ],
                        },
                    )
                if primary_step is not None and self.execution_store is not None:
                    primary_step = self.execution_store.update_step(
                        primary_step["step_id"],
                        status="running",
                        result_summary="正在执行已批准的主动作。",
                        attempt=1,
                        started_at=utc_now(),
                    )
                    self._append_system_event(
                        session_id=session_id,
                        thread_id=thread_id,
                        ticket_id=ticket_id,
                        event_type="execution.started",
                        payload={
                            "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                            "step_id": primary_step["step_id"],
                            "action": primary_action,
                        },
                        metadata={"source": "graph.execute_approved_action_transition", "approval_id": approval_id},
                    )
                current_failed_step_id = primary_step.get("step_id") if primary_step is not None else current_failed_step_id
                result = await self._execute_approved_action_transition(
                    approval_request_domain,
                    request,
                    execution_binding=execution_binding,
                )
        except Exception as exc:
            transition_notes.append(
                "approved action execution failed before finalize; latest execution checkpoint can be used for manual troubleshooting"
            )
            failure_summary = f"审批已通过，但执行失败：{exc}"
            if isinstance(exc, ExecutionSafetyError):
                transition_notes.append("execution safety validation blocked external tool execution")
            retry_state = retry_state_for_attempt(
                default_retry_policy(primary_action, risk=primary_risk, step_kind="tool"),
                attempt=int((primary_step or {}).get("attempt") or 1),
                error=exc,
            )
            failure_recovery_action = "manual_intervention"
            recovery_hints = [str(retry_state.get("operator_hint") or "")]
            if primary_step is not None and primary_step.get("compensation"):
                compensation = dict(primary_step.get("compensation") or {})
                hint = str(compensation.get("operator_hint") or compensation.get("reason") or "")
                if hint:
                    recovery_hints.append(hint)
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
                    "step_id": current_failed_step_id,
                },
            )
            failed_step_ref = current_failed_step_id
            if failed_step_ref is not None and self.execution_store is not None:
                self.execution_store.update_step(
                    failed_step_ref,
                    status="failed",
                    result_summary=failure_result.summary,
                    evidence=list(failure_result.evidence),
                    metadata={
                        **dict((precheck_step if failed_step_ref == (precheck_step or {}).get("step_id") else primary_step or {}).get("metadata") or {}),
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                    last_error={"error": str(exc), "error_type": type(exc).__name__},
                    finished_at=utc_now(),
                )
            if execution_plan is not None and self.execution_store is not None:
                self.execution_store.update_plan(
                    execution_plan["plan_id"],
                    status="failed",
                    steps=[step["step_id"] for step in created_steps],
                    current_step_id=failed_step_ref,
                    summary=failure_result.summary,
                    recovery={
                        "can_resume": True,
                        "recovery_action": failure_recovery_action,
                        "recovery_reason": (
                            "执行前校验失败，需人工核对审批快照、动作注册与目标资源状态。"
                            if isinstance(exc, ExecutionSafetyError)
                            else "主动作执行失败，当前阶段统一转人工处理，需先确认外部资源状态后再决定后续动作。"
                        ),
                        "resume_from_step_id": failed_step_ref,
                        "failed_step_id": failed_step_ref,
                        "last_completed_step_id": precheck_step.get("step_id") if precheck_step is not None and failed_step_ref != precheck_step.get("step_id") else None,
                        "suggested_retry_count": int(retry_state.get("remaining_attempts") or 0),
                        "hints": [hint for hint in recovery_hints if hint],
                    },
                    metadata={
                        **dict(execution_plan.get("metadata") or {}),
                        "failed_step_id": failed_step_ref,
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
                        "next_action": failure_recovery_action,
                        "state_snapshot": incident_state.model_dump(),
                        "metadata": {
                            "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                            "step_id": failed_step_ref,
                            "approval_id": approval_id,
                            "action": primary_action,
                            "step_status": "failed",
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                            "recovery_action": failure_recovery_action,
                            "failed_step_id": failed_step_ref,
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
                    "step_id": failed_step_ref,
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
                "failed_step_id": failed_step_ref,
                "recovery_action": failure_recovery_action,
                "recovery_hints": [hint for hint in recovery_hints if hint],
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
            primary_step = self.execution_store.update_step(
                primary_step["step_id"],
                status=primary_execution_state.status,
                result_summary=primary_execution_state.summary,
                evidence=list(primary_execution_state.evidence),
                metadata={
                    **dict(primary_step.get("metadata") or {}),
                    "payload": dict(primary_execution_state.payload),
                    "risk": primary_execution_state.risk,
                },
                last_error={},
                finished_at=utc_now(),
            )
            self._append_system_event(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=ticket_id,
                event_type="execution.step_finished",
                payload={
                    "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                    "step_id": primary_step["step_id"],
                    "action": primary_execution_state.action,
                    "status": primary_execution_state.status,
                },
                metadata={"source": "graph.execute_approved_action_transition", "approval_id": approval_id},
            )

        if finalize_step is not None and self.execution_store is not None:
            finalize_step = self.execution_store.update_step(
                finalize_step["step_id"],
                status="running",
                result_summary="正在记录执行结果与关键证据。",
                attempt=1,
                started_at=utc_now(),
            )
            self._append_system_event(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=ticket_id,
                event_type="execution.step_started",
                payload={
                    "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                    "step_id": finalize_step["step_id"],
                    "action": finalize_step["action"],
                    "sequence": finalize_step["sequence"],
                },
                metadata={"source": "graph.execute_approved_action_transition", "approval_id": approval_id},
            )
            finalize_step = self.execution_store.update_step(
                finalize_step["step_id"],
                status="completed",
                result_summary="执行结果、证据链和恢复元数据已写入执行计划。",
                evidence=list(primary_execution_state.evidence),
                metadata={
                    **dict(finalize_step.get("metadata") or {}),
                    "result_action": primary_execution_state.action,
                    "result_status": primary_execution_state.status,
                },
                last_error={},
                finished_at=utc_now(),
            )
            self._append_system_event(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=ticket_id,
                event_type="execution.step_finished",
                payload={
                    "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
                    "step_id": finalize_step["step_id"],
                    "action": finalize_step["action"],
                    "status": "completed",
                },
                metadata={"source": "graph.execute_approved_action_transition", "approval_id": approval_id},
            )

        next_sequence = 40
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
                        "sequence": next_sequence,
                        "dependencies": [finalize_step["step_id"]] if finalize_step is not None else [primary_step["step_id"]] if primary_step is not None else [],
                        "retry_policy": default_retry_policy(str(proposal.get("action") or ""), risk=str(proposal.get("risk") or "low"), step_kind="tool").model_dump(),
                        "compensation": None,
                        "attempt": 0,
                        "last_error": {},
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
                next_sequence += 10
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
            recovery_action = "none" if plan_status == "completed" else "manual_intervention"
            next_action = "complete" if plan_status == "completed" else "manual_intervention"
            self.execution_store.update_plan(
                execution_plan["plan_id"],
                status=plan_status,
                steps=[step["step_id"] for step in created_steps],
                current_step_id=finalize_step.get("step_id") if plan_status == "completed" and finalize_step is not None else primary_step.get("step_id") if primary_step is not None else None,
                summary=primary_execution_state.summary,
                recovery={
                    "can_resume": False,
                    "recovery_action": recovery_action,
                    "recovery_reason": (
                        "执行动作已完成；若系统在收尾前中断，需人工确认动作是否已实际生效。"
                        if plan_status == "completed"
                        else "主动作执行失败，当前阶段统一转人工处理。"
                    ),
                    "resume_from_step_id": None if plan_status == "completed" else primary_step.get("step_id") if primary_step is not None else None,
                    "failed_step_id": primary_step.get("step_id") if plan_status != "completed" and primary_step is not None else None,
                    "last_completed_step_id": finalize_step.get("step_id") if finalize_step is not None else primary_step.get("step_id") if primary_step is not None else None,
                    "suggested_retry_count": 0,
                    "hints": (
                        ["执行计划已完成；若会话在收尾前中断，请先人工确认动作是否已实际生效。"]
                        if plan_status == "completed"
                        else ["参考 retry_policy、补偿策略与外部资源状态，由人工决定后续动作。"]
                    ),
                },
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
                            "current_step_id": finalize_step.get("step_id") if finalize_step is not None else primary_step.get("step_id") if primary_step is not None else None,
                            "recovery_action": recovery_action,
                        },
                    }
                )

        next_incident_state = apply_execution_results_to_state(incident_state, execution_results)
        approval_result = dict(result)
        diagnosis = dict(approval_result.get("diagnosis") or {})
        diagnosis["execution_limit"] = {
            "transitional_executor_mode": "phase_m4_controlled_execution",
            "approved_proposal_count": len(proposals),
            "executed_proposal_count": 1 if proposals else 0,
            "skipped_proposal_count": max(len(proposals) - 1, 0),
            "plan_id": execution_plan.get("plan_id") if execution_plan is not None else None,
            "step_ids": [step.get("step_id") for step in created_steps],
            "failed_step_id": None,
            "recovery_action": "none" if primary_execution_state.status != "failed" else "manual_intervention",
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
    def route_after_ticket_approval_gate(state: TicketGraphState) -> str:
        pending_node = str(state.get("pending_node") or "")
        if pending_node == "execute":
            return "execute"
        if pending_node == "hypothesis_graph":
            return "hypothesis_graph"
        return "end"

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
    def _render_hypothesis_diagnosis(
        *,
        route_decision,
        incident_state: IncidentState,
        transition_notes: list[str] | None = None,
        ranked_result: RankedResult | None = None,
    ) -> Dict[str, object]:
        diagnosis: Dict[str, object] = {
            "summary": incident_state.final_summary or "",
            "routing": route_decision.model_dump() if hasattr(route_decision, "model_dump") else {},
            "context_snapshot": incident_state.context_snapshot.model_dump() if incident_state.context_snapshot is not None else None,
            "hypotheses": [item.model_dump() for item in incident_state.hypotheses],
            "verification_results": [item.model_dump() for item in incident_state.verification_results],
            "ranked_result": ranked_result.model_dump() if ranked_result is not None else incident_state.ranked_result.model_dump() if incident_state.ranked_result is not None else None,
            "incident_state": incident_state.model_dump(),
        }
        if transition_notes:
            diagnosis["graph"] = {"transition_notes": list(transition_notes)}
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
            params.setdefault("orchestration_mode", "hypothesis_graph")
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
            try:
                local_result = await LocalToolRuntime().execute_action(
                    action,
                    params=tool_params,
                    incident_state=None,
                )
                return {
                    "ticket_id": approval_request["ticket_id"],
                    "status": local_result.get("status", "completed"),
                    "message": local_result.get("message") or f"审批已通过；已执行 {action}。",
                    "diagnosis": {
                        "approval": {
                            "approval_id": approval_request["approval_id"],
                            "action": action,
                            "status": "approved",
                        },
                        "execution": (local_result.get("diagnosis") or {}).get("execution", local_result.get("structuredContent", {})),
                    },
                }
            except RuntimeError:
                pass
        if not mcp_server:
            raise ValueError("approval params missing mcp_server")

        observability = get_observability()
        with observability.start_span(
            name="execution.approved_action_call",
            as_type="tool",
            input={
                "approval_id": approval_request.get("approval_id"),
                "action": action,
                "tool_params": tool_params,
            },
            metadata={"mcp_server": str(mcp_server), "ticket_id": approval_request.get("ticket_id")},
        ) as span:
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
            span.update(output={"status": response_status, "summary": summary, "execution": execution_payload})
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
