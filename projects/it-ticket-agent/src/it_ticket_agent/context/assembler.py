from __future__ import annotations

from typing import Any, Optional

from ..memory.working_memory import normalize_working_memory
from .models import (
    EvidenceBundle,
    ExecutionBudget,
    ExecutionContext,
    PendingInterruptContext,
    RequestContext,
    SessionSnapshot,
)


class ContextAssembler:
    def __init__(self, *, max_recent_turns: int = 5) -> None:
        self.max_recent_turns = max_recent_turns

    def assemble(
        self,
        *,
        request: Any,
        session: dict[str, Any],
        pending_interrupt: Optional[dict[str, Any]] = None,
        recent_turns: Optional[list[dict[str, Any]]] = None,
        incident_state: Optional[dict[str, Any]] = None,
        process_memory_summary: Optional[dict[str, Any]] = None,
        incident_case_summary: Optional[list[dict[str, Any]]] = None,
        entrypoint: str,
    ) -> ExecutionContext:
        snapshot = incident_state or dict(session.get("incident_state") or {})
        turns = list(recent_turns or [])[-self.max_recent_turns :]
        pending_interrupt_context = None
        if pending_interrupt is not None:
            pending_interrupt_context = PendingInterruptContext(
                interrupt_id=str(pending_interrupt.get("interrupt_id") or ""),
                type=str(pending_interrupt.get("type") or ""),
                reason=str(pending_interrupt.get("reason") or ""),
                question=str(pending_interrupt.get("question") or ""),
                expected_input_schema=dict(pending_interrupt.get("expected_input_schema") or {}),
                metadata=dict(pending_interrupt.get("metadata") or {}),
            )
        evidence_bundle = EvidenceBundle(
            routing=dict(snapshot.get("routing") or {}),
            rag_context=dict(snapshot.get("rag_context") or {}) if snapshot.get("rag_context") else None,
            approval_proposals=list(snapshot.get("approval_proposals") or []),
            approved_actions=list(snapshot.get("approved_actions") or []),
            execution_results=list(snapshot.get("execution_results") or []),
            verification_results=list(snapshot.get("verification_results") or []),
            open_questions=list(snapshot.get("open_questions") or []),
        )
        session_memory = dict(session.get("session_memory") or {})
        working_memory_payload = session_memory.get("working_memory")
        working_memory = normalize_working_memory(
            working_memory_payload if isinstance(working_memory_payload, dict) else None
        )
        process_summary = dict(process_memory_summary or {})
        case_summary = list(incident_case_summary or [])
        fallback_summary = {
            "clarification_answers": dict(snapshot.get("metadata", {}).get("clarification_answers") or {}),
        }
        compact_incident_state = {
            "status": snapshot.get("status"),
            "service": snapshot.get("service"),
            "environment": snapshot.get("environment"),
            "host_identifier": snapshot.get("host_identifier"),
            "db_name": snapshot.get("db_name"),
            "db_type": snapshot.get("db_type"),
            "cluster": snapshot.get("cluster"),
            "namespace": snapshot.get("namespace"),
            "routing": dict(snapshot.get("routing") or {}),
            "open_questions": list(snapshot.get("open_questions") or []),
        }
        memory_summary: dict[str, Any] = {
            "working_memory": working_memory,
            "current_incident_state": compact_incident_state,
        }
        session_memory_payload = {key: value for key, value in session_memory.items() if key != "working_memory"}
        if session_memory_payload:
            memory_summary["session_memory"] = session_memory_payload
        elif fallback_summary["clarification_answers"]:
            memory_summary.update(fallback_summary)
        if process_summary:
            memory_summary["agent_events"] = process_summary
            memory_summary["process_memory"] = process_summary
        context_snapshot_payload = dict(snapshot.get("context_snapshot") or snapshot.get("metadata", {}).get("context_snapshot") or {})
        diagnosis_playbooks = list(context_snapshot_payload.get("diagnosis_playbooks") or [])
        playbook_recall = dict(context_snapshot_payload.get("playbook_recall") or {})
        if diagnosis_playbooks:
            memory_summary["diagnosis_playbooks"] = diagnosis_playbooks[:2]
        if playbook_recall:
            memory_summary["playbook_recall"] = playbook_recall
        if case_summary:
            memory_summary["incident_cases"] = case_summary
        return ExecutionContext(
            request_context=RequestContext(
                ticket_id=str(request.ticket_id),
                session_id=str(session.get("session_id") or request.ticket_id),
                thread_id=str(session.get("thread_id") or request.ticket_id),
                user_id=str(request.user_id),
                message=str(request.message),
                service=request.service,
                cluster=str(request.cluster),
                namespace=str(request.namespace),
                channel=str(request.channel),
                entrypoint=entrypoint,
            ),
            session_snapshot=SessionSnapshot(
                session_id=str(session.get("session_id") or request.ticket_id),
                thread_id=str(session.get("thread_id") or request.ticket_id),
                status=str(session.get("status") or "active"),
                current_stage=str(session.get("current_stage") or "ingest"),
                latest_approval_id=session.get("latest_approval_id"),
                pending_interrupt_id=session.get("pending_interrupt_id"),
                last_checkpoint_id=session.get("last_checkpoint_id"),
                incident_state=snapshot,
                recent_turns=turns,
            ),
            pending_interrupt=pending_interrupt_context,
            evidence_bundle=evidence_bundle,
            memory_summary=memory_summary,
            execution_budget=ExecutionBudget(max_recent_turns=self.max_recent_turns),
        )

    @staticmethod
    def to_shared_context(context: ExecutionContext) -> dict[str, Any]:
        request_context = context.request_context
        return {
            "ticket_id": request_context.ticket_id,
            "user_id": request_context.user_id,
            "message": request_context.message,
            "service": request_context.service or "",
            "cluster": request_context.cluster,
            "namespace": request_context.namespace,
            "channel": request_context.channel,
            "rag_context": dict(context.evidence_bundle.rag_context or {}),
            "execution_context": context.model_dump(),
        }
