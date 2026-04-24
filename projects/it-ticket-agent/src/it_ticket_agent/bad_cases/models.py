from __future__ import annotations

from typing import Any, Dict, List, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from ..session.models import utc_now

BadCaseSeverity = Literal["low", "medium", "high", "critical"]
BadCaseExportStatus = Literal["pending", "exported", "merged", "ignored"]


class BadCaseCandidate(BaseModel):
    candidate_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    thread_id: str
    ticket_id: str
    source: str = ""
    reason_codes: List[str] = Field(default_factory=list)
    severity: BadCaseSeverity = "low"
    request_payload: Dict[str, Any] = Field(default_factory=dict)
    response_payload: Dict[str, Any] = Field(default_factory=dict)
    incident_state_snapshot: Dict[str, Any] = Field(default_factory=dict)
    context_snapshot: Dict[str, Any] = Field(default_factory=dict)
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    retrieval_expansion: Dict[str, Any] = Field(default_factory=dict)
    human_feedback: Dict[str, Any] = Field(default_factory=dict)
    conversation_turns: List[Dict[str, Any]] = Field(default_factory=list)
    system_events: List[Dict[str, Any]] = Field(default_factory=list)
    export_status: BadCaseExportStatus = "pending"
    export_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
