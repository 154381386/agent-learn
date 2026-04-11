from __future__ import annotations

import json
from typing import Any, Optional

from .models import IncidentCase, ProcessMemoryEntry, ProcessMemorySummary
from ..session.models import utc_now
from ..storage.postgres import postgres_connection


class PostgresProcessMemoryStoreV2:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._init_db()

    def _init_db(self) -> None:
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                create table if not exists process_memory_entry (
                    memory_id text primary key,
                    session_id text not null,
                    thread_id text not null,
                    ticket_id text not null,
                    event_type text not null,
                    stage text not null,
                    source text not null,
                    summary text not null,
                    payload_json jsonb not null,
                    refs_json jsonb not null,
                    created_at timestamptz not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists incident_case (
                    case_id text primary key,
                    session_id text not null unique,
                    thread_id text not null,
                    ticket_id text not null,
                    service text not null,
                    cluster text not null,
                    namespace text not null,
                    current_agent text not null,
                    symptom text not null,
                    root_cause text not null,
                    key_evidence_json jsonb not null,
                    final_action text not null,
                    approval_required boolean not null,
                    verification_passed boolean,
                    human_verified boolean not null default false,
                    hypothesis_accuracy_json jsonb not null default '{}'::jsonb,
                    actual_root_cause_hypothesis text not null default '',
                    selected_hypothesis_id text not null default '',
                    selected_ranker_features_json jsonb not null default '{}'::jsonb,
                    final_conclusion text not null,
                    created_at timestamptz not null,
                    updated_at timestamptz not null,
                    closed_at timestamptz
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_incident_case_service_closed_at
                on incident_case (service, closed_at desc, case_id desc)
                """
            )
            conn.execute(
                """
                create index if not exists idx_incident_case_ticket_id
                on incident_case (ticket_id)
                """
            )
            conn.execute(
                """
                create index if not exists idx_process_memory_entry_session_created_at
                on process_memory_entry (session_id, created_at desc, memory_id desc)
                """
            )

    def append_entry(self, entry: ProcessMemoryEntry) -> ProcessMemoryEntry:
        payload = entry.model_copy(update={"created_at": utc_now()})
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                insert into process_memory_entry (
                    memory_id, session_id, thread_id, ticket_id, event_type, stage, source, summary,
                    payload_json, refs_json, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    payload.memory_id,
                    payload.session_id,
                    payload.thread_id,
                    payload.ticket_id,
                    payload.event_type,
                    payload.stage,
                    payload.source,
                    payload.summary,
                    json.dumps(payload.payload, ensure_ascii=False),
                    json.dumps(payload.refs, ensure_ascii=False),
                    payload.created_at,
                ),
            )
        return payload

    def list_entries(self, session_id: str, *, limit: Optional[int] = None) -> list[ProcessMemoryEntry]:
        query = """
            select memory_id, session_id, thread_id, ticket_id, event_type, stage, source, summary,
                   payload_json, refs_json, created_at
            from process_memory_entry
            where session_id = %s
            order by created_at desc, memory_id desc
        """
        params: tuple[Any, ...]
        if limit is not None:
            query += " limit %s"
            params = (session_id, limit)
        else:
            params = (session_id,)
        with postgres_connection(self.dsn) as conn:
            rows = conn.execute(query, params).fetchall()
        return [entry for row in rows if (entry := self._row_to_entry(row)) is not None]

    def summarize(self, session_id: str, *, limit: int = 20) -> ProcessMemorySummary:
        entries = self.list_entries(session_id, limit=limit)
        summary = ProcessMemorySummary(recent_entries=[entry.model_dump() for entry in reversed(entries[:5])])
        for entry in entries:
            item = {
                "memory_id": entry.memory_id,
                "event_type": entry.event_type,
                "stage": entry.stage,
                "summary": entry.summary,
                "refs": entry.refs,
                "created_at": entry.created_at,
            }
            if summary.latest_routing is None and entry.event_type == "routing_decision":
                summary.latest_routing = item
            if summary.latest_clarification is None and entry.event_type in {"clarification_created", "clarification_answered"}:
                summary.latest_clarification = item
            if summary.latest_approval is None and entry.event_type in {"approval_requested", "approval_decided"}:
                summary.latest_approval = item
            if summary.latest_execution is None and entry.event_type in {"execution_result", "run_summary", "verification_result"}:
                summary.latest_execution = item
            if entry.event_type in {"clarification_created", "approval_requested", "manual_intervention"}:
                summary.unresolved_items.append(item)
        if summary.latest_clarification and summary.latest_clarification.get("event_type") == "clarification_answered":
            summary.unresolved_items = [
                item for item in summary.unresolved_items if item.get("event_type") != "clarification_created"
            ]
        if summary.latest_approval and summary.latest_approval.get("event_type") == "approval_decided":
            summary.unresolved_items = [
                item for item in summary.unresolved_items if item.get("event_type") != "approval_requested"
            ]
        return summary

    def upsert_case(self, case: IncidentCase) -> IncidentCase:
        existing = self.get_case_by_session_id(case.session_id)
        now = utc_now()
        payload = case.model_copy(
            update={
                "case_id": existing.case_id if existing is not None else case.case_id,
                "created_at": existing.created_at if existing is not None else case.created_at,
                "updated_at": now,
            }
        )
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                insert into incident_case (
                    case_id, session_id, thread_id, ticket_id, service, cluster, namespace,
                    current_agent, symptom, root_cause, key_evidence_json, final_action, approval_required,
                    verification_passed, human_verified, hypothesis_accuracy_json, actual_root_cause_hypothesis,
                    selected_hypothesis_id, selected_ranker_features_json, final_conclusion, created_at, updated_at, closed_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s, %s)
                on conflict (session_id) do update set
                    thread_id = excluded.thread_id,
                    ticket_id = excluded.ticket_id,
                    service = excluded.service,
                    cluster = excluded.cluster,
                    namespace = excluded.namespace,
                    current_agent = excluded.current_agent,
                    symptom = excluded.symptom,
                    root_cause = excluded.root_cause,
                    key_evidence_json = excluded.key_evidence_json,
                    final_action = excluded.final_action,
                    approval_required = excluded.approval_required,
                    verification_passed = excluded.verification_passed,
                    human_verified = excluded.human_verified,
                    hypothesis_accuracy_json = excluded.hypothesis_accuracy_json,
                    actual_root_cause_hypothesis = excluded.actual_root_cause_hypothesis,
                    selected_hypothesis_id = excluded.selected_hypothesis_id,
                    selected_ranker_features_json = excluded.selected_ranker_features_json,
                    final_conclusion = excluded.final_conclusion,
                    updated_at = excluded.updated_at,
                    closed_at = excluded.closed_at
                """,
                (
                    payload.case_id,
                    payload.session_id,
                    payload.thread_id,
                    payload.ticket_id,
                    payload.service,
                    payload.cluster,
                    payload.namespace,
                    payload.current_agent,
                    payload.symptom,
                    payload.root_cause,
                    json.dumps(payload.key_evidence, ensure_ascii=False),
                    payload.final_action,
                    payload.approval_required,
                    payload.verification_passed,
                    payload.human_verified,
                    json.dumps(payload.hypothesis_accuracy, ensure_ascii=False),
                    payload.actual_root_cause_hypothesis,
                    payload.selected_hypothesis_id,
                    json.dumps(payload.selected_ranker_features, ensure_ascii=False),
                    payload.final_conclusion,
                    payload.created_at,
                    payload.updated_at,
                    payload.closed_at,
                ),
            )
        record = self.get_case_by_session_id(payload.session_id)
        if record is None:
            raise RuntimeError("incident case upsert failed")
        return record

    def get_case(self, case_id: str) -> Optional[IncidentCase]:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                """
                select case_id, session_id, thread_id, ticket_id, service, cluster, namespace, current_agent,
                       symptom, root_cause, key_evidence_json, final_action, approval_required, verification_passed,
                       human_verified, hypothesis_accuracy_json, actual_root_cause_hypothesis, selected_hypothesis_id,
                       selected_ranker_features_json, final_conclusion, created_at, updated_at, closed_at
                from incident_case where case_id = %s
                """,
                (case_id,),
            ).fetchone()
        return self._row_to_case(row)

    def get_case_by_session_id(self, session_id: str) -> Optional[IncidentCase]:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                """
                select case_id, session_id, thread_id, ticket_id, service, cluster, namespace, current_agent,
                       symptom, root_cause, key_evidence_json, final_action, approval_required, verification_passed,
                       human_verified, hypothesis_accuracy_json, actual_root_cause_hypothesis, selected_hypothesis_id,
                       selected_ranker_features_json, final_conclusion, created_at, updated_at, closed_at
                from incident_case where session_id = %s
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_case(row)

    def list_cases(
        self,
        *,
        service: str | None = None,
        final_action: str | None = None,
        approval_required: bool | None = None,
        verification_passed: bool | None = None,
        keyword: str | None = None,
        limit: int = 20,
    ) -> list[IncidentCase]:
        conditions = ["1 = 1"]
        params: list[Any] = []
        if service:
            conditions.append("service = %s")
            params.append(service)
        if final_action:
            conditions.append("final_action = %s")
            params.append(final_action)
        if approval_required is not None:
            conditions.append("approval_required = %s")
            params.append(approval_required)
        if verification_passed is not None:
            conditions.append("verification_passed = %s")
            params.append(verification_passed)
        if keyword:
            conditions.append("(symptom ilike %s or root_cause ilike %s or final_conclusion ilike %s)")
            like_value = f"%{keyword}%"
            params.extend([like_value, like_value, like_value])
        params.append(limit)
        query = f"""
            select case_id, session_id, thread_id, ticket_id, service, cluster, namespace, current_agent,
                   symptom, root_cause, key_evidence_json, final_action, approval_required, verification_passed,
                   human_verified, hypothesis_accuracy_json, actual_root_cause_hypothesis, selected_hypothesis_id,
                   selected_ranker_features_json, final_conclusion, created_at, updated_at, closed_at
            from incident_case
            where {' and '.join(conditions)}
            order by closed_at desc nulls last, updated_at desc, case_id desc
            limit %s
        """
        with postgres_connection(self.dsn) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [case for row in rows if (case := self._row_to_case(row)) is not None]

    @staticmethod
    def _row_to_entry(row: dict[str, Any] | None) -> Optional[ProcessMemoryEntry]:
        if row is None:
            return None
        return ProcessMemoryEntry(
            memory_id=row["memory_id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            ticket_id=row["ticket_id"],
            event_type=row["event_type"],
            stage=row["stage"],
            source=row["source"],
            summary=row["summary"],
            payload=row["payload_json"],
            refs=row["refs_json"],
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_case(row: dict[str, Any] | None) -> Optional[IncidentCase]:
        if row is None:
            return None
        return IncidentCase(
            case_id=row["case_id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            ticket_id=row["ticket_id"],
            service=row["service"],
            cluster=row["cluster"],
            namespace=row["namespace"],
            current_agent=row["current_agent"],
            symptom=row["symptom"],
            root_cause=row["root_cause"],
            key_evidence=row["key_evidence_json"],
            final_action=row["final_action"],
            approval_required=bool(row["approval_required"]),
            verification_passed=row["verification_passed"],
            human_verified=bool(row["human_verified"]),
            hypothesis_accuracy=row["hypothesis_accuracy_json"] or {},
            actual_root_cause_hypothesis=row["actual_root_cause_hypothesis"],
            selected_hypothesis_id=row["selected_hypothesis_id"],
            selected_ranker_features=row["selected_ranker_features_json"] or {},
            final_conclusion=row["final_conclusion"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            closed_at=str(row["closed_at"]) if row["closed_at"] is not None else None,
        )
