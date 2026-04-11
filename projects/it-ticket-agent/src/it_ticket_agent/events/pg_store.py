from __future__ import annotations

import json

from .models import SystemEvent
from ..session.models import utc_now
from ..storage.postgres import postgres_connection


class PostgresSystemEventStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._init_db()

    def _init_db(self) -> None:
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                create table if not exists system_event (
                    event_id text primary key,
                    session_id text not null,
                    thread_id text not null,
                    ticket_id text not null,
                    event_type text not null,
                    payload_json jsonb not null,
                    metadata_json jsonb not null,
                    created_at timestamptz not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_system_event_session_created_at
                on system_event (session_id, created_at, event_id)
                """
            )

    def create(self, event: SystemEvent | dict) -> dict:
        record = event if isinstance(event, SystemEvent) else SystemEvent.model_validate(event)
        saved = self.create_event(record)
        return saved.model_dump()

    def create_event(self, event: SystemEvent) -> SystemEvent:
        payload = event.model_copy(update={"created_at": utc_now()})
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                insert into system_event (
                    event_id, session_id, thread_id, ticket_id, event_type, payload_json, metadata_json, created_at
                ) values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    payload.event_id,
                    payload.session_id,
                    payload.thread_id,
                    payload.ticket_id,
                    payload.event_type,
                    json.dumps(payload.payload, ensure_ascii=False),
                    json.dumps(payload.metadata, ensure_ascii=False),
                    payload.created_at,
                ),
            )
        return payload

    def list_for_session(self, session_id: str, limit: int = 100) -> list[dict]:
        return [record.model_dump() for record in self.list_events(session_id, limit=limit)]

    def list_events(self, session_id: str, limit: int = 100) -> list[SystemEvent]:
        with postgres_connection(self.dsn) as conn:
            rows = conn.execute(
                """
                select event_id, session_id, thread_id, ticket_id, event_type, payload_json, metadata_json, created_at
                from system_event
                where session_id = %s
                order by created_at asc, event_id asc
                limit %s
                """,
                (session_id, limit),
            ).fetchall()
        return [event for row in rows if (event := self._row_to_event(row)) is not None]

    @staticmethod
    def _row_to_event(row: dict | None) -> SystemEvent | None:
        if row is None:
            return None
        return SystemEvent(
            event_id=row["event_id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            ticket_id=row["ticket_id"],
            event_type=row["event_type"],
            payload=row["payload_json"],
            metadata=row["metadata_json"],
            created_at=str(row["created_at"]),
        )
