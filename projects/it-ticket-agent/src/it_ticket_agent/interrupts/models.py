from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

InterruptType = Literal["clarification", "approval", "external_event"]
InterruptStatus = Literal["pending", "answered", "cancelled", "expired"]
InterruptSource = Literal["clarification", "approval", "external_event"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InterruptRequest(BaseModel):
    interrupt_id: str
    session_id: str
    ticket_id: str
    type: InterruptType
    source: InterruptSource
    reason: str
    question: str
    expected_input_schema: Dict[str, Any] = Field(default_factory=dict)
    status: InterruptStatus = "pending"
    resume_token: str
    timeout_at: Optional[str] = None
    answer_payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    resolved_at: Optional[str] = None
