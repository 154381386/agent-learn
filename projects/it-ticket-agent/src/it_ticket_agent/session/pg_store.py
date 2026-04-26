from __future__ import annotations

import json
from typing import Any, Optional

from .models import ConversationSession, ConversationTurn, SessionStage, SessionStatus, utc_now
from ..storage.postgres import postgres_connection


_UNSET = object()


class PostgresSessionStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._init_db()

    def _init_db(self) -> None:
        with postgres_connection(self.dsn) as conn:
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
                    incident_state_json jsonb not null,
                    latest_approval_id text,
                    pending_interrupt_id text,
                    last_checkpoint_id text,
                    session_memory_json jsonb not null,
                    metadata_json jsonb not null,
                    created_at timestamptz not null,
                    updated_at timestamptz not null,
                    last_active_at timestamptz not null,
                    closed_at timestamptz
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
                    structured_payload_json jsonb not null,
                    created_at timestamptz not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_conversation_turn_session_created_at
                on conversation_turn (session_id, created_at, turn_id)
                """
            )

    def create(self, session: ConversationSession) -> dict[str, Any]:
        return self.create_session(session).model_dump()

    def create_session(self, session: ConversationSession) -> ConversationSession:
        now = utc_now()
        payload = session.model_copy(update={"created_at": now, "updated_at": now, "last_active_at": now})
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                insert into conversation_session (
                    session_id, thread_id, ticket_id, user_id, status, current_stage, current_agent,
                    incident_state_json, latest_approval_id, pending_interrupt_id, last_checkpoint_id,
                    session_memory_json, metadata_json, created_at, updated_at, last_active_at, closed_at
                ) values (
                    %(session_id)s, %(thread_id)s, %(ticket_id)s, %(user_id)s, %(status)s, %(current_stage)s, %(current_agent)s,
                    %(incident_state_json)s::jsonb, %(latest_approval_id)s, %(pending_interrupt_id)s, %(last_checkpoint_id)s,
                    %(session_memory_json)s::jsonb, %(metadata_json)s::jsonb, %(created_at)s, %(updated_at)s, %(last_active_at)s, %(closed_at)s
                )
                on conflict (session_id) do update set
                    thread_id = excluded.thread_id,
                    ticket_id = excluded.ticket_id,
                    user_id = excluded.user_id,
                    status = excluded.status,
                    current_stage = excluded.current_stage,
                    current_agent = excluded.current_agent,
                    incident_state_json = excluded.incident_state_json,
                    latest_approval_id = excluded.latest_approval_id,
                    pending_interrupt_id = excluded.pending_interrupt_id,
                    last_checkpoint_id = excluded.last_checkpoint_id,
                    session_memory_json = excluded.session_memory_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at,
                    last_active_at = excluded.last_active_at,
                    closed_at = excluded.closed_at
                """,
                {
                    "session_id": payload.session_id,
                    "thread_id": payload.thread_id,
                    "ticket_id": payload.ticket_id,
                    "user_id": payload.user_id,
                    "status": payload.status,
                    "current_stage": payload.current_stage,
                    "current_agent": payload.current_agent,
                    "incident_state_json": json.dumps(payload.incident_state.model_dump(), ensure_ascii=False),
                    "latest_approval_id": payload.latest_approval_id,
                    "pending_interrupt_id": payload.pending_interrupt_id,
                    "last_checkpoint_id": payload.last_checkpoint_id,
                    "session_memory_json": json.dumps(payload.session_memory, ensure_ascii=False),
                    "metadata_json": json.dumps(payload.metadata, ensure_ascii=False),
                    "created_at": payload.created_at,
                    "updated_at": payload.updated_at,
                    "last_active_at": payload.last_active_at,
                    "closed_at": payload.closed_at,
                },
            )
        return payload

    def get(self, session_id: str) -> Optional[dict[str, Any]]:
        record = self.get_session(session_id)
        return None if record is None else record.model_dump()

    def get_session(self, session_id: str) -> Optional[ConversationSession]:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                """
                select session_id, thread_id, ticket_id, user_id, status, current_stage, current_agent,
                       incident_state_json, latest_approval_id, pending_interrupt_id, last_checkpoint_id,
                       session_memory_json, metadata_json, created_at, updated_at, last_active_at, closed_at
                from conversation_session where session_id = %s
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_session(row)

    def get_by_thread_id(self, thread_id: str) -> Optional[dict[str, Any]]:
        record = self.get_session_by_thread_id(thread_id)
        return None if record is None else record.model_dump()

    def get_session_by_thread_id(self, thread_id: str) -> Optional[ConversationSession]:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                """
                select session_id, thread_id, ticket_id, user_id, status, current_stage, current_agent,
                       incident_state_json, latest_approval_id, pending_interrupt_id, last_checkpoint_id,
                       session_memory_json, metadata_json, created_at, updated_at, last_active_at, closed_at
                from conversation_session where thread_id = %s
                """,
                (thread_id,),
            ).fetchone()
        return self._row_to_session(row)


    def list_sessions(
        self,
        *,
        limit: int = 20,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        records = self.list_conversation_sessions(limit=limit, user_id=user_id, status=status)
        return [record.model_dump() for record in records]

    def list_conversation_sessions(
        self,
        *,
        limit: int = 20,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[ConversationSession]:
        conditions: list[str] = []
        params: list[Any] = []
        if user_id:
            conditions.append("user_id = %s")
            params.append(user_id)
        if status:
            conditions.append("status = %s")
            params.append(status)
        where_clause = f"where {' and '.join(conditions)}" if conditions else ""
        query = f"""
            select session_id, thread_id, ticket_id, user_id, status, current_stage, current_agent,
                   incident_state_json, latest_approval_id, pending_interrupt_id, last_checkpoint_id,
                   session_memory_json, metadata_json, created_at, updated_at, last_active_at, closed_at
            from conversation_session
            {where_clause}
            order by last_active_at desc, created_at desc, session_id desc
            limit %s
        """
        params.append(max(1, min(int(limit or 20), 50)))
        with postgres_connection(self.dsn) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [session for row in rows if (session := self._row_to_session(row)) is not None]

    def update_state(
        self,
        session_id: str,
        *,
        incident_state: dict[str, Any],
        status: str,
        current_stage: str,
        current_agent: Optional[str] | object = _UNSET,
        latest_approval_id: Optional[str] = None,
        pending_interrupt_id: Optional[str] = None,
        last_checkpoint_id: Optional[str] = None,
        session_memory: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        record = self.update_session_state(
            session_id,
            incident_state=incident_state,
            status=status,
            current_stage=current_stage,
            current_agent=current_agent,
            latest_approval_id=latest_approval_id,
            pending_interrupt_id=pending_interrupt_id,
            last_checkpoint_id=last_checkpoint_id,
            session_memory=session_memory,
            metadata=metadata,
        )
        return None if record is None else record.model_dump()

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
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                update conversation_session set
                    status = %(status)s,
                    current_stage = %(current_stage)s,
                    current_agent = %(current_agent)s,
                    incident_state_json = %(incident_state_json)s::jsonb,
                    latest_approval_id = %(latest_approval_id)s,
                    pending_interrupt_id = %(pending_interrupt_id)s,
                    last_checkpoint_id = %(last_checkpoint_id)s,
                    session_memory_json = %(session_memory_json)s::jsonb,
                    metadata_json = %(metadata_json)s::jsonb,
                    updated_at = %(updated_at)s,
                    last_active_at = %(last_active_at)s,
                    closed_at = %(closed_at)s
                where session_id = %(session_id)s
                """,
                {
                    "session_id": session_id,
                    "status": status,
                    "current_stage": current_stage,
                    "current_agent": agent,
                    "incident_state_json": json.dumps(incident_state, ensure_ascii=False),
                    "latest_approval_id": latest,
                    "pending_interrupt_id": pending_interrupt,
                    "last_checkpoint_id": latest_checkpoint,
                    "session_memory_json": json.dumps(next_session_memory, ensure_ascii=False),
                    "metadata_json": json.dumps(next_metadata, ensure_ascii=False),
                    "updated_at": now,
                    "last_active_at": now,
                    "closed_at": closed_at,
                },
            )
        return self.get_session(session_id)

    def touch(self, session_id: str) -> Optional[dict[str, Any]]:
        record = self.get_session(session_id)
        if record is None:
            return None
        now = utc_now()
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                "update conversation_session set updated_at = %s, last_active_at = %s where session_id = %s",
                (now, now, session_id),
            )
        touched = self.get_session(session_id)
        return None if touched is None else touched.model_dump()

    def append_turn(self, turn: ConversationTurn | dict[str, Any]) -> dict[str, Any]:
        record = turn if isinstance(turn, ConversationTurn) else ConversationTurn.model_validate(turn)
        saved = self.append_conversation_turn(record)
        return saved.model_dump()

    def append_conversation_turn(self, turn: ConversationTurn) -> ConversationTurn:
        payload = turn.model_copy(update={"created_at": utc_now()})
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                insert into conversation_turn (
                    turn_id, session_id, role, content, structured_payload_json, created_at
                ) values (%s, %s, %s, %s, %s::jsonb, %s)
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
        return payload

    def list_turns(self, session_id: str, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        return [record.model_dump() for record in self.list_conversation_turns(session_id, limit=limit)]

    def list_conversation_turns(self, session_id: str, *, limit: Optional[int] = None) -> list[ConversationTurn]:
        query = """
            select turn_id, session_id, role, content, structured_payload_json, created_at
            from conversation_turn where session_id = %s
            order by created_at asc, turn_id asc
        """
        params: tuple[Any, ...]
        if limit is not None:
            query += " limit %s"
            params = (session_id, limit)
        else:
            params = (session_id,)
        with postgres_connection(self.dsn) as conn:
            rows = conn.execute(query, params).fetchall()
        return [turn for row in rows if (turn := self._row_to_turn(row)) is not None]

    @staticmethod
    def _row_to_session(row: dict[str, Any] | None) -> Optional[ConversationSession]:
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
            incident_state=row["incident_state_json"],
            latest_approval_id=row["latest_approval_id"],
            pending_interrupt_id=row["pending_interrupt_id"],
            last_checkpoint_id=row["last_checkpoint_id"],
            session_memory=row["session_memory_json"],
            metadata=row["metadata_json"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_active_at=str(row["last_active_at"]),
            closed_at=str(row["closed_at"]) if row["closed_at"] is not None else None,
        )

    @staticmethod
    def _row_to_turn(row: dict[str, Any] | None) -> Optional[ConversationTurn]:
        if row is None:
            return None
        return ConversationTurn(
            turn_id=row["turn_id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            structured_payload=row["structured_payload_json"],
            created_at=str(row["created_at"]),
        )
