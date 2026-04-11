from __future__ import annotations

import json
from typing import Any, List, Optional

from .models import InterruptRequest, utc_now
from .store import InterruptStateError
from ..storage.postgres import postgres_connection


class PostgresInterruptStoreV2:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._init_db()

    def _init_db(self) -> None:
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                create table if not exists interrupt_request (
                    interrupt_id text primary key,
                    session_id text not null,
                    ticket_id text not null,
                    type text not null,
                    source text not null,
                    reason text not null,
                    question text not null,
                    expected_input_schema_json jsonb not null,
                    status text not null,
                    resume_token text not null,
                    timeout_at timestamptz,
                    answer_payload_json jsonb not null,
                    metadata_json jsonb not null,
                    created_at timestamptz not null,
                    resolved_at timestamptz
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_interrupt_request_pending_session_created
                on interrupt_request (status, session_id, created_at)
                """
            )

    def create_interrupt(self, interrupt: InterruptRequest) -> InterruptRequest:
        payload = interrupt.model_copy(update={"created_at": utc_now(), "status": "pending", "resolved_at": None})
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                insert into interrupt_request (
                    interrupt_id, session_id, ticket_id, type, source, reason, question,
                    expected_input_schema_json, status, resume_token, timeout_at,
                    answer_payload_json, metadata_json, created_at, resolved_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                """,
                (
                    payload.interrupt_id,
                    payload.session_id,
                    payload.ticket_id,
                    payload.type,
                    payload.source,
                    payload.reason,
                    payload.question,
                    json.dumps(payload.expected_input_schema, ensure_ascii=False),
                    payload.status,
                    payload.resume_token,
                    payload.timeout_at,
                    json.dumps(payload.answer_payload, ensure_ascii=False),
                    json.dumps(payload.metadata, ensure_ascii=False),
                    payload.created_at,
                    payload.resolved_at,
                ),
            )
        return payload

    def get_interrupt(self, interrupt_id: str) -> Optional[InterruptRequest]:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                """
                select interrupt_id, session_id, ticket_id, type, source, reason, question,
                       expected_input_schema_json, status, resume_token, timeout_at,
                       answer_payload_json, metadata_json, created_at, resolved_at
                from interrupt_request
                where interrupt_id = %s
                """,
                (interrupt_id,),
            ).fetchone()
        return self._row_to_interrupt(row)

    def get_pending_interrupts(
        self,
        *,
        session_id: str | None = None,
        ticket_id: str | None = None,
    ) -> List[InterruptRequest]:
        conditions = ["status = 'pending'"]
        params: list[Any] = []
        if session_id is not None:
            conditions.append("session_id = %s")
            params.append(session_id)
        if ticket_id is not None:
            conditions.append("ticket_id = %s")
            params.append(ticket_id)
        query = f"""
            select interrupt_id, session_id, ticket_id, type, source, reason, question,
                   expected_input_schema_json, status, resume_token, timeout_at,
                   answer_payload_json, metadata_json, created_at, resolved_at
            from interrupt_request
            where {' and '.join(conditions)}
            order by created_at asc
        """
        with postgres_connection(self.dsn) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [interrupt for row in rows if (interrupt := self._row_to_interrupt(row)) is not None]

    def mark_answered(self, interrupt_id: str, *, answer_payload: dict[str, Any]) -> Optional[InterruptRequest]:
        return self._resolve_interrupt(interrupt_id, status="answered", answer_payload=answer_payload)

    def mark_cancelled(self, interrupt_id: str, *, answer_payload: dict[str, Any] | None = None) -> Optional[InterruptRequest]:
        return self._resolve_interrupt(interrupt_id, status="cancelled", answer_payload=answer_payload or {})

    def mark_expired(self, interrupt_id: str, *, answer_payload: dict[str, Any] | None = None) -> Optional[InterruptRequest]:
        return self._resolve_interrupt(interrupt_id, status="expired", answer_payload=answer_payload or {})

    def _resolve_interrupt(
        self,
        interrupt_id: str,
        *,
        status: str,
        answer_payload: dict[str, Any],
    ) -> Optional[InterruptRequest]:
        existing = self.get_interrupt(interrupt_id)
        if existing is None:
            return None
        if existing.status != "pending":
            raise InterruptStateError("interrupt already resolved")
        resolved_at = utc_now()
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                update interrupt_request
                set status = %s, answer_payload_json = %s::jsonb, resolved_at = %s
                where interrupt_id = %s and status = 'pending'
                """,
                (status, json.dumps(answer_payload, ensure_ascii=False), resolved_at, interrupt_id),
            )
        return self.get_interrupt(interrupt_id)

    @staticmethod
    def _row_to_interrupt(row: dict[str, Any] | None) -> Optional[InterruptRequest]:
        if row is None:
            return None
        return InterruptRequest(
            interrupt_id=row["interrupt_id"],
            session_id=row["session_id"],
            ticket_id=row["ticket_id"],
            type=row["type"],
            source=row["source"],
            reason=row["reason"],
            question=row["question"],
            expected_input_schema=row["expected_input_schema_json"],
            status=row["status"],
            resume_token=row["resume_token"],
            timeout_at=str(row["timeout_at"]) if row["timeout_at"] is not None else None,
            answer_payload=row["answer_payload_json"],
            metadata=row["metadata_json"],
            created_at=str(row["created_at"]),
            resolved_at=str(row["resolved_at"]) if row["resolved_at"] is not None else None,
        )
