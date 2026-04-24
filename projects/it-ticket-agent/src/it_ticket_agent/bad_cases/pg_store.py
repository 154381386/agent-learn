from __future__ import annotations

import json
from typing import Any, Optional

from .models import BadCaseCandidate
from ..session.models import utc_now
from ..storage.postgres import postgres_connection


class PostgresBadCaseCandidateStoreV2:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._init_db()

    def _init_db(self) -> None:
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                create table if not exists bad_case_candidate (
                    candidate_id text primary key,
                    session_id text not null,
                    thread_id text not null,
                    ticket_id text not null,
                    source text not null,
                    reason_codes_json jsonb not null,
                    severity text not null,
                    request_payload_json jsonb not null,
                    response_payload_json jsonb not null,
                    incident_state_snapshot_json jsonb not null,
                    context_snapshot_json jsonb not null,
                    observations_json jsonb not null,
                    retrieval_expansion_json jsonb not null,
                    human_feedback_json jsonb not null,
                    conversation_turns_json jsonb not null default '[]'::jsonb,
                    system_events_json jsonb not null default '[]'::jsonb,
                    export_status text not null default 'pending',
                    export_metadata_json jsonb not null default '{}'::jsonb,
                    created_at timestamptz not null,
                    updated_at timestamptz not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_bad_case_candidate_session_created_at
                on bad_case_candidate (session_id, created_at desc, candidate_id desc)
                """
            )
            conn.execute(
                """
                create index if not exists idx_bad_case_candidate_export_status_created_at
                on bad_case_candidate (export_status, created_at desc, candidate_id desc)
                """
            )
            conn.execute(
                """
                create index if not exists idx_bad_case_candidate_source_created_at
                on bad_case_candidate (source, created_at desc, candidate_id desc)
                """
            )
            columns = {
                row["column_name"]
                for row in conn.execute(
                    """
                    select column_name
                    from information_schema.columns
                    where table_schema = 'public' and table_name = 'bad_case_candidate'
                    """
                ).fetchall()
            }
            if "conversation_turns_json" not in columns:
                conn.execute(
                    "alter table bad_case_candidate add column conversation_turns_json jsonb not null default '[]'::jsonb"
                )
            if "system_events_json" not in columns:
                conn.execute(
                    "alter table bad_case_candidate add column system_events_json jsonb not null default '[]'::jsonb"
                )
            if "export_metadata_json" not in columns:
                conn.execute(
                    "alter table bad_case_candidate add column export_metadata_json jsonb not null default '{}'::jsonb"
                )

    def create_candidate(self, candidate: BadCaseCandidate) -> BadCaseCandidate:
        now = utc_now()
        payload = candidate.model_copy(update={"created_at": now, "updated_at": now})
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                insert into bad_case_candidate (
                    candidate_id, session_id, thread_id, ticket_id, source, reason_codes_json,
                    severity, request_payload_json, response_payload_json, incident_state_snapshot_json,
                    context_snapshot_json, observations_json, retrieval_expansion_json, human_feedback_json,
                    conversation_turns_json, system_events_json, export_status, export_metadata_json,
                    created_at, updated_at
                ) values (
                    %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s, %s::jsonb, %s, %s
                )
                """,
                (
                    payload.candidate_id,
                    payload.session_id,
                    payload.thread_id,
                    payload.ticket_id,
                    payload.source,
                    json.dumps(payload.reason_codes, ensure_ascii=False),
                    payload.severity,
                    json.dumps(payload.request_payload, ensure_ascii=False),
                    json.dumps(payload.response_payload, ensure_ascii=False),
                    json.dumps(payload.incident_state_snapshot, ensure_ascii=False),
                    json.dumps(payload.context_snapshot, ensure_ascii=False),
                    json.dumps(payload.observations, ensure_ascii=False),
                    json.dumps(payload.retrieval_expansion, ensure_ascii=False),
                    json.dumps(payload.human_feedback, ensure_ascii=False),
                    json.dumps(payload.conversation_turns, ensure_ascii=False),
                    json.dumps(payload.system_events, ensure_ascii=False),
                    payload.export_status,
                    json.dumps(payload.export_metadata, ensure_ascii=False),
                    payload.created_at,
                    payload.updated_at,
                ),
            )
        saved = self.get_candidate(payload.candidate_id)
        if saved is None:
            raise RuntimeError("bad case candidate create failed")
        return saved

    def get_candidate(self, candidate_id: str) -> Optional[BadCaseCandidate]:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                """
                select candidate_id, session_id, thread_id, ticket_id, source, reason_codes_json,
                       severity, request_payload_json, response_payload_json, incident_state_snapshot_json,
                       context_snapshot_json, observations_json, retrieval_expansion_json, human_feedback_json,
                       conversation_turns_json, system_events_json, export_status, export_metadata_json,
                       created_at, updated_at
                from bad_case_candidate
                where candidate_id = %s
                """,
                (candidate_id,),
            ).fetchone()
        return self._row_to_candidate(row)

    def list_candidates(
        self,
        *,
        session_id: str | None = None,
        source: str | None = None,
        export_status: str | None = None,
        limit: int = 50,
    ) -> list[BadCaseCandidate]:
        conditions = ["1 = 1"]
        params: list[Any] = []
        if session_id:
            conditions.append("session_id = %s")
            params.append(session_id)
        if source:
            conditions.append("source = %s")
            params.append(source)
        if export_status:
            conditions.append("export_status = %s")
            params.append(export_status)
        params.append(limit)
        query = f"""
            select candidate_id, session_id, thread_id, ticket_id, source, reason_codes_json,
                   severity, request_payload_json, response_payload_json, incident_state_snapshot_json,
                   context_snapshot_json, observations_json, retrieval_expansion_json, human_feedback_json,
                   conversation_turns_json, system_events_json, export_status, export_metadata_json,
                   created_at, updated_at
            from bad_case_candidate
            where {' and '.join(conditions)}
            order by created_at desc, candidate_id desc
            limit %s
        """
        with postgres_connection(self.dsn) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [candidate for row in rows if (candidate := self._row_to_candidate(row)) is not None]

    def update_export_status(
        self,
        candidate_id: str,
        *,
        export_status: str,
        export_metadata: dict[str, Any] | None = None,
    ) -> Optional[BadCaseCandidate]:
        existing = self.get_candidate(candidate_id)
        if existing is None:
            return None
        payload = existing.model_copy(
            update={
                "export_status": export_status,
                "export_metadata": dict(export_metadata or existing.export_metadata),
                "updated_at": utc_now(),
            }
        )
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                update bad_case_candidate
                set export_status = %s, export_metadata_json = %s::jsonb, updated_at = %s
                where candidate_id = %s
                """,
                (
                    payload.export_status,
                    json.dumps(payload.export_metadata, ensure_ascii=False),
                    payload.updated_at,
                    candidate_id,
                ),
            )
        return self.get_candidate(candidate_id)

    @staticmethod
    def _row_to_candidate(row: dict[str, Any] | None) -> BadCaseCandidate | None:
        if row is None:
            return None
        return BadCaseCandidate(
            candidate_id=row["candidate_id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            ticket_id=row["ticket_id"],
            source=row["source"],
            reason_codes=list(row["reason_codes_json"]),
            severity=row["severity"],
            request_payload=dict(row["request_payload_json"]),
            response_payload=dict(row["response_payload_json"]),
            incident_state_snapshot=dict(row["incident_state_snapshot_json"]),
            context_snapshot=dict(row["context_snapshot_json"]),
            observations=list(row["observations_json"]),
            retrieval_expansion=dict(row["retrieval_expansion_json"]),
            human_feedback=dict(row["human_feedback_json"]),
            conversation_turns=list(row["conversation_turns_json"]),
            system_events=list(row["system_events_json"]),
            export_status=row["export_status"],
            export_metadata=dict(row["export_metadata_json"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
