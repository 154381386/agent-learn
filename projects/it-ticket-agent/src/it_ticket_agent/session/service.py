from __future__ import annotations

from typing import Any, Optional

from ..schemas import TicketRequest
from .models import ConversationSession, ConversationTurn


class SessionService:
    def __init__(self, store) -> None:
        self.store = store

    def create_initial_session(
        self,
        *,
        session_id: str,
        thread_id: str,
        request: TicketRequest,
        incident_state,
        current_agent: str | None = None,
        session_memory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.store.create(
            ConversationSession(
                session_id=session_id,
                thread_id=thread_id,
                ticket_id=request.ticket_id,
                user_id=request.user_id,
                status="active",
                current_stage="ingest",
                current_agent=current_agent,
                incident_state=incident_state,
                session_memory=dict(session_memory or {}),
            )
        )

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        return self.store.get(session_id)

    def get_session_by_thread_id(self, thread_id: str) -> Optional[dict[str, Any]]:
        return self.store.get_by_thread_id(thread_id)

    def update_session_state(
        self,
        session_id: str,
        *,
        incident_state: dict[str, Any],
        status: str,
        current_stage: str,
        current_agent: Optional[str] | object = None,
        latest_approval_id: Optional[str] = None,
        pending_interrupt_id: Optional[str] = None,
        last_checkpoint_id: Optional[str] = None,
        session_memory: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        payload: dict[str, Any] = {
            "incident_state": incident_state,
            "status": status,
            "current_stage": current_stage,
            "latest_approval_id": latest_approval_id,
            "pending_interrupt_id": pending_interrupt_id,
            "last_checkpoint_id": last_checkpoint_id,
            "session_memory": session_memory,
            "metadata": metadata,
        }
        if current_agent is not None:
            payload["current_agent"] = current_agent
        return self.store.update_state(session_id, **payload)

    def update_session_status(
        self,
        session_id: str,
        *,
        status: str,
        current_stage: str,
        current_agent: Optional[str] | object = None,
        latest_approval_id: Optional[str] = None,
        pending_interrupt_id: Optional[str] = None,
        last_checkpoint_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        payload: dict[str, Any] = {
            "status": status,
            "current_stage": current_stage,
            "latest_approval_id": latest_approval_id,
            "pending_interrupt_id": pending_interrupt_id,
            "last_checkpoint_id": last_checkpoint_id,
        }
        if current_agent is not None:
            payload["current_agent"] = current_agent
        if hasattr(self.store, "update_status"):
            return self.store.update_status(session_id, **payload)
        current = self.store.get(session_id)
        if current is None:
            return None
        return self.store.update_state(
            session_id,
            incident_state=dict(current.get("incident_state") or {}),
            **payload,
        )

    def append_turn(self, turn: ConversationTurn | dict[str, Any]) -> dict[str, Any]:
        return self.store.append_turn(turn)

    def list_turns(self, session_id: str, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        return self.store.list_turns(session_id, limit=limit)

    def touch(self, session_id: str) -> Optional[dict[str, Any]]:
        return self.store.touch(session_id)
