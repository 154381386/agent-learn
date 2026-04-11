from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RequestContext(BaseModel):
    ticket_id: str
    session_id: str
    thread_id: str
    user_id: str
    message: str
    service: Optional[str] = None
    cluster: str
    namespace: str
    channel: str
    entrypoint: str


class SessionSnapshot(BaseModel):
    session_id: str
    thread_id: str
    status: str
    current_stage: str
    latest_approval_id: Optional[str] = None
    pending_interrupt_id: Optional[str] = None
    last_checkpoint_id: Optional[str] = None
    incident_state: Dict[str, Any] = Field(default_factory=dict)
    recent_turns: List[Dict[str, Any]] = Field(default_factory=list)


class PendingInterruptContext(BaseModel):
    interrupt_id: str
    type: str
    reason: str
    question: str
    expected_input_schema: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(BaseModel):
    routing: Dict[str, Any] = Field(default_factory=dict)
    rag_context: Optional[Dict[str, Any]] = None
    approval_proposals: List[Dict[str, Any]] = Field(default_factory=list)
    approved_actions: List[Dict[str, Any]] = Field(default_factory=list)
    execution_results: List[Dict[str, Any]] = Field(default_factory=list)
    verification_results: List[Dict[str, Any]] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)


class ExecutionBudget(BaseModel):
    max_recent_turns: int = 5
    max_tool_results: int = 5
    max_rag_hits: int = 5
    include_raw_payloads: bool = False
    budget_mode: str = "minimal"


class ExecutionContext(BaseModel):
    request_context: RequestContext
    session_snapshot: SessionSnapshot
    pending_interrupt: Optional[PendingInterruptContext] = None
    evidence_bundle: EvidenceBundle = Field(default_factory=EvidenceBundle)
    memory_summary: Dict[str, Any] = Field(default_factory=dict)
    execution_budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
