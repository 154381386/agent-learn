from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from ..session.models import utc_now

ProcessMemoryEventType = Literal[
    "routing_decision",
    "clarification_created",
    "clarification_answered",
    "approval_requested",
    "approval_decided",
    "execution_result",
    "verification_result",
    "manual_intervention",
    "run_summary",
]


class ProcessMemoryEntry(BaseModel):
    memory_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    thread_id: str
    ticket_id: str
    event_type: ProcessMemoryEventType
    stage: str
    source: str
    summary: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    refs: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


class ProcessMemorySummary(BaseModel):
    latest_routing: Dict[str, Any] | None = None
    latest_clarification: Dict[str, Any] | None = None
    latest_approval: Dict[str, Any] | None = None
    latest_execution: Dict[str, Any] | None = None
    unresolved_items: List[Dict[str, Any]] = Field(default_factory=list)
    recent_entries: List[Dict[str, Any]] = Field(default_factory=list)


class IncidentCase(BaseModel):
    case_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    thread_id: str
    ticket_id: str
    service: str = ""
    cluster: str = ""
    namespace: str = ""
    current_agent: str = ""
    symptom: str = ""
    root_cause: str = ""
    key_evidence: List[str] = Field(default_factory=list)
    final_action: str = ""
    approval_required: bool = False
    verification_passed: Optional[bool] = None
    final_conclusion: str = ""
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    closed_at: Optional[str] = None
