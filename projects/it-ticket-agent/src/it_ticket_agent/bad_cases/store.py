from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Optional

from .models import BadCaseCandidate
from ..session.models import utc_now


class BadCaseCandidateStoreV2:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        folder = os.path.dirname(db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists bad_case_candidate (
                    candidate_id text primary key,
                    session_id text not null,
                    thread_id text not null,
                    ticket_id text not null,
                    source text not null,
                    reason_codes_json text not null,
                    severity text not null,
                    request_payload_json text not null,
                    response_payload_json text not null,
                    incident_state_snapshot_json text not null,
                    context_snapshot_json text not null,
                    observations_json text not null,
                    retrieval_expansion_json text not null,
                    human_feedback_json text not null,
                    conversation_turns_json text not null,
                    system_events_json text not null,
                    export_status text not null default 'pending',
                    export_metadata_json text not null default '{}',
                    created_at text not null,
                    updated_at text not null
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
            columns = {row["name"] for row in conn.execute("pragma table_info(bad_case_candidate)").fetchall()}
            if "conversation_turns_json" not in columns:
                conn.execute(
                    "alter table bad_case_candidate add column conversation_turns_json text not null default '[]'"
                )
            if "system_events_json" not in columns:
                conn.execute(
                    "alter table bad_case_candidate add column system_events_json text not null default '[]'"
                )
            if "export_metadata_json" not in columns:
                conn.execute(
                    "alter table bad_case_candidate add column export_metadata_json text not null default '{}'"
                )
            conn.commit()

    def create_candidate(self, candidate: BadCaseCandidate) -> BadCaseCandidate:
        now = utc_now()
        payload = candidate.model_copy(update={"created_at": now, "updated_at": now})
        with self._connect() as conn:
            conn.execute(
                """
                insert into bad_case_candidate (
                    candidate_id, session_id, thread_id, ticket_id, source, reason_codes_json,
                    severity, request_payload_json, response_payload_json, incident_state_snapshot_json,
                    context_snapshot_json, observations_json, retrieval_expansion_json, human_feedback_json,
                    conversation_turns_json, system_events_json, export_status, export_metadata_json,
                    created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            conn.commit()
        saved = self.get_candidate(payload.candidate_id)
        if saved is None:
            raise RuntimeError("bad case candidate create failed")
        return saved

    def get_candidate(self, candidate_id: str) -> Optional[BadCaseCandidate]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select candidate_id, session_id, thread_id, ticket_id, source, reason_codes_json,
                       severity, request_payload_json, response_payload_json, incident_state_snapshot_json,
                       context_snapshot_json, observations_json, retrieval_expansion_json, human_feedback_json,
                       conversation_turns_json, system_events_json, export_status, export_metadata_json,
                       created_at, updated_at
                from bad_case_candidate
                where candidate_id = ?
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
            conditions.append("session_id = ?")
            params.append(session_id)
        if source:
            conditions.append("source = ?")
            params.append(source)
        if export_status:
            conditions.append("export_status = ?")
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
            limit ?
        """
        with self._connect() as conn:
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
        with self._connect() as conn:
            conn.execute(
                """
                update bad_case_candidate
                set export_status = ?, export_metadata_json = ?, updated_at = ?
                where candidate_id = ?
                """,
                (
                    payload.export_status,
                    json.dumps(payload.export_metadata, ensure_ascii=False),
                    payload.updated_at,
                    candidate_id,
                ),
            )
            conn.commit()
        return self.get_candidate(candidate_id)

    @staticmethod
    def _row_to_candidate(row: sqlite3.Row | None) -> BadCaseCandidate | None:
        if row is None:
            return None
        return BadCaseCandidate(
            candidate_id=row["candidate_id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            ticket_id=row["ticket_id"],
            source=row["source"],
            reason_codes=json.loads(row["reason_codes_json"]),
            severity=row["severity"],
            request_payload=json.loads(row["request_payload_json"]),
            response_payload=json.loads(row["response_payload_json"]),
            incident_state_snapshot=json.loads(row["incident_state_snapshot_json"]),
            context_snapshot=json.loads(row["context_snapshot_json"]),
            observations=json.loads(row["observations_json"]),
            retrieval_expansion=json.loads(row["retrieval_expansion_json"]),
            human_feedback=json.loads(row["human_feedback_json"]),
            conversation_turns=json.loads(row["conversation_turns_json"]),
            system_events=json.loads(row["system_events_json"]),
            export_status=row["export_status"],
            export_metadata=json.loads(row["export_metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
