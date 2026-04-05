from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, List, Optional

from .models import InterruptRequest, utc_now


class InterruptStateError(RuntimeError):
    pass


class InterruptStoreV2:
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
                create table if not exists interrupt_request (
                    interrupt_id text primary key,
                    session_id text not null,
                    ticket_id text not null,
                    type text not null,
                    source text not null,
                    reason text not null,
                    question text not null,
                    expected_input_schema_json text not null,
                    status text not null,
                    resume_token text not null,
                    timeout_at text,
                    answer_payload_json text not null,
                    metadata_json text not null,
                    created_at text not null,
                    resolved_at text
                )
                """
            )
            conn.commit()

    def create_interrupt(self, interrupt: InterruptRequest) -> InterruptRequest:
        payload = interrupt.model_copy(update={"created_at": utc_now(), "status": "pending", "resolved_at": None})
        with self._connect() as conn:
            conn.execute(
                """
                insert into interrupt_request (
                    interrupt_id, session_id, ticket_id, type, source, reason, question,
                    expected_input_schema_json, status, resume_token, timeout_at,
                    answer_payload_json, metadata_json, created_at, resolved_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            conn.commit()
        return payload

    def get_interrupt(self, interrupt_id: str) -> Optional[InterruptRequest]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select interrupt_id, session_id, ticket_id, type, source, reason, question,
                       expected_input_schema_json, status, resume_token, timeout_at,
                       answer_payload_json, metadata_json, created_at, resolved_at
                from interrupt_request
                where interrupt_id = ?
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
            conditions.append("session_id = ?")
            params.append(session_id)
        if ticket_id is not None:
            conditions.append("ticket_id = ?")
            params.append(ticket_id)
        query = f"""
            select interrupt_id, session_id, ticket_id, type, source, reason, question,
                   expected_input_schema_json, status, resume_token, timeout_at,
                   answer_payload_json, metadata_json, created_at, resolved_at
            from interrupt_request
            where {' and '.join(conditions)}
            order by created_at asc
        """
        with self._connect() as conn:
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
        with self._connect() as conn:
            conn.execute(
                """
                update interrupt_request
                set status = ?, answer_payload_json = ?, resolved_at = ?
                where interrupt_id = ? and status = 'pending'
                """,
                (status, json.dumps(answer_payload, ensure_ascii=False), resolved_at, interrupt_id),
            )
            conn.commit()
        return self.get_interrupt(interrupt_id)

    @staticmethod
    def _row_to_interrupt(row: sqlite3.Row | None) -> Optional[InterruptRequest]:
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
            expected_input_schema=json.loads(row["expected_input_schema_json"]),
            status=row["status"],
            resume_token=row["resume_token"],
            timeout_at=row["timeout_at"],
            answer_payload=json.loads(row["answer_payload_json"]),
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )
