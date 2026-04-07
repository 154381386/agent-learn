from __future__ import annotations

from typing import Any, Optional

from .session import ConversationSession, ConversationTurn
from .session.store import SessionStoreV2, _UNSET


class SessionStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.v2_store = SessionStoreV2(db_path)

    def create(self, session: ConversationSession | dict[str, Any]) -> dict[str, Any]:
        record = session if isinstance(session, ConversationSession) else ConversationSession.model_validate(session)
        saved = self.v2_store.create_session(record)
        return saved.model_dump()

    def get(self, session_id: str) -> Optional[dict[str, Any]]:
        record = self.v2_store.get_session(session_id)
        return None if record is None else record.model_dump()

    def get_by_thread_id(self, thread_id: str) -> Optional[dict[str, Any]]:
        record = self.v2_store.get_session_by_thread_id(thread_id)
        return None if record is None else record.model_dump()

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
        record = self.v2_store.update_session_state(
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

    def update_status(
        self,
        session_id: str,
        *,
        status: str,
        current_stage: str,
        current_agent: Optional[str] | object = _UNSET,
        latest_approval_id: Optional[str] = None,
        pending_interrupt_id: Optional[str] = None,
        last_checkpoint_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        record = self.v2_store.update_status(
            session_id,
            status=status,
            current_stage=current_stage,
            current_agent=current_agent,
            latest_approval_id=latest_approval_id,
            pending_interrupt_id=pending_interrupt_id,
            last_checkpoint_id=last_checkpoint_id,
        )
        return None if record is None else record.model_dump()

    def touch(self, session_id: str) -> Optional[dict[str, Any]]:
        record = self.v2_store.touch(session_id)
        return None if record is None else record.model_dump()

    def append_turn(self, turn: ConversationTurn | dict[str, Any]) -> dict[str, Any]:
        record = turn if isinstance(turn, ConversationTurn) else ConversationTurn.model_validate(turn)
        saved = self.v2_store.append_turn(record)
        return saved.model_dump()

    def list_turns(self, session_id: str, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        return [record.model_dump() for record in self.v2_store.list_turns(session_id, limit=limit)]
