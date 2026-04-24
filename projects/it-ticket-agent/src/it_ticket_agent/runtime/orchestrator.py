from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict
from uuid import uuid4

from ..approval.adapters import legacy_decision_to_record
from ..approval_store import ApprovalStore
from ..bad_case_store import BadCaseCandidateStore
from ..checkpoint_store import CheckpointStore
from ..execution_store import ExecutionStore
from ..context import ContextAssembler
from ..graph import (
    OrchestratorGraphBuilder,
    OrchestratorGraphNodes,
    build_approval_graph_input,
)
from ..graph.react_builder import ReactGraphBuilder
from ..graph.react_nodes import ReactGraphNodes
from ..graph.react_state import build_react_graph_input, extract_react_graph_response
from ..interrupt_store import InterruptStore
from ..knowledge import KnowledgeService
from ..mcp import MCPConnectionManager
from ..rag_client import RAGServiceClient
from ..system_event_store import SystemEventStore
from ..memory_store import IncidentCaseStore, ProcessMemoryStore
from ..observability import configure_observability
from ..orchestration.retrieval_planner import RetrievalPlanner
from ..case_memory_analysis import build_case_memory_reason_codes
from ..case_retrieval import CaseRetriever, infer_failure_mode, infer_root_cause_taxonomy
from ..case_vector_indexer import CaseVectorIndexer
from ..schemas import (
    ApprovalDecisionRequest,
    ConversationCreateRequest,
    ConversationMessageRequest,
    ConversationResumeRequest,
    TicketRequest,
)
from ..session import SessionService
from ..session.models import ConversationTurn, utc_now
from ..session_store import SessionStore
from ..settings import Settings
from ..state.approval_transformers import apply_approval_resume_result_to_state
from ..state.incident_state import IncidentState
from ..runtime.topic_shift_detector import TopicShiftDetector
from ..service_names import infer_service_name
from ..slot_resolution import infer_host_identifier, resolve_slots
from .react_supervisor import ReactSupervisor
from .smart_router import SmartRouter


logger = logging.getLogger(__name__)


class SupervisorOrchestrator:
    def __init__(
        self,
        settings: Settings,
        approval_store: ApprovalStore,
        session_store: SessionStore,
        interrupt_store: InterruptStore,
        checkpoint_store: CheckpointStore | None = None,
        process_memory_store: ProcessMemoryStore | None = None,
        execution_store: ExecutionStore | None = None,
        system_event_store: SystemEventStore | None = None,
        session_service: SessionService | None = None,
        incident_case_store: IncidentCaseStore | None = None,
        bad_case_candidate_store: BadCaseCandidateStore | None = None,
    ) -> None:
        self.settings = settings
        if self.settings.orchestration_mode != "react_tool_first":
            logger.warning("legacy orchestration_mode is no longer used; forcing react_tool_first")
            self.settings.orchestration_mode = "react_tool_first"
        self.approval_store = approval_store
        self.session_store = session_store
        self.interrupt_store = interrupt_store
        self.session_service = session_service or SessionService(session_store)
        self.checkpoint_store = checkpoint_store or CheckpointStore(settings.approval_db_path)
        self.process_memory_store = process_memory_store or ProcessMemoryStore(settings.approval_db_path)
        self.execution_store = execution_store or ExecutionStore(settings.approval_db_path)
        self.incident_case_store = incident_case_store or IncidentCaseStore(settings.approval_db_path)
        self.bad_case_candidate_store = bad_case_candidate_store or BadCaseCandidateStore(settings.approval_db_path)
        self.system_event_store = system_event_store or SystemEventStore(settings.approval_db_path)
        self.observability = configure_observability(settings)
        self.context_assembler = ContextAssembler()
        self.connection_manager = MCPConnectionManager(settings.mcp_connections_path)
        self.knowledge_client = RAGServiceClient(settings)
        self.knowledge_service = KnowledgeService(self.knowledge_client)
        self.smart_router = SmartRouter(settings)
        self.topic_shift_detector = TopicShiftDetector()
        self.retrieval_planner = RetrievalPlanner(settings)
        self.case_vector_indexer = CaseVectorIndexer(settings, self.incident_case_store, self.knowledge_client)
        self.case_retriever = CaseRetriever(self.knowledge_client, settings)
        self.graph_nodes = OrchestratorGraphNodes(
            approval_store=self.approval_store,
            session_store=self.session_store,
            interrupt_store=self.interrupt_store,
            process_memory_store=self.process_memory_store,
            incident_case_store=self.incident_case_store,
            connection_manager=self.connection_manager,
            execution_store=self.execution_store,
            system_event_store=self.system_event_store,
            smart_router=self.smart_router,
            case_retriever=self.case_retriever,
            knowledge_service=self.knowledge_service,
            retrieval_planner=self.retrieval_planner,
        )
        self.graph_builder = OrchestratorGraphBuilder(self.graph_nodes)
        self.react_supervisor = ReactSupervisor(
            self.graph_nodes,
            settings=settings,
            max_iterations=settings.react_max_iterations,
            max_tool_calls=settings.react_max_tool_calls,
            confidence_threshold=settings.react_confidence_threshold,
            max_parallel_branches=settings.react_max_parallel_branches,
            summary_after_n_steps=settings.react_summary_after_n_steps,
            max_context_tokens=settings.react_max_context_tokens,
        )
        self.react_graph_nodes = ReactGraphNodes(
            smart_router=self.smart_router,
            legacy_nodes=self.graph_nodes,
            supervisor=self.react_supervisor,
            tool_middleware=self.react_supervisor.tool_middleware,
            action_executor=self.react_supervisor.tool_runtime,
        )
        self.react_graph_builder = ReactGraphBuilder(self.react_graph_nodes)
        self.react_ticket_graph = self.react_graph_builder.build_ticket_graph()
        self.ticket_graph = self.react_ticket_graph
        self.approval_graph = self.graph_builder.build_approval_graph()

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
    ) -> dict[str, Any]:
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

    def _summarize_process_memory(self, session_id: str) -> dict[str, Any]:
        return self.process_memory_store.summarize(session_id)

    def _summarize_incident_cases(self, *, service: str | None, session_id: str | None = None) -> list[dict[str, Any]]:
        if not service:
            return []
        cases = self.incident_case_store.list_cases(service=service, limit=3)
        if session_id is None:
            return cases
        return [case for case in cases if str(case.get("session_id") or "") != str(session_id)]

    def _upsert_incident_case(
        self,
        *,
        session: dict[str, Any],
        response: dict[str, Any],
        incident_state: dict[str, Any],
    ) -> dict[str, Any]:
        diagnosis = response.get("diagnosis") if isinstance(response.get("diagnosis"), dict) else {}
        session_memory = dict(session.get("session_memory") or {})
        aggregated_result = {}
        incident_metadata = incident_state.get("metadata") if isinstance(incident_state.get("metadata"), dict) else {}
        if isinstance(incident_metadata.get("aggregated_result"), dict):
            aggregated_result = dict(incident_metadata.get("aggregated_result") or {})
        key_evidence = []
        if isinstance(diagnosis.get("evidence"), list):
            key_evidence.extend(str(item) for item in diagnosis.get("evidence", []) if item)
        if not key_evidence and isinstance(aggregated_result.get("evidence"), list):
            key_evidence.extend(str(item) for item in aggregated_result.get("evidence", []) if item)
        findings = diagnosis.get("findings") if isinstance(diagnosis.get("findings"), list) else []
        if not findings and isinstance(aggregated_result.get("findings"), list):
            findings = aggregated_result.get("findings")
        for item in findings[:3]:
            if isinstance(item, dict):
                detail = str(item.get("detail") or item.get("title") or "")
                if detail:
                    key_evidence.append(detail)
        if not key_evidence:
            verification_results = incident_state.get("verification_results") if isinstance(incident_state.get("verification_results"), list) else []
            for result in verification_results[:1]:
                if isinstance(result, dict):
                    key_evidence.extend(str(item) for item in result.get("evidence", [])[:3] if item)

        verification_results = incident_state.get("verification_results") if isinstance(incident_state.get("verification_results"), list) else []
        verification_passed = None
        if verification_results:
            statuses = [str(item.get("status") or "") for item in verification_results if isinstance(item, dict)]
            if any(status == "failed" for status in statuses):
                verification_passed = False
            elif any(status == "passed" for status in statuses):
                verification_passed = True

        approved_actions = incident_state.get("approved_actions") if isinstance(incident_state.get("approved_actions"), list) else []
        execution_results = incident_state.get("execution_results") if isinstance(incident_state.get("execution_results"), list) else []
        diagnosis_approval = diagnosis.get("approval") if isinstance(diagnosis.get("approval"), dict) else {}
        ranked_result = incident_state.get("ranked_result") if isinstance(incident_state.get("ranked_result"), dict) else {}
        final_action = ""
        if approved_actions and isinstance(approved_actions[0], dict):
            final_action = str(approved_actions[0].get("action") or "")
        if not final_action and execution_results and isinstance(execution_results[0], dict):
            final_action = str(execution_results[0].get("action") or "")
        if not final_action:
            final_action = str(diagnosis_approval.get("action") or "")

        root_cause = str(
            diagnosis.get("root_cause")
            or diagnosis.get("summary")
            or aggregated_result.get("summary")
            or incident_state.get("final_summary")
            or ""
        )
        symptom = str(
            session_memory.get("original_user_message")
            or incident_state.get("message")
            or response.get("message")
            or ""
        )
        final_conclusion = str(
            response.get("message")
            or incident_state.get("final_message")
            or root_cause
            or symptom
        )
        approval_required = bool(session.get("latest_approval_id") or diagnosis_approval or approved_actions)
        failure_mode = infer_failure_mode(symptom or root_cause or final_conclusion)
        root_cause_taxonomy = infer_root_cause_taxonomy(symptom or root_cause or final_conclusion)
        signal_pattern = ""
        if failure_mode == "oom":
            signal_pattern = "pod_restart+heap_pressure"
        elif failure_mode == "dependency_timeout":
            signal_pattern = "timeout+gateway_unhealthy"
        elif failure_mode == "db_pool_saturation":
            signal_pattern = "slow_query+pool_saturation"
        elif failure_mode == "deploy_regression":
            signal_pattern = "release_window+5xx_spike"
        action_pattern = str(final_action or "")

        saved_case = self.incident_case_store.upsert(
            {
                "session_id": str(session.get("session_id") or ""),
                "thread_id": str(session.get("thread_id") or session.get("session_id") or ""),
                "ticket_id": str(session.get("ticket_id") or session.get("session_id") or ""),
                "service": str(incident_state.get("service") or ""),
                "cluster": str(incident_state.get("cluster") or ""),
                "namespace": str(incident_state.get("namespace") or ""),
                "current_agent": str(session.get("current_agent") or ""),
                "failure_mode": failure_mode,
                "root_cause_taxonomy": root_cause_taxonomy,
                "signal_pattern": signal_pattern,
                "action_pattern": action_pattern,
                "symptom": symptom,
                "root_cause": root_cause,
                "key_evidence": key_evidence[:5],
                "final_action": final_action,
                "approval_required": approval_required,
                "verification_passed": verification_passed,
                "human_verified": False,
                "hypothesis_accuracy": {},
                "actual_root_cause_hypothesis": "",
                "selected_hypothesis_id": str(((ranked_result.get("primary") or {}) if isinstance(ranked_result, dict) else {}).get("hypothesis_id") or ""),
                "selected_ranker_features": {
                    key: float(value)
                    for key, value in dict((((ranked_result.get("primary") or {}) if isinstance(ranked_result, dict) else {}).get("metadata") or {}).get("ranker", {})).items()
                    if key in {"evidence_strength", "confidence", "history_match"} and isinstance(value, (int, float))
                },
                "final_conclusion": final_conclusion,
                "closed_at": session.get("closed_at"),
            }
        )
        if self.case_vector_indexer.enabled:
            try:
                asyncio.get_running_loop().create_task(self.case_vector_indexer.index_case(saved_case))
            except RuntimeError:
                pass
        return saved_case

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            return dict(dumped) if isinstance(dumped, dict) else {}
        return {}

    @staticmethod
    def _dedupe_reason_codes(reason_codes: list[str]) -> list[str]:
        deduped: list[str] = []
        for item in reason_codes:
            value = str(item or "").strip()
            if value and value not in deduped:
                deduped.append(value)
        return deduped

    def _build_bad_case_request_payload(
        self,
        *,
        session: dict[str, Any],
        incident_state: dict[str, Any],
    ) -> dict[str, Any]:
        session_memory = dict(session.get("session_memory") or {})
        shared_context = dict(incident_state.get("shared_context") or {})
        return {
            "session_id": session.get("session_id"),
            "thread_id": session.get("thread_id"),
            "ticket_id": session.get("ticket_id"),
            "user_id": session.get("user_id"),
            "message": incident_state.get("message") or session_memory.get("original_user_message"),
            "original_user_message": session_memory.get("original_user_message"),
            "service": incident_state.get("service"),
            "environment": incident_state.get("environment"),
            "host_identifier": incident_state.get("host_identifier"),
            "db_name": incident_state.get("db_name"),
            "db_type": incident_state.get("db_type"),
            "cluster": incident_state.get("cluster"),
            "namespace": incident_state.get("namespace"),
            "channel": incident_state.get("channel"),
            "mock_scenario": shared_context.get("mock_scenario"),
            "mock_scenarios": dict(shared_context.get("mock_scenarios") or {}),
            "mock_tool_responses": dict(shared_context.get("mock_tool_responses") or {}),
            "mock_world_state": dict(shared_context.get("mock_world_state") or {}),
            "current_intent": dict(session_memory.get("current_intent") or {}),
            "current_intent_history": list(session_memory.get("current_intent_history") or []),
            "session_event_queue": [dict(item) for item in list(session_memory.get("session_event_queue") or []) if isinstance(item, dict)],
        }

    @staticmethod
    def _normalize_bad_case_response_payload(response: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(response, dict):
            return {}
        return {
            "status": response.get("status"),
            "message": response.get("message"),
            "diagnosis": dict(response.get("diagnosis") or {}) if isinstance(response.get("diagnosis"), dict) else {},
            "approval_request": (
                dict(response.get("approval_request") or {})
                if isinstance(response.get("approval_request"), dict)
                else {}
            ),
            "pending_interrupt": (
                dict(response.get("pending_interrupt") or {})
                if isinstance(response.get("pending_interrupt"), dict)
                else {}
            ),
        }

    def _latest_assistant_response_payload(self, session_id: str) -> dict[str, Any]:
        turns = self.session_store.list_turns(session_id, limit=20)
        for turn in reversed(turns):
            if str(turn.get("role") or "") != "assistant":
                continue
            structured_payload = dict(turn.get("structured_payload") or {})
            return {
                "status": structured_payload.get("status"),
                "message": turn.get("content"),
                "diagnosis": (
                    dict(structured_payload.get("diagnosis") or {})
                    if isinstance(structured_payload.get("diagnosis"), dict)
                    else {}
                ),
                "approval_request": (
                    dict(structured_payload.get("approval_request") or {})
                    if isinstance(structured_payload.get("approval_request"), dict)
                    else {}
                ),
            }
        return {}

    @staticmethod
    def _resolve_bad_case_severity(reason_codes: list[str]) -> str:
        codes = set(reason_codes)
        if codes.intersection({"human_feedback_negative", "actual_root_cause_provided", "feedback_reopen"}):
            return "high"
        if codes.intersection(
            {
                "tool_budget_reached",
                "iteration_guardrail_reached",
                "rejected_tool_call_detected",
                "retrieval_misaligned_with_primary_root_cause",
                "case_memory_failed",
            }
        ):
            return "medium"
        return "low"

    def _detect_bad_case_reason_codes(
        self,
        *,
        source: str,
        response_payload: dict[str, Any],
        incident_state_snapshot: dict[str, Any],
        incident_case: dict[str, Any] | None,
        human_feedback: dict[str, Any] | None = None,
    ) -> list[str]:
        diagnosis = dict(response_payload.get("diagnosis") or {})
        incident_metadata = dict(incident_state_snapshot.get("metadata") or {})
        context_snapshot = self._as_dict(diagnosis.get("context_snapshot")) or self._as_dict(
            incident_state_snapshot.get("context_snapshot")
        )
        react_runtime = self._as_dict(diagnosis.get("react_runtime")) or self._as_dict(
            incident_metadata.get("react_runtime")
        )
        retrieval_expansion = self._as_dict(context_snapshot.get("retrieval_expansion"))
        case_memory_reason_codes = build_case_memory_reason_codes(
            self._as_dict(context_snapshot.get("case_recall"))
        )
        stop_reason = str(diagnosis.get("stop_reason") or react_runtime.get("stop_reason") or "").strip()
        reason_codes: list[str] = []

        if stop_reason in {"tool_budget_reached", "iteration_guardrail_reached"}:
            reason_codes.append(stop_reason)

        if int(react_runtime.get("rejected_tool_call_count") or 0) > 0:
            reason_codes.append("rejected_tool_call_detected")

        subqueries = [
            self._as_dict(item)
            for item in list(retrieval_expansion.get("subqueries") or [])
            if isinstance(item, dict) or hasattr(item, "model_dump")
        ]
        added_rag_hits = int(retrieval_expansion.get("added_rag_hits") or 0)
        added_case_hits = int(retrieval_expansion.get("added_case_hits") or 0)
        if subqueries and added_rag_hits == 0 and added_case_hits == 0:
            reason_codes.append("retrieval_expansion_no_gain")

        primary_root_cause_taxonomy = str((incident_case or {}).get("root_cause_taxonomy") or "").strip()
        if not primary_root_cause_taxonomy:
            primary_text = str(
                (incident_case or {}).get("root_cause")
                or diagnosis.get("summary")
                or response_payload.get("message")
                or ""
            )
            primary_root_cause_taxonomy = infer_root_cause_taxonomy(primary_text)
        for item in subqueries:
            query_taxonomy = str(item.get("root_cause_taxonomy") or "").strip()
            query_added_hits = int(item.get("added_rag_hits") or 0) + int(item.get("added_case_hits") or 0)
            if query_added_hits > 0 and primary_root_cause_taxonomy and query_taxonomy and query_taxonomy != primary_root_cause_taxonomy:
                reason_codes.append("retrieval_misaligned_with_primary_root_cause")
                break

        feedback_payload = dict(human_feedback or {})
        if feedback_payload.get("human_verified") is False:
            reason_codes.append("human_feedback_negative")
        if str(feedback_payload.get("actual_root_cause_hypothesis") or "").strip():
            reason_codes.append("actual_root_cause_provided")
        if source == "feedback_reopen":
            reason_codes.append("feedback_reopen")

        if reason_codes or "case_memory_failed" in case_memory_reason_codes:
            reason_codes.extend(case_memory_reason_codes)

        return self._dedupe_reason_codes(reason_codes)

    def _create_bad_case_candidate(
        self,
        *,
        session: dict[str, Any],
        source: str,
        response_payload: dict[str, Any],
        incident_state_snapshot: dict[str, Any],
        incident_case: dict[str, Any] | None = None,
        human_feedback: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        reason_codes = self._detect_bad_case_reason_codes(
            source=source,
            response_payload=response_payload,
            incident_state_snapshot=incident_state_snapshot,
            incident_case=incident_case,
            human_feedback=human_feedback,
        )
        if not reason_codes:
            return None
        context_snapshot = self._as_dict((response_payload.get("diagnosis") or {}).get("context_snapshot")) or self._as_dict(
            incident_state_snapshot.get("context_snapshot")
        )
        retrieval_expansion = self._as_dict(context_snapshot.get("retrieval_expansion"))
        observations = list((response_payload.get("diagnosis") or {}).get("observations") or [])
        if not observations:
            observations = list(dict(incident_state_snapshot.get("metadata") or {}).get("react_observations") or [])
        conversation_turns = self.session_store.list_turns(str(session.get("session_id") or ""), limit=20)
        system_events = self.system_event_store.list_for_session(str(session.get("session_id") or ""), limit=40)
        return self.bad_case_candidate_store.create(
            {
                "session_id": str(session.get("session_id") or ""),
                "thread_id": str(session.get("thread_id") or session.get("session_id") or ""),
                "ticket_id": str(session.get("ticket_id") or session.get("session_id") or ""),
                "source": source,
                "reason_codes": reason_codes,
                "severity": self._resolve_bad_case_severity(reason_codes),
                "request_payload": self._build_bad_case_request_payload(
                    session=session,
                    incident_state=incident_state_snapshot,
                ),
                "response_payload": response_payload,
                "incident_state_snapshot": incident_state_snapshot,
                "context_snapshot": context_snapshot,
                "observations": [dict(item) for item in observations if isinstance(item, dict)],
                "retrieval_expansion": retrieval_expansion,
                "human_feedback": dict(human_feedback or {}),
                "conversation_turns": [dict(item) for item in conversation_turns if isinstance(item, dict)],
                "system_events": [dict(item) for item in system_events if isinstance(item, dict)],
            }
        )

    def _maybe_create_bad_case_candidate_from_run(
        self,
        *,
        session: dict[str, Any] | None,
        response: dict[str, Any],
        incident_state: dict[str, Any],
        incident_case: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(session, dict):
            return None
        return self._create_bad_case_candidate(
            session=session,
            source="runtime_completion",
            response_payload=self._normalize_bad_case_response_payload(response),
            incident_state_snapshot=incident_state,
            incident_case=incident_case,
        )

    def _maybe_create_bad_case_candidate_from_feedback(
        self,
        *,
        session: dict[str, Any],
        answer_payload: dict[str, Any],
        incident_case: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        source = "feedback_reopen" if self._has_feedback_reopen_signal(answer_payload) else "feedback_negative"
        return self._create_bad_case_candidate(
            session=session,
            source=source,
            response_payload=self._latest_assistant_response_payload(str(session.get("session_id") or "")),
            incident_state_snapshot=dict(session.get("incident_state") or {}),
            incident_case=incident_case,
            human_feedback={
                "human_verified": bool(answer_payload.get("human_verified")),
                "actual_root_cause_hypothesis": str(answer_payload.get("actual_root_cause_hypothesis") or "").strip(),
                "comment": str(answer_payload.get("comment") or "").strip(),
                "hypothesis_accuracy": {
                    str(key): float(value)
                    for key, value in dict(answer_payload.get("hypothesis_accuracy") or {}).items()
                    if isinstance(value, (int, float))
                },
            },
        )

    def _append_user_turn(self, session_id: str, *, content: str, structured_payload: dict[str, Any]) -> dict[str, Any]:
        return self.session_store.append_turn(
            ConversationTurn(
                session_id=session_id,
                role="user",
                content=content,
                structured_payload=structured_payload,
            )
        )

    @staticmethod
    def _session_event_queue(session: dict[str, Any] | None) -> list[dict[str, Any]]:
        memory = dict((session or {}).get("session_memory") or {})
        queue: list[dict[str, Any]] = []
        for item in list(memory.get("session_event_queue") or []):
            if isinstance(item, dict):
                queue.append(dict(item))
        return queue

    def _build_session_event(
        self,
        *,
        source: str,
        event_type: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "event_id": f"evt-{uuid4().hex[:12]}",
            "source": source,
            "event_type": event_type,
            "message": str(message or "").strip(),
            "metadata": dict(metadata or {}),
            "created_at": utc_now(),
            "consumed_at": None,
        }

    def _persist_session_event_queue(
        self,
        session: dict[str, Any],
        *,
        queue: list[dict[str, Any]],
        original_user_message: str | None = None,
    ) -> dict[str, Any]:
        updated = self.session_service.update_session_state(
            str(session["session_id"]),
            incident_state=dict(session.get("incident_state") or {}),
            status=str(session.get("status") or "active"),
            current_stage=str(session.get("current_stage") or "routing"),
            current_agent=session.get("current_agent"),
            latest_approval_id=session.get("latest_approval_id"),
            pending_interrupt_id=session.get("pending_interrupt_id"),
            last_checkpoint_id=session.get("last_checkpoint_id"),
            session_memory=self._merge_session_memory(
                session,
                original_user_message=original_user_message,
                session_event_queue=queue[-20:],
            ),
        )
        return updated or session

    def _queue_session_event(
        self,
        session: dict[str, Any],
        *,
        event: dict[str, Any],
        original_user_message: str | None = None,
    ) -> dict[str, Any]:
        queue = self._session_event_queue(session)
        queue.append(dict(event))
        return self._persist_session_event_queue(
            session,
            queue=queue,
            original_user_message=original_user_message,
        )

    def _mark_session_events_consumed(
        self,
        session: dict[str, Any],
        *,
        event_ids: list[str],
    ) -> dict[str, Any]:
        if not event_ids:
            return session
        queue = self._session_event_queue(session)
        if not queue:
            return session
        now = utc_now()
        touched = False
        target_ids = set(event_ids)
        for item in queue:
            if str(item.get("event_id") or "") in target_ids and not item.get("consumed_at"):
                item["consumed_at"] = now
                touched = True
        if not touched:
            return session
        return self._persist_session_event_queue(session, queue=queue)

    def _classify_user_message_event(
        self,
        *,
        session: dict[str, Any],
        message: str,
        message_mode: str = "default",
    ) -> dict[str, Any]:
        normalized = str(message or "").strip()
        lowered = normalized.lower()
        topic_shift = self._detect_topic_shift_for_session(session=session, current_message=normalized)
        explicit_mode = str(message_mode or "default").strip().lower()
        if explicit_mode == "supplement":
            reason_tags = ["explicit_message_mode", "supplement_mode"]
            if bool(topic_shift.get("topic_shift_detected")):
                reason_tags.append("topic_shift_detected")
            return {
                "event_type": "supplement",
                "message": normalized,
                "topic_shift": topic_shift,
                "reason_tags": reason_tags,
                "incremental_tool_domains": list(topic_shift.get("incremental_tool_domains") or []),
                "message_tokens": lowered[:120],
            }
        correction_markers = (
            "不是",
            "不对",
            "误判",
            "纠正",
            "而是",
            "其实",
            "更像",
            "不准确",
            "你错了",
            "判断错",
            "真实根因",
            "实际根因",
            "先别",
        )
        supplement_markers = (
            "补充",
            "另外",
            "还有",
            "新增",
            "发现",
            "进一步",
            "刚刚",
            "日志",
            "线索",
            "观测",
            "只在",
            "只有",
            "仅在",
        )
        new_issue_markers = (
            "另一个问题",
            "另外一个问题",
            "新问题",
            "还有个问题",
            "再问一个",
            "顺便问",
        )
        reason_tags: list[str] = []
        if any(marker in normalized for marker in new_issue_markers):
            event_type = "new_issue"
            reason_tags.append("explicit_new_issue")
        elif any(marker in normalized for marker in correction_markers):
            event_type = "correction"
            reason_tags.append("correction_marker")
        elif any(marker in normalized for marker in supplement_markers):
            event_type = "supplement"
            reason_tags.append("supplement_marker")
        elif bool(topic_shift.get("topic_shift_detected")):
            event_type = "new_issue"
            reason_tags.append("topic_shift")
        else:
            event_type = "supplement"
            reason_tags.append("default_followup")
        if bool(topic_shift.get("topic_shift_detected")):
            reason_tags.append("topic_shift_detected")
        return {
            "event_type": event_type,
            "message": normalized,
            "topic_shift": topic_shift,
            "reason_tags": reason_tags,
            "incremental_tool_domains": list(topic_shift.get("incremental_tool_domains") or []),
            "message_tokens": lowered[:120],
        }

    @staticmethod
    def _has_feedback_reopen_signal(answer_payload: dict[str, Any]) -> bool:
        actual_root = str(answer_payload.get("actual_root_cause_hypothesis") or "").strip()
        comment = str(answer_payload.get("comment") or "").strip()
        hypothesis_accuracy = dict(answer_payload.get("hypothesis_accuracy") or {})
        return bool(actual_root or comment or hypothesis_accuracy)

    @staticmethod
    def _build_feedback_reopen_message(answer_payload: dict[str, Any]) -> str:
        actual_root = str(answer_payload.get("actual_root_cause_hypothesis") or "").strip()
        comment = str(answer_payload.get("comment") or "").strip()
        hypothesis_accuracy = {
            str(key): float(value)
            for key, value in dict(answer_payload.get("hypothesis_accuracy") or {}).items()
            if isinstance(value, (int, float))
        }
        parts = ["人工反馈认为上一轮诊断不准确，请基于新信息重新分析。"]
        if actual_root:
            parts.append(f"真实根因假设：{actual_root}")
        if comment:
            parts.append(f"补充说明：{comment}")
        if hypothesis_accuracy:
            ranked = sorted(hypothesis_accuracy.items(), key=lambda item: item[1], reverse=True)
            parts.append(
                "人工准确度判断："
                + "，".join(f"{hypothesis_id}={score:.2f}" for hypothesis_id, score in ranked[:3])
            )
        return "\n".join(parts)

    @staticmethod
    def _feedback_reopen_capability(
        session: dict[str, Any],
        *,
        interrupt: dict[str, Any] | None = None,
        incident_case: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        interrupt_metadata = dict((interrupt or {}).get("metadata") or {})
        if bool(interrupt_metadata.get("can_reject_reopen")):
            return {
                "allowed": True,
                "action_name": str(interrupt_metadata.get("action_name") or ""),
                "action_source": str(interrupt_metadata.get("action_source") or ""),
                "approval_present": bool(interrupt_metadata.get("approval_present")),
            }
        if isinstance(incident_case, dict):
            final_action = str(incident_case.get("final_action") or "").strip()
            if final_action or bool(incident_case.get("approval_required")):
                return {
                    "allowed": True,
                    "action_name": final_action,
                    "action_source": "incident_case",
                    "approval_present": bool(incident_case.get("approval_required")),
                }
        incident_state = dict(session.get("incident_state") or {})
        ranked_result = dict(incident_state.get("ranked_result") or {})
        primary = dict(ranked_result.get("primary") or {})
        recommended_action = str(primary.get("recommended_action") or "").strip()
        approval_proposals = list(incident_state.get("approval_proposals") or [])
        approved_actions = list(incident_state.get("approved_actions") or [])
        metadata = dict(incident_state.get("metadata") or {})
        approval_request = dict(metadata.get("approval_request") or {})
        proposal_action = (
            str(dict(approval_proposals[0]).get("action") or "").strip()
            if approval_proposals and isinstance(approval_proposals[0], dict)
            else ""
        )
        approved_action = (
            str(dict(approved_actions[0]).get("action") or "").strip()
            if approved_actions and isinstance(approved_actions[0], dict)
            else ""
        )
        approval_action = str(approval_request.get("action") or "").strip()
        action_name = recommended_action or proposal_action or approved_action or approval_action
        return {
            "allowed": bool(action_name),
            "action_name": action_name,
            "action_source": (
                "recommended_action"
                if recommended_action
                else "approval_proposal"
                if proposal_action
                else "approved_action"
                if approved_action
                else "approval_request"
                if approval_action
                else ""
            ),
            "approval_present": bool(proposal_action or approved_action or approval_action),
        }

    def _attach_observability(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if payload is None:
            return None
        observability_context = self.observability.current_trace_context()
        if not observability_context:
            return payload
        updated = dict(payload)
        diagnosis = updated.get("diagnosis")
        if isinstance(diagnosis, dict):
            diagnosis = dict(diagnosis)
            diagnosis["observability"] = observability_context
            updated["diagnosis"] = diagnosis
        else:
            updated["observability"] = observability_context
        return updated

    def _append_assistant_turn(self, session_id: str, *, response: dict[str, Any]) -> dict[str, Any]:
        return self.session_store.append_turn(
            ConversationTurn(
                session_id=session_id,
                role="assistant",
                content=str(response.get("message") or ""),
                structured_payload={
                    "status": response.get("status"),
                    "diagnosis": response.get("diagnosis"),
                    "approval_request": response.get("approval_request"),
                },
            )
        )

    def _create_checkpoint(
        self,
        *,
        session: dict[str, Any],
        stage: str,
        next_action: str | None,
        incident_state: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.checkpoint_store.create(
            {
                "session_id": str(session["session_id"]),
                "thread_id": str(session.get("thread_id") or session["session_id"]),
                "ticket_id": str(session.get("ticket_id") or session["session_id"]),
                "stage": stage,
                "next_action": next_action,
                "state_snapshot": incident_state,
                "metadata": dict(metadata or {}),
            }
        )

    def _get_pending_interrupt(self, session: dict[str, Any] | None) -> dict[str, Any] | None:
        if session is None:
            return None
        pending_interrupt_id = session.get("pending_interrupt_id")
        if not pending_interrupt_id:
            return None
        return self.interrupt_store.get(str(pending_interrupt_id))

    @staticmethod
    def _is_active_interrupt(interrupt: dict[str, Any] | None) -> bool:
        if not isinstance(interrupt, dict):
            return False
        interrupt_id = str(interrupt.get("interrupt_id") or "").strip()
        status = str(interrupt.get("status") or "").strip().lower()
        if not interrupt_id:
            return False
        return status not in {"answered", "cancelled", "expired", "rejected", "resolved", "closed"}

    @classmethod
    def _resolve_pending_interrupt(
        cls,
        *,
        final_status: str,
        approval_request: dict[str, Any] | None,
        clarification_interrupt: dict[str, Any] | None,
        feedback_interrupt: dict[str, Any] | None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        if final_status == "awaiting_approval":
            approval_id = str((approval_request or {}).get("interrupt_id") or "").strip()
            return (approval_id or None), None
        if final_status == "awaiting_clarification" and cls._is_active_interrupt(clarification_interrupt):
            return str(clarification_interrupt.get("interrupt_id") or ""), clarification_interrupt
        if final_status in {"completed", "failed"} and cls._is_active_interrupt(feedback_interrupt):
            return str(feedback_interrupt.get("interrupt_id") or ""), feedback_interrupt
        return None, None

    def _normalize_resume_answer(self, request: ConversationResumeRequest) -> dict[str, Any]:
        if request.answer_payload:
            return dict(request.answer_payload)
        if request.approved is not None and request.approver_id:
            payload = {
                "approved": request.approved,
                "approver_id": request.approver_id,
                "comment": request.comment,
            }
            if request.approval_id:
                payload["approval_id"] = request.approval_id
            return payload
        raise ValueError("resume request is missing answer payload")

    def _restore_incident_state_for_session(self, session: dict[str, Any]) -> dict[str, Any]:
        last_checkpoint_id = session.get("last_checkpoint_id")
        if last_checkpoint_id:
            checkpoint = self.checkpoint_store.get(str(last_checkpoint_id))
            if checkpoint is not None and isinstance(checkpoint.get("state_snapshot"), dict):
                return dict(checkpoint["state_snapshot"])
        latest = self.checkpoint_store.get_latest(str(session["session_id"]))
        if latest is not None and isinstance(latest.get("state_snapshot"), dict):
            return dict(latest["state_snapshot"])
        return dict(session.get("incident_state") or {})

    async def _resume_clarification(
        self,
        session: dict[str, Any],
        interrupt: dict[str, Any],
        answer_payload: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = str(session["session_id"])
        restored_state = self._restore_incident_state_for_session(session)
        metadata_payload = dict(interrupt.get("metadata", {}) or {})
        configured_fields = metadata_payload.get("clarification_fields")
        normalized_answers: dict[str, Any] = {}
        if isinstance(configured_fields, list) and configured_fields:
            if len(configured_fields) == 1 and "text" in answer_payload:
                first = configured_fields[0]
                field_name = str(first.get("name") or "")
                if field_name:
                    normalized_answers[field_name] = answer_payload.get("text")
            else:
                for field in configured_fields:
                    if not isinstance(field, dict):
                        continue
                    field_name = str(field.get("name") or "")
                    if field_name and field_name in answer_payload:
                        normalized_answers[field_name] = answer_payload.get(field_name)
        else:
            field_name = str(metadata_payload.get("field_name") or "")
            text = str(answer_payload.get("text") or "").strip()
            if field_name and text:
                normalized_answers[field_name] = text

        shared_context = dict(restored_state.get("shared_context") or {})
        for field_name, value in normalized_answers.items():
            if value in (None, ""):
                continue
            restored_state[field_name] = value
            shared_context[field_name] = value
        restored_state["shared_context"] = shared_context
        metadata = dict(restored_state.get("metadata") or {})
        clarification_answers = dict(metadata.get("clarification_answers") or {})
        clarification_answers[str(interrupt.get("interrupt_id") or "")] = {
            "raw_answer": answer_payload,
            "normalized_answers": normalized_answers,
        }
        metadata.pop("clarification_interrupt", None)
        metadata["clarification_answers"] = clarification_answers
        restored_state["metadata"] = metadata
        answered_summary = ", ".join(f"{key}={value}" for key, value in normalized_answers.items() if value not in (None, ""))
        self._append_process_entry(
            session_id=session_id,
            thread_id=str(session.get("thread_id") or session_id),
            ticket_id=str(session.get("ticket_id") or session_id),
            event_type="clarification_answered",
            stage="routing",
            source="orchestrator",
            summary=(f"用户补充了澄清信息：{answered_summary}" if answered_summary else "用户已回答澄清问题"),
            payload={
                "field_names": list(normalized_answers.keys()),
                "answer_payload": answer_payload,
                "normalized_answers": normalized_answers,
            },
            refs={
                "interrupt_id": interrupt.get("interrupt_id"),
                "checkpoint_id": session.get("last_checkpoint_id"),
            },
        )
        self.interrupt_store.answer(str(interrupt["interrupt_id"]), answer_payload=answer_payload)
        self.session_store.update_state(
            session_id,
            incident_state=restored_state,
            status="active",
            current_stage="routing",
            latest_approval_id=session.get("latest_approval_id"),
            pending_interrupt_id=None,
            last_checkpoint_id=session.get("last_checkpoint_id"),
            session_memory=self._merge_session_memory(
                session,
                key_entities={
                    "service": restored_state.get("service"),
                    "environment": restored_state.get("environment"),
                    "host_identifier": restored_state.get("host_identifier"),
                    "db_name": restored_state.get("db_name"),
                    "db_type": restored_state.get("db_type"),
                    "cluster": restored_state.get("cluster"),
                    "namespace": restored_state.get("namespace"),
                },
                clarification_answers={str(interrupt.get("interrupt_id") or ""): {"raw_answer": answer_payload, "normalized_answers": normalized_answers}},
                current_stage="routing",
                pending_interrupt=None,
            ),
        )
        ticket_request = TicketRequest(
            ticket_id=str(session.get("ticket_id") or session_id),
            user_id=str(session.get("user_id") or ""),
            message=str(restored_state.get("message") or "补充澄清信息"),
            service=restored_state.get("service"),
            environment=restored_state.get("environment"),
            host_identifier=restored_state.get("host_identifier"),
            db_name=restored_state.get("db_name"),
            db_type=restored_state.get("db_type"),
            cluster=str(restored_state.get("cluster") or "prod-shanghai-1"),
            namespace=str(restored_state.get("namespace") or "default"),
            channel=str(restored_state.get("channel") or "feishu"),
            mock_scenario=str(shared_context.get("mock_scenario") or "") or None,
            mock_scenarios=dict(shared_context.get("mock_scenarios") or {}),
            mock_tool_responses=dict(shared_context.get("mock_tool_responses") or {}),
            mock_world_state=dict(shared_context.get("mock_world_state") or {}),
        )
        return await self._run_ticket_message(
            ticket_request,
            session_id=session_id,
            thread_id=str(session.get("thread_id") or session_id),
            create_session=False,
            incident_state_override=restored_state,
            entrypoint="clarification_resume",
            user_turn_payload={
                "interrupt_id": interrupt.get("interrupt_id"),
                "interrupt_type": interrupt.get("type"),
                "answer_payload": answer_payload,
            },
            react_resume_target="supervisor_loop",
        )

    async def _resume_feedback(
        self,
        session: dict[str, Any],
        interrupt: dict[str, Any],
        answer_payload: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = str(session["session_id"])
        human_verified = bool(answer_payload.get("human_verified"))
        actual_root = str(answer_payload.get("actual_root_cause_hypothesis") or "")
        hypothesis_accuracy = {
            str(key): float(value)
            for key, value in dict(answer_payload.get("hypothesis_accuracy") or {}).items()
        }
        reopen_capability = self._feedback_reopen_capability(
            session,
            interrupt=interrupt,
            incident_case=self.incident_case_store.get_by_session_id(session_id),
        )
        if not human_verified and self._has_feedback_reopen_signal(answer_payload) and not reopen_capability["allowed"]:
            raise RuntimeError("reject and reopen is only allowed after actionable guidance or approval")
        if not human_verified and not self._has_feedback_reopen_signal(answer_payload):
            raise ValueError("reject and reopen requires new information such as actual_root_cause_hypothesis or comment")
        updated_case = self.incident_case_store.update_feedback(
            session_id,
            human_verified=human_verified,
            hypothesis_accuracy=hypothesis_accuracy,
            actual_root_cause_hypothesis=actual_root,
        )
        reopen_capability = self._feedback_reopen_capability(
            session,
            interrupt=interrupt,
            incident_case=updated_case,
        )
        if not human_verified:
            self._maybe_create_bad_case_candidate_from_feedback(
                session=session,
                answer_payload=answer_payload,
                incident_case=updated_case,
            )
        if updated_case is not None and self.case_vector_indexer.enabled:
            try:
                asyncio.get_running_loop().create_task(self.case_vector_indexer.index_case(updated_case))
            except RuntimeError:
                pass
        self.interrupt_store.answer(str(interrupt["interrupt_id"]), answer_payload=answer_payload)
        self._append_process_entry(
            session_id=session_id,
            thread_id=str(session.get("thread_id") or session_id),
            ticket_id=str(session.get("ticket_id") or session_id),
            event_type="manual_intervention",
            stage="feedback",
            source="orchestrator",
            summary="人工反馈已写回案例库。",
            payload={
                "human_verified": human_verified,
                "actual_root_cause_hypothesis": actual_root,
                "hypothesis_accuracy": hypothesis_accuracy,
            },
            refs={"interrupt_id": interrupt.get("interrupt_id")},
        )
        self._append_system_event(
            session_id=session_id,
            thread_id=str(session.get("thread_id") or session_id),
            ticket_id=str(session.get("ticket_id") or session_id),
            event_type="feedback.received",
            payload={
                "human_verified": human_verified,
                "actual_root_cause_hypothesis": actual_root,
            },
            metadata={"source": "orchestrator", "interrupt_id": interrupt.get("interrupt_id")},
        )
        if not human_verified and self._has_feedback_reopen_signal(answer_payload):
            restored_state = self._restore_incident_state_for_session(session)
            restored_metadata = dict(restored_state.get("metadata") or {})
            restored_metadata.pop("feedback_interrupt", None)
            restored_state["metadata"] = restored_metadata
            shared_context = dict(restored_state.get("shared_context") or {})
            shared_context["feedback_reopen"] = {
                "human_verified": human_verified,
                "actual_root_cause_hypothesis": actual_root,
                "comment": str(answer_payload.get("comment") or "").strip(),
                "action_name": reopen_capability["action_name"],
                "action_source": reopen_capability["action_source"],
            }
            restored_state["shared_context"] = shared_context
            active_session = self.session_store.update_state(
                session_id,
                incident_state=restored_state,
                status="active",
                current_stage="routing",
                latest_approval_id=session.get("latest_approval_id"),
                pending_interrupt_id=None,
                last_checkpoint_id=session.get("last_checkpoint_id"),
                session_memory=self._merge_session_memory(
                    session,
                    current_stage="routing",
                    pending_interrupt=None,
                ),
            )
            next_session = active_session or session
            feedback_message = self._build_feedback_reopen_message(answer_payload)
            feedback_event = self._build_session_event(
                source="feedback",
                event_type="correction",
                message=feedback_message,
                metadata={
                    "interrupt_id": interrupt.get("interrupt_id"),
                    "human_verified": False,
                    "actual_root_cause_hypothesis": actual_root,
                    "reason": "feedback_reopen",
                },
            )
            queued_session = self._queue_session_event(next_session, event=feedback_event)
            self._append_process_entry(
                session_id=session_id,
                thread_id=str(session.get("thread_id") or session_id),
                ticket_id=str(session.get("ticket_id") or session_id),
                event_type="manual_intervention",
                stage="routing",
                source="orchestrator",
                summary="人工反馈附带新信息，已重新开启诊断。",
                payload={
                    "actual_root_cause_hypothesis": actual_root,
                    "comment": str(answer_payload.get("comment") or ""),
                },
                refs={"interrupt_id": interrupt.get("interrupt_id")},
            )
            self._append_system_event(
                session_id=session_id,
                thread_id=str(session.get("thread_id") or session_id),
                ticket_id=str(session.get("ticket_id") or session_id),
                event_type="feedback.reopened",
                payload={
                    "actual_root_cause_hypothesis": actual_root,
                    "comment": str(answer_payload.get("comment") or ""),
                },
                metadata={"source": "orchestrator", "interrupt_id": interrupt.get("interrupt_id")},
            )
            ticket_request = TicketRequest(
                ticket_id=str(session.get("ticket_id") or session_id),
                user_id=str(session.get("user_id") or ""),
                message=feedback_message,
                service=restored_state.get("service"),
                environment=restored_state.get("environment"),
                host_identifier=restored_state.get("host_identifier"),
                db_name=restored_state.get("db_name"),
                db_type=restored_state.get("db_type"),
                cluster=str(restored_state.get("cluster") or "prod-shanghai-1"),
                namespace=str(restored_state.get("namespace") or "default"),
                channel=str(restored_state.get("channel") or "feishu"),
                mock_scenario=str(shared_context.get("mock_scenario") or "") or None,
                mock_scenarios=dict(shared_context.get("mock_scenarios") or {}),
                mock_tool_responses=dict(shared_context.get("mock_tool_responses") or {}),
                mock_world_state=dict(shared_context.get("mock_world_state") or {}),
            )
            reopened = await self._run_ticket_message(
                ticket_request,
                session_id=session_id,
                thread_id=str(session.get("thread_id") or session_id),
                create_session=False,
                incident_state_override=restored_state,
                entrypoint="feedback_reopen",
                user_turn_payload={
                    "interrupt_id": interrupt.get("interrupt_id"),
                    "interrupt_type": interrupt.get("type"),
                    "answer_payload": answer_payload,
                    "message_event": {
                        "event_type": "correction",
                        "source": "feedback",
                    },
                },
            )
            reopened_session = reopened.get("session")
            if isinstance(reopened_session, dict):
                reopened["session"] = self._mark_session_events_consumed(
                    reopened_session,
                    event_ids=[str(feedback_event.get("event_id") or "")],
                )
            diagnosis = dict(reopened.get("diagnosis") or {})
            diagnosis["feedback"] = updated_case
            diagnosis["feedback_reopened"] = True
            reopened["diagnosis"] = diagnosis
            return reopened
        updated_session = self.session_store.update_state(
            session_id,
            incident_state=session.get("incident_state") or {},
            status="completed",
            current_stage="finalize",
            latest_approval_id=session.get("latest_approval_id"),
            pending_interrupt_id=None,
            last_checkpoint_id=session.get("last_checkpoint_id"),
            session_memory=self._merge_session_memory(
                session,
                current_stage="finalize",
                pending_interrupt=None,
            ),
        )
        message = "已记录人工反馈，本次诊断结果已补充到案例库。"
        assistant_turn = self._append_assistant_turn(
            session_id,
            response={"status": "completed", "message": message, "diagnosis": {"feedback": updated_case}},
        )
        return {
            "session": updated_session,
            "status": "completed",
            "message": message,
            "diagnosis": {"feedback": updated_case},
            "approval_request": None,
            "pending_interrupt": None,
            "assistant_turn": assistant_turn,
        }

    def _merge_session_memory(
        self,
        session: dict[str, Any],
        *,
        original_user_message: str | None = None,
        current_intent: dict[str, Any] | None = None,
        key_entities: dict[str, Any] | None = None,
        clarification_answers: dict[str, Any] | None = None,
        current_intent_history: list[dict[str, Any]] | None = None,
        pending_approval: dict[str, Any] | None = None,
        current_stage: str | None = None,
        pending_interrupt: dict[str, Any] | None = None,
        session_event_queue: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        memory = dict(session.get("session_memory") or {})
        if original_user_message is not None:
            memory["original_user_message"] = original_user_message
        if current_intent is not None:
            memory["current_intent"] = current_intent
        key_entities_payload = dict(memory.get("key_entities") or {})
        if key_entities:
            key_entities_payload.update({k: v for k, v in key_entities.items() if v is not None})
        memory["key_entities"] = key_entities_payload
        clarification_payload = dict(memory.get("clarification_answers") or {})
        if clarification_answers:
            clarification_payload.update(clarification_answers)
        memory["clarification_answers"] = clarification_payload
        if current_intent_history is not None:
            memory["current_intent_history"] = list(current_intent_history)
        memory["pending_approval"] = pending_approval if pending_approval is not None else memory.get("pending_approval")
        if current_stage is not None:
            memory["current_stage"] = current_stage
        memory["pending_interrupt"] = pending_interrupt if pending_interrupt is not None else memory.get("pending_interrupt")
        if session_event_queue is not None:
            memory["session_event_queue"] = [dict(item) for item in session_event_queue[-20:] if isinstance(item, dict)]
        return memory

    @staticmethod
    def _resolve_current_agent(
        session: dict[str, Any] | None,
        *,
        incident_state: dict[str, Any] | None = None,
        current_intent: dict[str, Any] | None = None,
    ) -> str | None:
        if current_intent and current_intent.get("current_node"):
            return str(current_intent["current_node"])
        if current_intent and current_intent.get("intent"):
            return str(current_intent["intent"])
        if current_intent and current_intent.get("agent_name"):
            return str(current_intent["agent_name"])
        routing = dict((incident_state or {}).get("routing") or {})
        if routing.get("intent"):
            return str(routing["intent"])
        if routing.get("agent_name"):
            return str(routing["agent_name"])
        existing_memory = dict((session or {}).get("session_memory") or {})
        existing_intent = dict(existing_memory.get("current_intent") or {})
        if existing_intent.get("current_node"):
            return str(existing_intent["current_node"])
        if existing_intent.get("intent"):
            return str(existing_intent["intent"])
        if existing_intent.get("agent_name"):
            return str(existing_intent["agent_name"])
        current_agent = (session or {}).get("current_agent")
        return str(current_agent) if current_agent else None

    def _build_conversation_detail(self, session_id: str) -> dict[str, Any] | None:
        session = self.session_service.get_session(session_id)
        if session is None:
            return None
        pending_interrupt = self._get_pending_interrupt(session)
        return {
            "session": session,
            "turns": self.session_service.list_turns(session_id),
            "pending_interrupt": pending_interrupt,
        }

    def _build_clarification_schema(self, missing_fields: list[dict[str, str]]) -> dict[str, Any]:
        properties = {
            str(field["name"]): {
                "type": "string",
                "title": str(field["label"]),
                "description": str(field["description"]),
            }
            for field in missing_fields
        }
        return {
            "type": "object",
            "properties": properties,
            "required": [str(field["name"]) for field in missing_fields if bool(field.get("required", True))],
        }

    async def _build_generic_guidance(self, request: TicketRequest, *, issue_type: str) -> str:
        try:
            bundle = await self.knowledge_service.retrieve_query(
                query=request.message,
                service=str(request.service or ""),
                top_k=2,
            )
            answer = await self.smart_router.generate_direct_answer(request, rag_context=bundle)
            if str(answer or "").strip():
                return str(answer).strip()
        except Exception:
            pass
        defaults = {
            "host": "可以先检查实例状态、控制台启动日志、最近变更、系统盘与网络挂载情况。",
            "database": "可以先检查数据库实例状态、连接数、慢查询、复制延迟和最近变更。",
            "service": "可以先确认是否存在发布变更、资源异常、网络抖动或上游依赖问题。",
        }
        return defaults.get(issue_type, "可以先从最近变更、运行状态和错误信息入手做初步排查。")

    def _build_clarification_response(
        self,
        *,
        session: dict[str, Any],
        request: TicketRequest,
        interrupt: dict[str, Any],
        guidance: str = "",
    ) -> dict[str, Any]:
        followup = str(interrupt.get("question") or "请先补充关键信息后再继续分析。")
        message = (
            f"{guidance}\n\n{followup}".strip()
            if guidance
            else followup
        )
        assistant_turn = self._append_assistant_turn(
            str(session["session_id"]),
            response={"status": "awaiting_clarification", "message": message, "diagnosis": {"clarification": interrupt}},
        )
        return {
            "session": session,
            "status": "awaiting_clarification",
            "message": message,
            "diagnosis": {"clarification": interrupt},
            "approval_request": None,
            "pending_interrupt": interrupt,
            "assistant_turn": assistant_turn,
        }

    async def _retrieve_rag_context(self, request: TicketRequest):
        return await self.knowledge_service.retrieve_for_request(request)

    def _assemble_execution_context(
        self,
        *,
        request: TicketRequest,
        session: dict[str, Any],
        incident_state: dict[str, Any],
        entrypoint: str,
    ):
        return self.context_assembler.assemble(
            request=request,
            session=session,
            pending_interrupt=self._get_pending_interrupt(session),
            recent_turns=self.session_service.list_turns(str(session["session_id"]), limit=5),
            incident_state=incident_state,
            process_memory_summary=self._summarize_process_memory(str(session["session_id"])),
            incident_case_summary=self._summarize_incident_cases(
                service=str(incident_state.get("service") or request.service or "") or None,
                session_id=str(session["session_id"]),
            ),
            entrypoint=entrypoint,
        )

    def _infer_tool_domains_from_message(self, message: str) -> list[str]:
        lowered = str(message or "").lower()
        matched: list[str] = []
        if any(token in lowered for token in ("pod", "k8s", "oom", "container", "重启", "副本", "资源")):
            matched.append("k8s")
        if any(token in lowered for token in ("deploy", "release", "pipeline", "rollback", "发布", "变更", "构建")):
            matched.append("cicd")
        if any(token in lowered for token in ("dns", "ingress", "gateway", "vpc", "timeout", "502", "网络")):
            matched.append("network")
        if any(token in lowered for token in ("mysql", "postgres", "数据库", "slow query", "deadlock", "连接池")):
            matched.append("db")
        if any(token in lowered for token in ("alert", "slo", "burn rate", "日志", "监控", "告警")):
            matched.append("monitor")
        return matched

    def _detect_topic_shift_for_session(
        self,
        *,
        session: dict[str, Any],
        current_message: str,
    ) -> dict[str, Any]:
        previous_incident_state = dict(session.get("incident_state") or {})
        previous_snapshot = dict(previous_incident_state.get("context_snapshot") or {})
        previous_categories = list(previous_snapshot.get("matched_tool_domains") or [])
        current_categories = self._infer_tool_domains_from_message(current_message)
        return self.topic_shift_detector.detect(
            previous_message=str(previous_incident_state.get("message") or ""),
            current_message=current_message,
            previous_categories=previous_categories,
            current_categories=current_categories,
        )

    def _supersede_pending_interrupt_for_new_message(
        self,
        *,
        session: dict[str, Any],
        pending_interrupt: dict[str, Any],
        request: ConversationMessageRequest,
        message_event: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = str(session["session_id"])
        interrupt_type = str(pending_interrupt.get("type") or "")
        topic_shift = dict(message_event.get("topic_shift") or {})
        event_type = str(message_event.get("event_type") or "")
        should_supersede = interrupt_type == "feedback" or (
            interrupt_type == "approval" and event_type in {"supplement", "correction", "new_issue"}
        )
        if not should_supersede:
            raise RuntimeError("conversation is awaiting resume; use the resume endpoint")

        if interrupt_type == "approval":
            approval_id = str(pending_interrupt.get("metadata", {}).get("approval_id") or "")
            if approval_id:
                self.approval_store.cancel(
                    approval_id,
                    actor_id="system",
                    comment="superseded by topic shift",
                )
                self._append_system_event(
                    session_id=session_id,
                    thread_id=str(session.get("thread_id") or session_id),
                    ticket_id=str(session.get("ticket_id") or session_id),
                    event_type="approval.superseded",
                    payload={"approval_id": approval_id, "new_message": request.message},
                    metadata={"source": "orchestrator"},
                )
        self.interrupt_store.cancel(
            str(pending_interrupt["interrupt_id"]),
            answer_payload={"superseded_by_message": request.message},
        )
        self._append_system_event(
            session_id=session_id,
            thread_id=str(session.get("thread_id") or session_id),
            ticket_id=str(session.get("ticket_id") or session_id),
            event_type="interrupt.superseded",
            payload={
                "interrupt_id": pending_interrupt.get("interrupt_id"),
                "interrupt_type": interrupt_type,
                "message_event_type": event_type,
                "new_message": request.message,
                "topic_shift_detected": bool(topic_shift.get("topic_shift_detected")),
            },
            metadata={"source": "orchestrator"},
        )
        updated_session = self.session_service.update_session_state(
            session_id,
            incident_state=dict(session.get("incident_state") or {}),
            status="active",
            current_stage="routing",
            current_agent=session.get("current_agent"),
            latest_approval_id=session.get("latest_approval_id") if interrupt_type != "approval" else None,
            pending_interrupt_id=None,
            last_checkpoint_id=session.get("last_checkpoint_id"),
            session_memory=self._merge_session_memory(
                session,
                current_stage="routing",
                pending_approval=None if interrupt_type == "approval" else dict((session.get("session_memory") or {}).get("pending_approval") or {}),
                pending_interrupt=None,
            ),
        )
        return updated_session or session

    async def _run_ticket_message(
        self,
        request: TicketRequest,
        *,
        session_id: str,
        thread_id: str,
        create_session: bool,
        incident_state_override: dict[str, Any] | None = None,
        entrypoint: str = "ticket_message",
        user_turn_payload: dict[str, Any] | None = None,
        react_resume_target: str | None = None,
    ) -> dict[str, Any]:
        self.observability.update_current_trace(
            name=entrypoint,
            user_id=request.user_id,
            session_id=session_id,
            input={
                "ticket_id": request.ticket_id,
                "message": request.message,
                "service": request.service,
                "environment": request.environment,
                "host_identifier": request.host_identifier,
                "cluster": request.cluster,
                "namespace": request.namespace,
            },
            metadata={"thread_id": thread_id, "entrypoint": entrypoint},
        )
        incident_state = None
        if incident_state_override is not None:
            incident_state = IncidentState.model_validate(incident_state_override)
        graph_input = build_react_graph_input(
            request,
            session_id=session_id,
            thread_id=thread_id,
            incident_state=incident_state,
            resume_target=react_resume_target,
        )
        incident_state = graph_input["incident_state"]
        slot_resolution = resolve_slots(
            message=request.message,
            service=request.service,
            environment=request.environment,
            cluster=request.cluster,
            namespace=request.namespace,
            host_identifier=request.host_identifier,
            db_name=request.db_name,
            db_type=request.db_type,
        )
        if slot_resolution.resolved.get("service"):
            incident_state.service = str(slot_resolution.resolved.get("service") or "")
        if slot_resolution.resolved.get("environment"):
            incident_state.environment = str(slot_resolution.resolved.get("environment") or "") or None
        if slot_resolution.resolved.get("host_identifier"):
            incident_state.host_identifier = str(slot_resolution.resolved.get("host_identifier") or "") or None
        if slot_resolution.resolved.get("db_name"):
            incident_state.db_name = str(slot_resolution.resolved.get("db_name") or "") or None
        if slot_resolution.resolved.get("db_type"):
            incident_state.db_type = str(slot_resolution.resolved.get("db_type") or "") or None
        if slot_resolution.resolved.get("cluster"):
            incident_state.cluster = str(slot_resolution.resolved.get("cluster") or "") or incident_state.cluster
        if slot_resolution.resolved.get("namespace"):
            incident_state.namespace = str(slot_resolution.resolved.get("namespace") or "") or incident_state.namespace
        clarification_fields = [
            {
                "name": field.name,
                "label": field.label,
                "description": field.description,
                "required": field.required,
                "inferred_value": field.inferred_value,
                "source": field.source,
            }
            for field in [*slot_resolution.missing_fields, *slot_resolution.inferred_fields]
        ]
        if slot_resolution.inferred_fields:
            clarification_fields.append(
                {
                    "name": "confirm_inferred_context",
                    "label": "确认推测信息",
                    "description": "如果当前推测信息正确，请填写 true；如果不正确，请直接覆盖对应字段。",
                    "required": False,
                }
            )
        if slot_resolution.needs_clarification:
            incident_state.shared_context.update(
                {
                    "service": incident_state.service or "",
                    "environment": incident_state.environment or "",
                    "host_identifier": incident_state.host_identifier or "",
                    "db_name": incident_state.db_name or "",
                    "db_type": incident_state.db_type or "",
                }
            )
            if create_session:
                self.session_service.create_initial_session(
                    session_id=session_id,
                    thread_id=thread_id,
                    request=request,
                    incident_state=incident_state,
                    session_memory={
                        "original_user_message": request.message,
                        "current_intent": {},
                        "key_entities": {
                            "service": incident_state.service,
                            "environment": incident_state.environment,
                            "host_identifier": incident_state.host_identifier,
                            "db_name": incident_state.db_name,
                            "db_type": incident_state.db_type,
                            "cluster": request.cluster,
                            "namespace": request.namespace,
                        },
                        "clarification_answers": {},
                        "pending_approval": None,
                        "current_stage": "awaiting_clarification",
                        "pending_interrupt": None,
                        "session_event_queue": [],
                    },
                )
            current_session = self.session_service.get_session(session_id)
            if current_session is None:
                raise ValueError("session not found during clarification setup")
            self._append_user_turn(
                session_id,
                content=request.message,
                structured_payload=user_turn_payload
                or {
                    "ticket_id": request.ticket_id,
                    "user_id": request.user_id,
                    "service": request.service,
                    "environment": request.environment,
                    "host_identifier": request.host_identifier,
                    "db_name": request.db_name,
                    "db_type": request.db_type,
                    "cluster": request.cluster,
                    "namespace": request.namespace,
                    "channel": request.channel,
                },
            )
            self._append_system_event(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=request.ticket_id,
                event_type="message.received",
                payload={
                    "entrypoint": entrypoint,
                    "message": request.message,
                    "service": request.service,
                    "environment": request.environment,
                    "host_identifier": request.host_identifier,
                    "db_name": request.db_name,
                    "db_type": request.db_type,
                },
                metadata={"source": "orchestrator"},
            )
            guidance = await self._build_generic_guidance(request, issue_type=slot_resolution.issue_type)
            if slot_resolution.inferred_fields:
                inferred_lines = "；".join(
                    f"{field.label}={field.inferred_value}（来源 {field.source}）" for field in slot_resolution.inferred_fields
                )
                guidance = f"{guidance}\n\n我当前推测：{inferred_lines}。请确认或直接覆盖。".strip()
            if slot_resolution.missing_fields:
                required_labels = "、".join(field.label for field in slot_resolution.missing_fields)
                guidance = f"{guidance}\n\n如果您能补充 {required_labels}，我可以继续深入排查。".strip()
            interrupt = self.interrupt_store.create_clarification_interrupt(
                session_id=session_id,
                ticket_id=request.ticket_id,
                reason="missing_required_fields",
                question="请确认或补充继续诊断所需的关键信息。",
                expected_input_schema=self._build_clarification_schema(clarification_fields),
                metadata={
                    "clarification_fields": clarification_fields,
                    "issue_type": slot_resolution.issue_type,
                },
            )
            incident_state.metadata["clarification_interrupt"] = interrupt
            self._append_process_entry(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=request.ticket_id,
                event_type="clarification_created",
                stage="routing",
                source="orchestrator",
                summary=(
                    "缺少或推测了继续诊断所需信息，已触发澄清："
                    + ", ".join(item["name"] for item in clarification_fields)
                ),
                payload={
                    "missing_fields": [field.name for field in slot_resolution.missing_fields],
                    "inferred_fields": [field.name for field in slot_resolution.inferred_fields],
                    "issue_type": slot_resolution.issue_type,
                },
                refs={"interrupt_id": interrupt.get("interrupt_id")},
            )
            self._append_system_event(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=request.ticket_id,
                event_type="clarification.requested",
                payload={
                    "missing_fields": [field.name for field in slot_resolution.missing_fields],
                    "inferred_fields": [field.name for field in slot_resolution.inferred_fields],
                    "issue_type": slot_resolution.issue_type,
                },
                metadata={"source": "orchestrator", "interrupt_id": interrupt.get("interrupt_id")},
            )
            session = self.session_service.update_session_state(
                session_id,
                incident_state=incident_state.model_dump(),
                status="awaiting_clarification",
                current_stage="awaiting_clarification",
                current_agent="clarification",
                latest_approval_id=current_session.get("latest_approval_id"),
                pending_interrupt_id=str(interrupt.get("interrupt_id") or ""),
                last_checkpoint_id=current_session.get("last_checkpoint_id"),
                session_memory=self._merge_session_memory(
                    current_session,
                    key_entities={
                        "service": incident_state.service,
                        "environment": incident_state.environment,
                        "host_identifier": incident_state.host_identifier,
                        "db_name": incident_state.db_name,
                        "db_type": incident_state.db_type,
                        "cluster": incident_state.cluster,
                        "namespace": incident_state.namespace,
                    },
                    current_stage="awaiting_clarification",
                    pending_interrupt={
                        "interrupt_id": interrupt.get("interrupt_id"),
                        "type": "clarification",
                        "reason": interrupt.get("reason"),
                        "question": interrupt.get("question"),
                    },
                ),
            )
            return self._build_clarification_response(
                session=session or current_session,
                request=request,
                interrupt=interrupt,
                guidance=guidance,
            )
        incident_state.rag_context = await self._retrieve_rag_context(request)
        incident_state.shared_context["rag_context"] = incident_state.rag_context.model_dump()
        if create_session:
            self.session_service.create_initial_session(
                session_id=session_id,
                thread_id=thread_id,
                request=request,
                incident_state=incident_state,
                session_memory={
                    "original_user_message": request.message,
                    "current_intent": {},
                    "key_entities": {
                        "service": request.service,
                        "environment": request.environment,
                        "host_identifier": request.host_identifier,
                        "cluster": request.cluster,
                        "namespace": request.namespace,
                    },
                    "clarification_answers": {},
                    "pending_approval": None,
                    "current_stage": "ingest",
                    "pending_interrupt": None,
                    "session_event_queue": [],
                },
            )
            self._append_system_event(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=request.ticket_id,
                event_type="conversation.created",
                payload={
                    "entrypoint": entrypoint,
                    "user_id": request.user_id,
                    "message": request.message,
                },
                metadata={"source": "orchestrator"},
            )
        current_session = self.session_service.get_session(session_id)
        if current_session is None:
            raise ValueError("session not found during context assembly")
        topic_shift = None
        if not create_session:
            previous_incident_state = dict(current_session.get("incident_state") or {})
            previous_snapshot = dict(previous_incident_state.get("context_snapshot") or {})
            previous_categories = list(previous_snapshot.get("matched_tool_domains") or [])
            current_categories = self._infer_tool_domains_from_message(request.message)
            topic_shift = self.topic_shift_detector.detect(
                previous_message=str(previous_incident_state.get("message") or ""),
                current_message=request.message,
                previous_categories=previous_categories,
                current_categories=current_categories,
            )
            incident_state.shared_context["current_intent_history"] = list(
                dict(current_session.get("session_memory") or {}).get("current_intent_history") or []
            )
            incident_state.shared_context["topic_shift"] = topic_shift
            incident_state.shared_context["incremental_tool_domains"] = list(topic_shift.get("incremental_tool_domains") or [])
        graph_input["execution_context"] = self._assemble_execution_context(
            request=request,
            session=current_session,
            incident_state=incident_state.model_dump(),
            entrypoint=entrypoint,
        )
        self._append_system_event(
            session_id=session_id,
            thread_id=thread_id,
            ticket_id=request.ticket_id,
            event_type="knowledge.retrieved",
            payload={
                "query": incident_state.rag_context.query if incident_state.rag_context is not None else request.message,
                "query_type": incident_state.rag_context.query_type if incident_state.rag_context is not None else "unknown",
                "hit_count": len(incident_state.rag_context.hits if incident_state.rag_context is not None else []),
                "context_count": len(incident_state.rag_context.context if incident_state.rag_context is not None else []),
                "citations": list(incident_state.rag_context.citations if incident_state.rag_context is not None else []),
            },
            metadata={"source": "orchestrator"},
        )
        self._append_user_turn(
            session_id,
            content=request.message,
            structured_payload=user_turn_payload
            or {
                "ticket_id": request.ticket_id,
                "user_id": request.user_id,
                "service": request.service,
                "cluster": request.cluster,
                "namespace": request.namespace,
                "channel": request.channel,
            },
        )
        self._append_system_event(
            session_id=session_id,
            thread_id=thread_id,
            ticket_id=request.ticket_id,
            event_type="message.received",
            payload={
                "entrypoint": entrypoint,
                "message": request.message,
                "service": request.service,
                "environment": request.environment,
                "host_identifier": request.host_identifier,
            },
            metadata={"source": "orchestrator"},
        )
        try:
            state = await self.ticket_graph.ainvoke(graph_input)
        except Exception:
            self.session_service.update_session_status(
                session_id,
                status="failed",
                current_stage="finalize",
                current_agent=self._resolve_current_agent(current_session, incident_state=incident_state.model_dump()),
            )
            raise
        response = extract_react_graph_response(state)
        final_incident_state = state.get("incident_state") or incident_state
        latest_approval_id = None
        approval_request = state.get("approval_request")
        if isinstance(approval_request, dict):
            latest_approval_id = approval_request.get("approval_id")
        route_decision = state.get("route_decision")
        current_intent = {
            "intent": getattr(route_decision, "intent", None) if route_decision is not None else None,
            "route_source": getattr(route_decision, "route_source", None) if route_decision is not None else None,
            "matched_signals": list(getattr(route_decision, "matched_signals", []) or []) if route_decision is not None else [],
            "current_node": getattr(route_decision, "intent", None) if route_decision is not None else None,
        }
        current_agent = self._resolve_current_agent(
            current_session,
            incident_state=final_incident_state.model_dump(),
            current_intent=current_intent,
        )
        final_status = response.get("status") or "completed"
        if final_status == "awaiting_approval":
            final_stage = "awaiting_approval"
        elif final_status == "awaiting_clarification":
            final_stage = "awaiting_clarification"
        else:
            final_status = "completed"
            final_stage = "finalize"
        clarification_interrupt = final_incident_state.metadata.get("clarification_interrupt") if hasattr(final_incident_state, "metadata") else None
        feedback_interrupt = final_incident_state.metadata.get("feedback_interrupt") if hasattr(final_incident_state, "metadata") else None
        pending_interrupt_id, pending_interrupt_payload = self._resolve_pending_interrupt(
            final_status=final_status,
            approval_request=approval_request if isinstance(approval_request, dict) else None,
            clarification_interrupt=clarification_interrupt if isinstance(clarification_interrupt, dict) else None,
            feedback_interrupt=feedback_interrupt if isinstance(feedback_interrupt, dict) else None,
        )
        session = self.session_service.update_session_state(
            session_id,
            incident_state=final_incident_state.model_dump(),
            status=final_status,
            current_stage=final_stage,
            latest_approval_id=latest_approval_id,
            pending_interrupt_id=pending_interrupt_id,
            session_memory=self._merge_session_memory(
                current_session,
                current_intent=current_intent,
                key_entities={
                    "service": final_incident_state.service,
                    "environment": getattr(final_incident_state, "environment", None),
                    "host_identifier": getattr(final_incident_state, "host_identifier", None),
                    "db_name": getattr(final_incident_state, "db_name", None),
                    "db_type": getattr(final_incident_state, "db_type", None),
                    "cluster": final_incident_state.cluster,
                    "namespace": final_incident_state.namespace,
                },
                pending_approval=(
                    {
                        "approval_id": latest_approval_id,
                        "action": approval_request.get("action"),
                        "risk": approval_request.get("risk"),
                        "reason": approval_request.get("reason"),
                    }
                    if isinstance(approval_request, dict)
                    else None
                ),
                current_stage=final_stage,
                pending_interrupt=(
                    {
                        "interrupt_id": pending_interrupt_id,
                        "type": (
                            "approval"
                            if final_status == "awaiting_approval"
                            else "clarification"
                            if final_status == "awaiting_clarification"
                            else "feedback"
                            if isinstance(pending_interrupt_payload, dict) and pending_interrupt_payload.get("type") == "feedback"
                            else None
                        ),
                        "reason": (
                            approval_request.get("reason")
                            if isinstance(approval_request, dict)
                            else pending_interrupt_payload.get("reason") if isinstance(pending_interrupt_payload, dict) else None
                        ),
                        "question": (
                            "是否批准执行该高风险动作？"
                            if final_status == "awaiting_approval"
                            else pending_interrupt_payload.get("question") if isinstance(pending_interrupt_payload, dict) else None
                        ),
                    }
                    if pending_interrupt_id
                    else None
                ),
            ),
        )
        if topic_shift is not None and session is not None:
            current_intent_history = list(dict(session.get("session_memory") or {}).get("current_intent_history") or [])
            current_intent_history.append(
                {
                    "message": request.message,
                    "topic_shift_detected": bool(topic_shift.get("topic_shift_detected")),
                    "incremental_tool_domains": list(topic_shift.get("incremental_tool_domains") or []),
                }
            )
            session = self.session_service.update_session_state(
                session_id,
                incident_state=final_incident_state.model_dump(),
                status=final_status,
                current_stage=final_stage,
                session_memory=self._merge_session_memory(
                    session,
                    current_intent=current_intent,
                    key_entities={
                        "service": final_incident_state.service,
                        "environment": getattr(final_incident_state, "environment", None),
                        "host_identifier": getattr(final_incident_state, "host_identifier", None),
                        "db_name": getattr(final_incident_state, "db_name", None),
                        "db_type": getattr(final_incident_state, "db_type", None),
                        "cluster": final_incident_state.cluster,
                        "namespace": final_incident_state.namespace,
                    },
                    pending_approval=(
                        {
                            "approval_id": latest_approval_id,
                            "action": approval_request.get("action"),
                            "risk": approval_request.get("risk"),
                            "reason": approval_request.get("reason"),
                        }
                        if isinstance(approval_request, dict)
                        else None
                    ),
                    current_stage=final_stage,
                    current_intent_history=current_intent_history,
                    pending_interrupt=(
                        {
                            "interrupt_id": pending_interrupt_id,
                            "type": (
                                "approval"
                                if final_status == "awaiting_approval"
                                else "clarification"
                                if final_status == "awaiting_clarification"
                                else "feedback"
                                if isinstance(pending_interrupt_payload, dict) and pending_interrupt_payload.get("type") == "feedback"
                                else None
                            ),
                            "reason": (
                                approval_request.get("reason")
                                if isinstance(approval_request, dict)
                                else pending_interrupt_payload.get("reason") if isinstance(pending_interrupt_payload, dict) else None
                            ),
                            "question": (
                                "是否批准执行该高风险动作？"
                                if final_status == "awaiting_approval"
                                else pending_interrupt_payload.get("question") if isinstance(pending_interrupt_payload, dict) else None
                            ),
                        }
                        if pending_interrupt_id
                        else None
                    ),
                ),
            )
        checkpoint = None
        if session is not None:
            checkpoint = self._create_checkpoint(
                session=session,
                stage=(
                    "awaiting_approval"
                    if final_status == "awaiting_approval"
                    else "awaiting_clarification"
                    if final_status == "awaiting_clarification"
                    else "finalize"
                ),
                next_action=(
                    "wait_for_approval"
                    if final_status == "awaiting_approval"
                    else "wait_for_clarification"
                    if final_status == "awaiting_clarification"
                    else "complete"
                ),
                incident_state=final_incident_state.model_dump(),
                metadata={
                    "source": "ticket_message",
                    "response_status": response.get("status"),
                    "approval_id": latest_approval_id,
                    "interrupt_id": pending_interrupt_id,
                },
            )
            self._append_process_entry(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=request.ticket_id,
                event_type="run_summary",
                stage=final_stage,
                source="orchestrator",
                summary=(
                    f"本轮处理完成，当前状态为 {final_status}"
                    if final_status != "completed"
                    else "本轮处理已完成并产出最终回复"
                ),
                payload={
                    "response_status": response.get("status"),
                    "message": response.get("message"),
                    "diagnosis_summary": (response.get("diagnosis") or {}).get("summary") if isinstance(response.get("diagnosis"), dict) else None,
                    "route_intent": current_intent.get("intent"),
                    "routing": (response.get("diagnosis") or {}).get("routing") if isinstance(response.get("diagnosis"), dict) else None,
                    "approval_request": approval_request if isinstance(approval_request, dict) else None,
                    "pending_interrupt": pending_interrupt_payload if isinstance(pending_interrupt_payload, dict) else None,
                },
                refs={
                    "checkpoint_id": checkpoint.get("checkpoint_id"),
                    "approval_id": latest_approval_id,
                    "interrupt_id": pending_interrupt_id,
                },
            )
            session = self.session_service.update_session_state(
                session_id,
                incident_state=final_incident_state.model_dump(),
                status=final_status,
                current_stage=final_stage,
                current_agent=current_agent,
                latest_approval_id=latest_approval_id,
                pending_interrupt_id=pending_interrupt_id,
                last_checkpoint_id=checkpoint["checkpoint_id"],
            )
            saved_case = None
            if session is not None and final_status in {"completed", "failed"}:
                saved_case = self._upsert_incident_case(
                    session=session,
                    response=response,
                    incident_state=final_incident_state.model_dump(),
                )
                self._append_system_event(
                    session_id=session_id,
                    thread_id=thread_id,
                    ticket_id=request.ticket_id,
                    event_type="conversation.closed",
                    payload={
                        "status": final_status,
                        "message": response.get("message"),
                    },
                    metadata={
                        "source": "orchestrator",
                        "checkpoint_id": checkpoint.get("checkpoint_id") if checkpoint is not None else None,
                    },
                )
        assistant_turn = self._append_assistant_turn(session_id, response=response)
        if session is not None and final_status in {"completed", "failed"}:
            self._maybe_create_bad_case_candidate_from_run(
                session=session,
                response=response,
                incident_state=final_incident_state.model_dump(),
                incident_case=saved_case,
            )
        payload = {
            "session": session,
            "status": response.get("status"),
            "message": response.get("message"),
            "diagnosis": response.get("diagnosis"),
            "approval_request": response.get("approval_request"),
            "pending_interrupt": (
                self.interrupt_store.get(str(pending_interrupt_id))
                if pending_interrupt_id
                else pending_interrupt_payload
            ),
            "assistant_turn": assistant_turn,
        }
        self.observability.update_current_trace(
            output={"status": payload.get("status"), "message": payload.get("message")},
            metadata={"current_agent": current_agent, "final_stage": final_stage},
        )
        return self._attach_observability(payload) or payload

    async def start_conversation(self, request: ConversationCreateRequest) -> dict[str, Any]:
        ticket_id = request.ticket_id or f"CONV-{uuid4().hex[:12]}"
        ticket_request = TicketRequest(
            ticket_id=ticket_id,
            user_id=request.user_id,
            message=request.message,
            service=request.service or infer_service_name(request.message),
            environment=request.environment,
            host_identifier=request.host_identifier or infer_host_identifier(request.message),
            db_name=request.db_name,
            db_type=request.db_type,
            cluster=request.cluster,
            namespace=request.namespace,
            channel=request.channel,
            mock_scenario=request.mock_scenario,
            mock_scenarios=dict(request.mock_scenarios or {}),
            mock_tool_responses=dict(request.mock_tool_responses or {}),
            mock_world_state=dict(request.mock_world_state or {}),
        )
        with self.observability.start_span(
            name="orchestrator.start_conversation",
            as_type="span",
            input={"ticket_id": ticket_id, "message": request.message, "service": request.service},
            metadata={"entrypoint": "conversation_create"},
        ):
            return await self._run_ticket_message(
                ticket_request,
                session_id=ticket_id,
                thread_id=ticket_id,
                create_session=True,
                entrypoint="conversation_create",
            )

    async def post_message(self, session_id: str, request: ConversationMessageRequest) -> dict[str, Any]:
        session = self.session_service.get_session(session_id)
        if session is None:
            raise ValueError("session not found")
        pending_interrupt = self._get_pending_interrupt(session)
        message_event = self._classify_user_message_event(
            session=session,
            message=request.message,
            message_mode=request.message_mode,
        )
        if pending_interrupt is not None:
            session = self._supersede_pending_interrupt_for_new_message(
                session=session,
                pending_interrupt=pending_interrupt,
                request=request,
                message_event=message_event,
            )
        queued_event = self._build_session_event(
            source="user_message",
            event_type=str(message_event.get("event_type") or "supplement"),
            message=request.message,
            metadata={
                "reason_tags": list(message_event.get("reason_tags") or []),
                "topic_shift_detected": bool((message_event.get("topic_shift") or {}).get("topic_shift_detected")),
                "incremental_tool_domains": list(message_event.get("incremental_tool_domains") or []),
            },
        )
        session = self._queue_session_event(
            session,
            event=queued_event,
            original_user_message=(
                request.message
                if str(message_event.get("event_type") or "") == "new_issue"
                else None
            ),
        )
        incident_state = dict(session.get("incident_state") or {})
        shared_context = dict(incident_state.get("shared_context") or {})
        ticket_request = TicketRequest(
            ticket_id=str(session.get("ticket_id") or session_id),
            user_id=str(session.get("user_id") or ""),
            message=request.message,
            service=incident_state.get("service") or infer_service_name(request.message),
            environment=request.environment or incident_state.get("environment"),
            host_identifier=request.host_identifier or incident_state.get("host_identifier") or infer_host_identifier(request.message),
            db_name=request.db_name or incident_state.get("db_name"),
            db_type=request.db_type or incident_state.get("db_type"),
            cluster=str(incident_state.get("cluster") or "prod-shanghai-1"),
            namespace=str(incident_state.get("namespace") or "default"),
            channel=str(incident_state.get("channel") or "feishu"),
            mock_scenario=request.mock_scenario or (str(shared_context.get("mock_scenario") or "") or None),
            mock_scenarios=(
                dict(request.mock_scenarios)
                if request.mock_scenarios
                else dict(shared_context.get("mock_scenarios") or {})
            ),
            mock_tool_responses=(
                dict(request.mock_tool_responses)
                if request.mock_tool_responses
                else dict(shared_context.get("mock_tool_responses") or {})
            ),
            mock_world_state=(
                dict(request.mock_world_state)
                if request.mock_world_state
                else dict(shared_context.get("mock_world_state") or {})
            ),
        )
        with self.observability.start_span(
            name="orchestrator.post_message",
            as_type="span",
            input={"session_id": session_id, "message": request.message},
            metadata={"entrypoint": "conversation_message"},
        ):
            payload = await self._run_ticket_message(
                ticket_request,
                session_id=str(session["session_id"]),
                thread_id=str(session.get("thread_id") or session["session_id"]),
                create_session=False,
                entrypoint="conversation_message",
                user_turn_payload={
                    "message": request.message,
                    "message_mode": request.message_mode,
                    "environment": request.environment,
                    "host_identifier": request.host_identifier,
                    "db_name": request.db_name,
                    "db_type": request.db_type,
                    "mock_scenario": request.mock_scenario,
                    "message_event": {
                        "event_type": message_event.get("event_type"),
                        "reason_tags": list(message_event.get("reason_tags") or []),
                        "topic_shift_detected": bool((message_event.get("topic_shift") or {}).get("topic_shift_detected")),
                        "incremental_tool_domains": list(message_event.get("incremental_tool_domains") or []),
                    },
                },
            )
            updated_session = payload.get("session")
            if isinstance(updated_session, dict):
                payload["session"] = self._mark_session_events_consumed(
                    updated_session,
                    event_ids=[str(queued_event.get("event_id") or "")],
                )
            diagnosis = dict(payload.get("diagnosis") or {})
            diagnosis["message_event"] = {
                "event_type": message_event.get("event_type"),
                "reason_tags": list(message_event.get("reason_tags") or []),
                "topic_shift_detected": bool((message_event.get("topic_shift") or {}).get("topic_shift_detected")),
                "incremental_tool_domains": list(message_event.get("incremental_tool_domains") or []),
            }
            payload["diagnosis"] = diagnosis
            return payload

    async def resume_conversation(self, session_id: str, request: ConversationResumeRequest) -> dict[str, Any]:
        session = self.session_service.get_session(session_id)
        if session is None:
            raise ValueError("session not found")
        pending_interrupt = self._get_pending_interrupt(session)
        if pending_interrupt is None:
            raise RuntimeError("conversation has no pending interrupt to resume")
        expected_interrupt_id = str(pending_interrupt.get("interrupt_id") or "")
        if request.interrupt_id and str(request.interrupt_id) != expected_interrupt_id:
            raise RuntimeError("resume interrupt does not match the current pending interrupt")
        answer_payload = self._normalize_resume_answer(request)
        interrupt_type = pending_interrupt.get("type")
        resume_target = self._resume_target_for_interrupt(str(interrupt_type or ""))
        self._append_system_event(
            session_id=session_id,
            thread_id=str(session.get("thread_id") or session_id),
            ticket_id=str(session.get("ticket_id") or session_id),
            event_type="conversation.resumed",
            payload={
                "interrupt_id": expected_interrupt_id,
                "interrupt_type": interrupt_type,
                "resume_target": resume_target,
                "orchestration_mode": self.settings.orchestration_mode,
            },
            metadata={"source": "orchestrator"},
        )
        with self.observability.start_span(
            name="orchestrator.resume_conversation",
            as_type="span",
            input={"session_id": session_id, "interrupt_type": interrupt_type, "answer_payload": answer_payload},
            metadata={"interrupt_id": expected_interrupt_id},
        ):
            self.observability.update_current_trace(
                session_id=session_id,
                user_id=str(session.get("user_id") or ""),
                metadata={"ticket_id": session.get("ticket_id"), "interrupt_type": interrupt_type},
            )
            if interrupt_type == "approval":
                approval_id = pending_interrupt.get("metadata", {}).get("approval_id")
                if not approval_id:
                    raise ValueError("approval id not found for pending approval interrupt")
                requested_approval_id = answer_payload.get("approval_id")
                if requested_approval_id is not None and str(requested_approval_id) != str(approval_id):
                    raise RuntimeError("resume approval id does not match the current pending interrupt")
                if answer_payload.get("approved") is None or not answer_payload.get("approver_id"):
                    raise ValueError("approval resume requires approved and approver_id")
                approval = self.approval_store.get(str(approval_id))
                if approval is None:
                    raise ValueError("approval not found")
                decision_request = ApprovalDecisionRequest(
                    approved=bool(answer_payload.get("approved")),
                    approver_id=str(answer_payload.get("approver_id")),
                    comment=answer_payload.get("comment"),
                )
                self.approval_store.decide(
                    str(approval_id),
                    decision_request.approved,
                    decision_request.approver_id,
                    decision_request.comment,
                )
                updated_approval = self.approval_store.get(str(approval_id))
                if updated_approval is not None:
                    approval = updated_approval
                self.interrupt_store.answer(
                    str(expected_interrupt_id),
                    answer_payload={
                        "approved": decision_request.approved,
                        "approver_id": decision_request.approver_id,
                        "comment": decision_request.comment,
                        "approval_id": approval_id,
                    },
                )
                approval_request_domain = self.approval_store.get_request(str(approval_id))
                payload = await self._resume_react_approval(
                    session=session,
                    approval=approval,
                    approval_id=str(approval_id),
                    pending_interrupt_id=str(expected_interrupt_id),
                    request=decision_request,
                    approval_request_domain=approval_request_domain,
                )
                return self._attach_observability(payload) or payload
            if interrupt_type == "clarification":
                payload = await self._resume_clarification(session, pending_interrupt, answer_payload)
                return self._attach_observability(payload) or payload
            if interrupt_type == "feedback":
                payload = await self._resume_feedback(session, pending_interrupt, answer_payload)
                return self._attach_observability(payload) or payload
            raise RuntimeError(f"unsupported interrupt type for resume: {interrupt_type}")


    @staticmethod
    def _resume_target_for_interrupt(interrupt_type: str | None) -> str:
        mapping = {
            "clarification": "supervisor_loop",
            "approval": "execute_approved_action",
            "feedback": "finalize",
        }
        return mapping.get(str(interrupt_type or ""), "supervisor_loop")

    async def _resume_react_approval(
        self,
        *,
        session: dict[str, Any],
        approval: dict[str, Any],
        approval_id: str,
        pending_interrupt_id: str,
        request: ApprovalDecisionRequest,
        approval_request_domain,
    ) -> dict[str, Any]:
        decision_record = legacy_decision_to_record(request, approval_id=approval_id)
        current_incident_state = IncidentState.model_validate(dict(session.get("incident_state") or {}))
        next_incident_state = apply_approval_resume_result_to_state(
            current_incident_state,
            approval_request_domain,
            decision_record,
        )
        if not request.approved:
            response = {
                "ticket_id": str(session.get("ticket_id") or session.get("session_id") or ""),
                "status": "completed",
                "message": "审批已拒绝，未执行任何高风险动作。",
                "diagnosis": {
                    "approval": {
                        "approval_id": approval_id,
                        "action": approval.get("action"),
                        "status": "rejected",
                        "comment": request.comment,
                    }
                },
            }
            return self._finalize_approval_resolution(
                session=session,
                approval=approval,
                approval_id=approval_id,
                pending_interrupt_id=pending_interrupt_id,
                response=response,
                next_incident_state=next_incident_state.model_dump(),
                actor_id=request.approver_id,
                process_summary="审批已拒绝，react tool-first 链路未执行高风险动作",
                user_turn_content=(f"拒绝审批动作：{request.comment}" if request.comment else "拒绝审批动作"),
                user_turn_payload={
                    "approval_id": approval_id,
                    "interrupt_id": pending_interrupt_id,
                    "approved": False,
                    "approver_id": request.approver_id,
                    "comment": request.comment,
                },
                process_payload={
                    "approved": False,
                    "approver_id": request.approver_id,
                    "comment": request.comment,
                },
            )

        synthetic_request = TicketRequest(
            ticket_id=str(session.get("ticket_id") or session.get("session_id") or ""),
            user_id=str(session.get("user_id") or ""),
            message=str(next_incident_state.message or dict(session.get("session_memory") or {}).get("original_user_message") or "approval resume"),
            service=next_incident_state.service or None,
            environment=getattr(next_incident_state, "environment", None),
            host_identifier=getattr(next_incident_state, "host_identifier", None),
            db_name=getattr(next_incident_state, "db_name", None),
            db_type=getattr(next_incident_state, "db_type", None),
            cluster=str(next_incident_state.cluster or "prod-shanghai-1"),
            namespace=str(next_incident_state.namespace or "default"),
            channel=str(next_incident_state.channel or "feishu"),
        )
        react_state = {
            "request": synthetic_request,
            "session_id": str(session.get("session_id") or session.get("thread_id") or synthetic_request.ticket_id),
            "thread_id": str(session.get("thread_id") or session.get("session_id") or synthetic_request.ticket_id),
            "incident_state": next_incident_state,
            "transition_notes": ["approval resume routed through react execute_approved_action"],
        }
        execute_state = await self.react_graph_nodes.execute_approved_action(react_state)
        final_state = await self.react_graph_nodes.finalize({**react_state, **execute_state})
        response = extract_react_graph_response(final_state)
        final_incident_state = final_state.get("incident_state") or next_incident_state
        return self._finalize_approval_resolution(
            session=session,
            approval=approval,
            approval_id=approval_id,
            pending_interrupt_id=pending_interrupt_id,
            response=response,
            next_incident_state=final_incident_state.model_dump() if hasattr(final_incident_state, "model_dump") else dict(final_incident_state or {}),
            actor_id=request.approver_id,
            process_summary="审批已通过，react tool-first 链路已执行高风险动作",
            user_turn_content=(f"批准审批动作：{request.comment}" if request.comment else "批准审批动作"),
            user_turn_payload={
                "approval_id": approval_id,
                "interrupt_id": pending_interrupt_id,
                "approved": True,
                "approver_id": request.approver_id,
                "comment": request.comment,
            },
            process_payload={
                "approved": True,
                "approver_id": request.approver_id,
                "comment": request.comment,
            },
        )

    def get_conversation(self, session_id: str) -> dict[str, Any] | None:
        return self._build_conversation_detail(session_id)

    def list_approval_events(self, approval_id: str) -> list[dict[str, Any]]:
        return self.approval_store.list_events(approval_id)

    def get_runtime_snapshot(self, session_id: str) -> dict[str, Any] | None:
        session = self.session_service.get_session(session_id)
        if session is None:
            return None
        incident_state = dict(session.get("incident_state") or {})
        metadata = dict(incident_state.get("metadata") or {})
        return {
            "session_id": session_id,
            "orchestration_mode": self.settings.orchestration_mode,
            "react_runtime": dict(metadata.get("react_runtime") or {}),
            "process_memory_summary": self._summarize_process_memory(session_id),
            "pending_interrupt": self._get_pending_interrupt(session),
        }

    def list_system_events(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.system_event_store.list_for_session(session_id, limit=limit)

    def list_execution_plans(self, session_id: str) -> list[dict[str, Any]]:
        plans = self.execution_store.list_plans(session_id)
        for plan in plans:
            plan["steps"] = self.execution_store.list_steps(str(plan["plan_id"]))
        return plans

    def get_execution_plan(self, plan_id: str) -> dict[str, Any] | None:
        plan = self.execution_store.get_plan(plan_id)
        if plan is None:
            return None
        plan["steps"] = self.execution_store.list_steps(plan_id)
        return plan

    def get_execution_recovery(self, session_id: str) -> dict[str, Any] | None:
        session = self.session_service.get_session(session_id)
        if session is None:
            return None
        checkpoints = self.checkpoint_store.list_for_session(session_id, limit=20)
        latest_checkpoint = checkpoints[0] if checkpoints else None
        last_success_checkpoint = None
        for checkpoint in checkpoints:
            metadata = dict(checkpoint.get("metadata") or {})
            response_status = str(metadata.get("response_status") or "")
            step_status = str(metadata.get("step_status") or "")
            if response_status == "failed" or step_status == "failed" or checkpoint.get("stage") == "execution_failed":
                continue
            last_success_checkpoint = checkpoint
            break

        recovery_action = "none"
        reason = "当前会话没有执行恢复需求。"
        plan = None
        resume_from_step_id = None
        failed_step_id = None
        last_completed_step_id = None
        recovery_hints: list[str] = []
        if latest_checkpoint is not None:
            metadata = dict(latest_checkpoint.get("metadata") or {})
            plan_id = metadata.get("plan_id")
            if plan_id:
                plan = self.get_execution_plan(str(plan_id))
            plan_recovery = dict(plan.get("recovery") or {}) if isinstance(plan, dict) else {}
            stage = str(latest_checkpoint.get("stage") or "")
            next_action = str(latest_checkpoint.get("next_action") or "")
            response_status = str(metadata.get("response_status") or "")
            step_status = str(metadata.get("step_status") or "")
            resume_from_step_id = plan_recovery.get("resume_from_step_id")
            failed_step_id = plan_recovery.get("failed_step_id")
            last_completed_step_id = plan_recovery.get("last_completed_step_id")
            recovery_hints = list(plan_recovery.get("hints") or [])
            if plan_recovery.get("recovery_action"):
                stored_recovery_action = str(plan_recovery.get("recovery_action") or "none")
                if stored_recovery_action in {"retry_execution_step", "finalize_execution"}:
                    recovery_action = "manual_intervention"
                    reason = "会话命中了旧版执行恢复语义；当前阶段统一转人工介入。"
                else:
                    recovery_action = stored_recovery_action
                    reason = str(plan_recovery.get("recovery_reason") or reason)
            elif stage == "execution_started" or stage == "execution_failed" or response_status == "failed" or step_status == "failed" or next_action in {"retry_execution_step", "manual_intervention"}:
                recovery_action = "manual_intervention"
                reason = "最近一次执行失败或中断，当前阶段统一转人工介入。"
                failed_step_id = failed_step_id or str(metadata.get("failed_step_id") or metadata.get("step_id") or "") or None
                resume_from_step_id = resume_from_step_id or failed_step_id
            elif stage == "execution_step_finished" or next_action == "finalize_execution":
                recovery_action = "manual_intervention"
                reason = "主动作可能已经执行，但会话未完整收尾，需人工确认外部状态后再继续处理。"
                last_completed_step_id = last_completed_step_id or str(metadata.get("current_step_id") or "") or None

        return {
            "session_id": session_id,
            "recovery_action": recovery_action,
            "reason": reason,
            "latest_checkpoint": latest_checkpoint,
            "last_success_checkpoint": last_success_checkpoint,
            "execution_plan": plan,
            "resume_from_step_id": resume_from_step_id,
            "failed_step_id": failed_step_id,
            "last_completed_step_id": last_completed_step_id,
            "recovery_hints": recovery_hints,
        }

    async def resume_execution_recovery(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("execution recovery is manual-only in the current stage; automatic resume is not supported")

    async def handle_ticket(self, request: TicketRequest) -> Dict[str, object]:
        with self.observability.start_span(
            name="orchestrator.handle_ticket",
            as_type="span",
            input={"ticket_id": request.ticket_id, "message": request.message, "service": request.service},
            metadata={"entrypoint": "ticket_create_legacy"},
        ):
            return await self._run_ticket_message(
                request,
                session_id=request.ticket_id,
                thread_id=request.ticket_id,
                create_session=True,
                entrypoint="ticket_create_legacy",
            )

    async def handle_approval_decision(
        self,
        approval: Dict[str, object],
        request: ApprovalDecisionRequest,
    ) -> Dict[str, object]:
        approval_id = str(approval.get("approval_id") or "") or None
        if approval_id is None:
            raise ValueError("approval id not found")
        thread_id = str(approval.get("thread_id") or approval.get("ticket_id") or "")
        session = self.session_service.get_session_by_thread_id(thread_id) if thread_id else None
        if session is None:
            raise ValueError("session not found for approval")
        session_id = str(session["session_id"])
        pending_interrupt_id = session.get("pending_interrupt_id")
        if not pending_interrupt_id:
            raise RuntimeError("approval decision requires a pending interrupt")

        with self.observability.start_span(
            name="orchestrator.handle_approval_decision",
            as_type="span",
            input={"approval_id": approval_id, "approved": request.approved, "approver_id": request.approver_id},
            metadata={"session_id": session_id},
        ):
            self.observability.update_current_trace(
                session_id=session_id,
                user_id=str(session.get("user_id") or ""),
                metadata={"approval_id": approval_id, "ticket_id": session.get("ticket_id")},
            )
            self.approval_store.decide(
                approval_id,
                request.approved,
                request.approver_id,
                request.comment,
            )
            updated_approval = self.approval_store.get(approval_id)
            if updated_approval is not None:
                approval = updated_approval
            self.interrupt_store.answer(
                str(pending_interrupt_id),
                answer_payload={
                    "approved": request.approved,
                    "approver_id": request.approver_id,
                    "comment": request.comment,
                    "approval_id": approval_id,
                },
            )

            approval_request_domain = self.approval_store.get_request(approval_id)
            state = await self.approval_graph.ainvoke(
                build_approval_graph_input(
                    approval,
                    request,
                    approval_request_domain=(approval_request_domain.model_dump() if approval_request_domain is not None else None),
                )
            )
            response = extract_react_graph_response(state)
            final_incident_state = state.get("incident_state")
            next_incident_state = (
                final_incident_state.model_dump()
                if final_incident_state is not None
                else dict(session.get("incident_state") or {})
            )

            decision_label = "批准" if request.approved else "拒绝"
            decision_content = f"{decision_label}审批动作"
            if request.comment:
                decision_content = f"{decision_content}：{request.comment}"
            payload = self._finalize_approval_resolution(
                session=session,
                approval=approval,
                approval_id=approval_id,
                pending_interrupt_id=str(pending_interrupt_id),
                response=response,
                next_incident_state=next_incident_state,
                actor_id=request.approver_id,
                process_summary=(
                    "审批已通过，但执行失败，已保留 checkpoint 与 failed step 等待人工介入"
                    if request.approved and str(response.get("status") or "") == "failed"
                    else "审批已通过，流程继续执行"
                    if request.approved
                    else "审批已拒绝，流程进入确定性结束状态"
                ),
                user_turn_content=decision_content,
                user_turn_payload={
                    "approved": request.approved,
                    "approver_id": request.approver_id,
                    "comment": request.comment,
                    "approval_id": approval_id,
                    "interrupt_id": pending_interrupt_id,
                    "approval_status": approval.get("status"),
                },
                process_payload={
                    "approved": request.approved,
                    "approver_id": request.approver_id,
                    "comment": request.comment,
                    "approval_status": approval.get("status"),
                    "response_status": response.get("status"),
                    "recovery_action": ((response.get("diagnosis") or {}).get("execution_limit") or {}).get("recovery_action") if isinstance(response.get("diagnosis"), dict) else None,
                },
            )
            return self._attach_observability(payload) or payload

    async def expire_approval(
        self,
        approval: Dict[str, object],
        *,
        actor_id: str = "system",
        comment: str | None = None,
    ) -> Dict[str, object]:
        return await self._resolve_terminal_approval(
            approval,
            resolution_status="expired",
            actor_id=actor_id,
            comment=comment,
        )

    async def cancel_approval(
        self,
        approval: Dict[str, object],
        *,
        actor_id: str = "system",
        comment: str | None = None,
    ) -> Dict[str, object]:
        return await self._resolve_terminal_approval(
            approval,
            resolution_status="cancelled",
            actor_id=actor_id,
            comment=comment,
        )

    async def _resolve_terminal_approval(
        self,
        approval: Dict[str, object],
        *,
        resolution_status: str,
        actor_id: str,
        comment: str | None,
    ) -> Dict[str, object]:
        approval_id = str(approval.get("approval_id") or "") or None
        if approval_id is None:
            raise ValueError("approval id not found")
        thread_id = str(approval.get("thread_id") or approval.get("ticket_id") or "")
        session = self.session_service.get_session_by_thread_id(thread_id) if thread_id else None
        if session is None:
            raise ValueError("session not found for approval")
        pending_interrupt_id = session.get("pending_interrupt_id")
        if not pending_interrupt_id:
            raise RuntimeError(f"approval {resolution_status} requires a pending interrupt")
        with self.observability.start_span(
            name=f"orchestrator.approval_{resolution_status}",
            as_type="span",
            input={"approval_id": approval_id, "actor_id": actor_id, "comment": comment},
            metadata={"session_id": session.get("session_id")},
        ):
            if resolution_status == "expired":
                self.approval_store.expire(approval_id, actor_id=actor_id, comment=comment)
                self.interrupt_store.expire(
                    str(pending_interrupt_id),
                    answer_payload={"approval_id": approval_id, "status": resolution_status, "actor_id": actor_id, "comment": comment},
                )
                message = "审批已超时，未执行任何高风险动作。"
                summary = "审批已超时，流程进入确定性结束状态"
                turn_content = "审批已超时"
            elif resolution_status == "cancelled":
                self.approval_store.cancel(approval_id, actor_id=actor_id, comment=comment)
                self.interrupt_store.cancel(
                    str(pending_interrupt_id),
                    answer_payload={"approval_id": approval_id, "status": resolution_status, "actor_id": actor_id, "comment": comment},
                )
                message = "审批已取消，未执行任何高风险动作。"
                summary = "审批已取消，流程进入确定性结束状态"
                turn_content = "审批已取消"
            else:
                raise ValueError(f"unsupported approval resolution status: {resolution_status}")

            updated_approval = self.approval_store.get(approval_id)
            if updated_approval is not None:
                approval = updated_approval
            approval_request = self.approval_store.get_request(approval_id)
            action = approval.get("action")
            if approval_request is not None and approval_request.proposals:
                action = approval_request.proposals[0].action
            response = {
                "ticket_id": str(approval.get("ticket_id") or session.get("ticket_id") or session.get("session_id") or ""),
                "status": "completed",
                "message": message,
                "diagnosis": {
                    "approval": {
                        "approval_id": approval_id,
                        "action": action,
                        "status": resolution_status,
                        "comment": comment,
                    }
                },
            }
            if comment:
                turn_content = f"{turn_content}：{comment}"
            next_incident_state = self._restore_incident_state_for_session(session)
            payload = self._finalize_approval_resolution(
                session=session,
                approval=approval,
                approval_id=approval_id,
                pending_interrupt_id=str(pending_interrupt_id),
                response=response,
                next_incident_state=next_incident_state,
                actor_id=actor_id,
                process_summary=summary,
                user_turn_content=turn_content,
                user_turn_payload={
                    "approval_id": approval_id,
                    "interrupt_id": pending_interrupt_id,
                    "approval_status": resolution_status,
                    "actor_id": actor_id,
                    "comment": comment,
                },
                process_payload={
                    "approval_status": resolution_status,
                    "actor_id": actor_id,
                    "comment": comment,
                },
            )
            return self._attach_observability(payload) or payload

    def _finalize_approval_resolution(
        self,
        *,
        session: dict[str, Any],
        approval: dict[str, Any] | Dict[str, object],
        approval_id: str,
        pending_interrupt_id: str,
        response: dict[str, Any],
        next_incident_state: dict[str, Any],
        actor_id: str,
        process_summary: str,
        user_turn_content: str,
        user_turn_payload: dict[str, Any],
        process_payload: dict[str, Any],
    ) -> Dict[str, object]:
        session_id = str(session["session_id"])
        thread_id = str(approval.get("thread_id") or approval.get("ticket_id") or session.get("thread_id") or session_id)

        response_status = str(response.get("status") or "completed")
        session_status = "failed" if response_status == "failed" else "completed"
        execution_recovery_action = (
            ((response.get("diagnosis") or {}).get("execution_limit") or {}).get("recovery_action")
            if isinstance(response.get("diagnosis"), dict)
            else None
        )
        checkpoint_next_action = str(execution_recovery_action or "manual_intervention") if response_status == "failed" else "complete"
        next_state_metadata = dict(next_incident_state.get("metadata") or {})
        _, pending_interrupt_payload = self._resolve_pending_interrupt(
            final_status=session_status,
            approval_request=None,
            clarification_interrupt=None,
            feedback_interrupt=next_state_metadata.get("feedback_interrupt")
            if isinstance(next_state_metadata.get("feedback_interrupt"), dict)
            else None,
        )
        pending_interrupt_id = (
            str(pending_interrupt_payload.get("interrupt_id") or "")
            if isinstance(pending_interrupt_payload, dict)
            else None
        )

        updated_session = self.session_service.update_session_state(
            session_id,
            incident_state=next_incident_state,
            status=session_status,
            current_stage="finalize",
            current_agent=self._resolve_current_agent(session, incident_state=next_incident_state),
            latest_approval_id=approval_id,
            pending_interrupt_id=pending_interrupt_id,
            session_memory=self._merge_session_memory(
                session,
                current_stage="finalize",
                pending_approval={},
                pending_interrupt=(
                    {
                        "interrupt_id": pending_interrupt_id,
                        "type": "feedback",
                        "reason": pending_interrupt_payload.get("reason"),
                        "question": pending_interrupt_payload.get("question"),
                    }
                    if pending_interrupt_id and isinstance(pending_interrupt_payload, dict)
                    else None
                ),
            ),
        )
        if updated_session is None:
            raise RuntimeError("session update failed after approval decision")

        checkpoint = self._create_checkpoint(
            session=updated_session,
            stage="approval_resume_finalize",
            next_action=checkpoint_next_action,
            incident_state=next_incident_state,
            metadata={
                "source": "approval_resume",
                "response_status": response.get("status"),
                "approval_id": approval_id,
                "interrupt_id": pending_interrupt_id,
                "recovery_action": checkpoint_next_action,
                "plan_id": (((response.get("diagnosis") or {}).get("execution_limit") or {}).get("plan_id") if isinstance(response.get("diagnosis"), dict) else None),
                "step_ids": (((response.get("diagnosis") or {}).get("execution_limit") or {}).get("step_ids") if isinstance(response.get("diagnosis"), dict) else None),
            },
        )
        updated_session = self.session_service.update_session_state(
            session_id,
            incident_state=next_incident_state,
            status=session_status,
            current_stage="finalize",
            current_agent=self._resolve_current_agent(updated_session, incident_state=next_incident_state),
            latest_approval_id=approval_id,
            pending_interrupt_id=pending_interrupt_id,
            last_checkpoint_id=checkpoint["checkpoint_id"],
        )
        if updated_session is None:
            raise RuntimeError("session checkpoint backfill failed after approval decision")
        saved_case = self._upsert_incident_case(
            session=updated_session,
            response=response,
            incident_state=next_incident_state,
        )
        self._append_user_turn(
            session_id,
            content=user_turn_content,
            structured_payload=dict(user_turn_payload),
        )
        self._append_process_entry(
            session_id=session_id,
            thread_id=thread_id,
            ticket_id=str(approval.get("ticket_id") or session.get("ticket_id") or session_id),
            event_type="approval_decided",
            stage="approval_resume",
            source="orchestrator",
            summary=process_summary,
            payload=dict(process_payload),
            refs={
                "approval_id": approval_id,
                "interrupt_id": pending_interrupt_id,
            },
        )
        self._append_system_event(
            session_id=session_id,
            thread_id=thread_id,
            ticket_id=str(approval.get("ticket_id") or session.get("ticket_id") or session_id),
            event_type=(
                "approval.approved"
                if process_payload.get("approved") is True
                else "approval.rejected"
                if process_payload.get("approved") is False
                else f"approval.{str(process_payload.get('approval_status') or 'resolved')}"
            ),
            payload={
                "approval_id": approval_id,
                "approval_status": approval.get("status"),
                "response_status": response.get("status"),
                "comment": process_payload.get("comment"),
            },
            metadata={"source": "orchestrator", "interrupt_id": pending_interrupt_id},
        )
        self._append_process_entry(
            session_id=session_id,
            thread_id=thread_id,
            ticket_id=str(approval.get("ticket_id") or session.get("ticket_id") or session_id),
            event_type="run_summary",
            stage="finalize",
            source="orchestrator",
            summary="审批恢复链路已完成并写入最终结果",
            payload={
                "response_status": response.get("status"),
                "message": response.get("message"),
                "approved": process_payload.get("approved"),
                "approval_status": approval.get("status"),
            },
            refs={
                "checkpoint_id": checkpoint.get("checkpoint_id"),
                "approval_id": approval_id,
                "interrupt_id": pending_interrupt_id,
            },
        )
        self.approval_store.record_resumed(
            approval_id,
            actor_id=actor_id,
            detail={
                "session_id": session_id,
                "thread_id": thread_id,
                "checkpoint_id": checkpoint.get("checkpoint_id"),
                "response_status": response.get("status"),
                "approval_status": approval.get("status"),
            },
        )
        self._append_system_event(
            session_id=session_id,
            thread_id=thread_id,
            ticket_id=str(approval.get("ticket_id") or session.get("ticket_id") or session_id),
            event_type="conversation.closed",
            payload={
                "status": session_status,
                "message": response.get("message"),
            },
            metadata={"source": "orchestrator", "checkpoint_id": checkpoint.get("checkpoint_id")},
        )
        assistant_turn = self._append_assistant_turn(
            session_id,
            response={
                **response,
                "approval_request": response.get("approval_request"),
            },
        )
        self._maybe_create_bad_case_candidate_from_run(
            session=updated_session,
            response=response,
            incident_state=next_incident_state,
            incident_case=saved_case,
        )
        return {
            **response,
            "session": updated_session,
            "pending_interrupt": (
                self.interrupt_store.get(str(pending_interrupt_id))
                if pending_interrupt_id
                else None
            ),
            "assistant_turn": assistant_turn,
        }
