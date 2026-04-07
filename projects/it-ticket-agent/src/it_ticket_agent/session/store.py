from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Optional

from .models import ConversationSession, ConversationTurn, SessionStage, SessionStatus, utc_now


_UNSET = object()


class SessionStoreV2:
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
                create table if not exists conversation_session (
                    session_id text primary key,
                    thread_id text not null unique,
                    ticket_id text not null,
                    user_id text not null,
                    status text not null,
                    current_stage text not null,
                    current_agent text,
                    incident_state_json text not null,
                    latest_approval_id text,
                    pending_interrupt_id text,
                    last_checkpoint_id text,
                    session_memory_json text not null,
                    metadata_json text not null,
                    created_at text not null,
                    updated_at text not null,
                    last_active_at text not null
                    ,closed_at text
                )
                """
            )
            conn.execute(
                """
                create table if not exists conversation_turn (
                    turn_id text primary key,
                    session_id text not null,
                    role text not null,
                    content text not null,
                    structured_payload_json text not null,
                    created_at text not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_conversation_turn_session_created_at
                on conversation_turn (session_id, created_at, turn_id)
                """
            )
            columns = {row[1] for row in conn.execute("pragma table_info(conversation_session)").fetchall()}
            if "pending_interrupt_id" not in columns:
                conn.execute("alter table conversation_session add column pending_interrupt_id text")
            if "last_checkpoint_id" not in columns:
                conn.execute("alter table conversation_session add column last_checkpoint_id text")
            if "current_agent" not in columns:
                conn.execute("alter table conversation_session add column current_agent text")
            if "session_memory_json" not in columns:
                conn.execute("alter table conversation_session add column session_memory_json text not null default '{}' ")
            if "closed_at" not in columns:
                conn.execute("alter table conversation_session add column closed_at text")
            conn.commit()

    def create_session(self, session: ConversationSession) -> ConversationSession:
        now = utc_now()
        payload = session.model_copy(
            update={
                "created_at": now,
                "updated_at": now,
                "last_active_at": now,
            }
        )
        with self._connect() as conn:
            conn.execute(
                """
                insert or replace into conversation_session (
                    session_id, thread_id, ticket_id, user_id, status, current_stage, current_agent,
                    incident_state_json, latest_approval_id, pending_interrupt_id, last_checkpoint_id, session_memory_json, metadata_json,
                    created_at, updated_at, last_active_at, closed_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.session_id,
                    payload.thread_id,
                    payload.ticket_id,
                    payload.user_id,
                    payload.status,
                    payload.current_stage,
                    payload.current_agent,
                    json.dumps(payload.incident_state.model_dump(), ensure_ascii=False),
                    payload.latest_approval_id,
                    payload.pending_interrupt_id,
                    payload.last_checkpoint_id,
                    json.dumps(payload.session_memory, ensure_ascii=False),
                    json.dumps(payload.metadata, ensure_ascii=False),
                    payload.created_at,
                    payload.updated_at,
                    payload.last_active_at,
                    payload.closed_at,
                ),
            )
            conn.commit()
        return payload

    def get_session(self, session_id: str) -> Optional[ConversationSession]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select session_id, thread_id, ticket_id, user_id, status, current_stage, current_agent,
                       incident_state_json, latest_approval_id, pending_interrupt_id, last_checkpoint_id, session_memory_json, metadata_json,
                       created_at, updated_at, last_active_at, closed_at
                from conversation_session
                where session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_session(row)

    def get_session_by_thread_id(self, thread_id: str) -> Optional[ConversationSession]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select session_id, thread_id, ticket_id, user_id, status, current_stage, current_agent,
                       incident_state_json, latest_approval_id, pending_interrupt_id, last_checkpoint_id, session_memory_json, metadata_json,
                       created_at, updated_at, last_active_at, closed_at
                from conversation_session
                where thread_id = ?
                """,
                (thread_id,),
            ).fetchone()
        return self._row_to_session(row)

    def update_session_state(
        self,
        session_id: str,
        *,
        incident_state: dict[str, Any],
        status: SessionStatus,
        current_stage: SessionStage,
        current_agent: Optional[str] | object = _UNSET,
        latest_approval_id: Optional[str] | object = _UNSET,
        pending_interrupt_id: Optional[str] | object = _UNSET,
        last_checkpoint_id: Optional[str] | object = _UNSET,
        session_memory: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[ConversationSession]:
        existing = self.get_session(session_id)
        if existing is None:
            return None
        now = utc_now()
        next_metadata = dict(existing.metadata)
        if metadata:
            next_metadata.update(metadata)
        agent = existing.current_agent if current_agent is _UNSET else current_agent
        latest = existing.latest_approval_id if latest_approval_id is _UNSET else latest_approval_id
        pending_interrupt = existing.pending_interrupt_id if pending_interrupt_id is _UNSET else pending_interrupt_id
        latest_checkpoint = existing.last_checkpoint_id if last_checkpoint_id is _UNSET else last_checkpoint_id
        closed_at = existing.closed_at
        if status in {"completed", "failed"}:
            closed_at = existing.closed_at or now
        elif status == "active":
            closed_at = None
        next_session_memory = dict(existing.session_memory)
        if session_memory:
            next_session_memory.update(session_memory)
        with self._connect() as conn:
            conn.execute(
                """
                update conversation_session
                set status = ?,
                    current_stage = ?,
                    current_agent = ?,
                    incident_state_json = ?,
                    latest_approval_id = ?,
                    pending_interrupt_id = ?,
                    last_checkpoint_id = ?,
                    session_memory_json = ?,
                    metadata_json = ?,
                    updated_at = ?,
                    last_active_at = ?,
                    closed_at = ?
                where session_id = ?
                """,
                (
                    status,
                    current_stage,
                    agent,
                    json.dumps(incident_state, ensure_ascii=False),
                    latest,
                    pending_interrupt,
                    latest_checkpoint,
                    json.dumps(next_session_memory, ensure_ascii=False),
                    json.dumps(next_metadata, ensure_ascii=False),
                    now,
                    now,
                    closed_at,
                    session_id,
                ),
            )
            conn.commit()
        return self.get_session(session_id)

    def update_status(
        self,
        session_id: str,
        *,
        status: SessionStatus,
        current_stage: SessionStage,
        current_agent: Optional[str] | object = _UNSET,
        latest_approval_id: Optional[str] = None,
        pending_interrupt_id: Optional[str] = None,
        last_checkpoint_id: Optional[str] = None,
    ) -> Optional[ConversationSession]:
        existing = self.get_session(session_id)
        if existing is None:
            return None
        return self.update_session_state(
            session_id,
            incident_state=existing.incident_state.model_dump(),
            status=status,
            current_stage=current_stage,
            current_agent=current_agent,
            latest_approval_id=latest_approval_id,
            pending_interrupt_id=pending_interrupt_id,
            last_checkpoint_id=last_checkpoint_id,
        )

    def touch(self, session_id: str) -> Optional[ConversationSession]:
        existing = self.get_session(session_id)
        if existing is None:
            return None
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                update conversation_session
                set updated_at = ?, last_active_at = ?
                where session_id = ?
                """,
                (now, now, session_id),
            )
            conn.commit()
        return self.get_session(session_id)

    def append_turn(self, turn: ConversationTurn) -> ConversationTurn:
        payload = turn.model_copy(update={"created_at": utc_now()})
        with self._connect() as conn:
            conn.execute(
                """
                insert into conversation_turn (
                    turn_id, session_id, role, content, structured_payload_json, created_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.turn_id,
                    payload.session_id,
                    payload.role,
                    payload.content,
                    json.dumps(payload.structured_payload, ensure_ascii=False),
                    payload.created_at,
                ),
            )
            conn.commit()
        return payload

    def list_turns(self, session_id: str, *, limit: Optional[int] = None) -> list[ConversationTurn]:
        query = """
            select turn_id, session_id, role, content, structured_payload_json, created_at
            from conversation_turn
            where session_id = ?
            order by created_at asc, turn_id asc
        """
        params: tuple[Any, ...]
        if limit is not None:
            query += " limit ?"
            params = (session_id, limit)
        else:
            params = (session_id,)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [turn for row in rows if (turn := self._row_to_turn(row)) is not None]

    @staticmethod
    def _row_to_session(row: sqlite3.Row | None) -> Optional[ConversationSession]:
        if row is None:
            return None
        return ConversationSession(
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            ticket_id=row["ticket_id"],
            user_id=row["user_id"],
            status=row["status"],
            current_stage=row["current_stage"],
            current_agent=row["current_agent"],
            incident_state=json.loads(row["incident_state_json"]),
            latest_approval_id=row["latest_approval_id"],
            pending_interrupt_id=row["pending_interrupt_id"],
            last_checkpoint_id=row["last_checkpoint_id"],
            session_memory=json.loads(row["session_memory_json"]),
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_active_at=row["last_active_at"],
            closed_at=row["closed_at"],
        )

    @staticmethod
    def _row_to_turn(row: sqlite3.Row | None) -> Optional[ConversationTurn]:
        if row is None:
            return None
        return ConversationTurn(
            turn_id=row["turn_id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            structured_payload=json.loads(row["structured_payload_json"]),
            created_at=row["created_at"],
        )
