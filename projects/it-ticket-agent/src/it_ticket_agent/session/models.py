from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from ..state.incident_state import IncidentState

SessionStatus = Literal["active", "awaiting_approval", "awaiting_clarification", "completed", "failed"]
SessionStage = Literal[
    "ingest",
    "routing",
    "domain_agent",
    "approval_gate",
    "awaiting_approval",
    "awaiting_clarification",
    "approval_resume",
    "finalize",
]
TurnRole = Literal["user", "assistant", "system", "tool"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationSession(BaseModel):
    session_id: str
    thread_id: str
    ticket_id: str
    user_id: str
    status: SessionStatus = "active"
    current_stage: SessionStage = "ingest"
    current_agent: Optional[str] = None
    incident_state: IncidentState
    latest_approval_id: Optional[str] = None
    pending_interrupt_id: Optional[str] = None
    last_checkpoint_id: Optional[str] = None
    session_memory: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    last_active_at: str = Field(default_factory=utc_now)
    closed_at: Optional[str] = None


class ConversationTurn(BaseModel):
    turn_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    role: TurnRole
    content: str
    structured_payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
