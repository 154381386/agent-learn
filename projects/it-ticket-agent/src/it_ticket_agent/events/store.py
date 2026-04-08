from __future__ import annotations

import json
import os
import sqlite3

from .models import SystemEvent
from ..session.models import utc_now


class SystemEventStoreV2:
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
                create table if not exists system_event (
                    event_id text primary key,
                    session_id text not null,
                    thread_id text not null,
                    ticket_id text not null,
                    event_type text not null,
                    payload_json text not null,
                    metadata_json text not null,
                    created_at text not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_system_event_session_created_at
                on system_event (session_id, created_at, event_id)
                """
            )
            conn.commit()

    def create_event(self, event: SystemEvent) -> SystemEvent:
        payload = event.model_copy(update={"created_at": utc_now()})
        with self._connect() as conn:
            conn.execute(
                """
                insert into system_event (
                    event_id, session_id, thread_id, ticket_id, event_type,
                    payload_json, metadata_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
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
            conn.commit()
        return payload

    def list_events(self, session_id: str, limit: int = 100) -> list[SystemEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select event_id, session_id, thread_id, ticket_id, event_type,
                       payload_json, metadata_json, created_at
                from system_event
                where session_id = ?
                order by created_at asc, event_id asc
                limit ?
                """,
                (session_id, limit),
            ).fetchall()
        return [event for row in rows if (event := self._row_to_event(row)) is not None]

    @staticmethod
    def _row_to_event(row: sqlite3.Row | None) -> SystemEvent | None:
        if row is None:
            return None
        return SystemEvent(
            event_id=row["event_id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            ticket_id=row["ticket_id"],
            event_type=row["event_type"],
            payload=json.loads(row["payload_json"]),
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
        )
