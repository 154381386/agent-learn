from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from ..session.models import utc_now

AgentEventType = Literal[
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
ProcessMemoryEventType = AgentEventType

CaseStatus = Literal["draft", "pending_review", "verified", "rejected"]
PlaybookStatus = Literal["draft", "pending_review", "verified", "rejected", "retired"]


class AgentEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(
        default_factory=lambda: str(uuid4()),
        validation_alias=AliasChoices("event_id", "memory_id"),
    )
    session_id: str
    thread_id: str
    ticket_id: str
    event_type: AgentEventType
    stage: str
    source: str
    summary: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    refs: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)

    @property
    def memory_id(self) -> str:
        return self.event_id


class AgentEventSummary(BaseModel):
    latest_routing: Dict[str, Any] | None = None
    latest_clarification: Dict[str, Any] | None = None
    latest_approval: Dict[str, Any] | None = None
    latest_execution: Dict[str, Any] | None = None
    unresolved_items: List[Dict[str, Any]] = Field(default_factory=list)
    recent_entries: List[Dict[str, Any]] = Field(default_factory=list)


ProcessMemoryEntry = AgentEvent
ProcessMemorySummary = AgentEventSummary


class IncidentCase(BaseModel):
    case_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    thread_id: str
    ticket_id: str
    service: str = ""
    cluster: str = ""
    namespace: str = ""
    current_agent: str = ""
    case_status: CaseStatus = "pending_review"
    failure_mode: str = ""
    root_cause_taxonomy: str = ""
    signal_pattern: str = ""
    action_pattern: str = ""
    symptom: str = ""
    root_cause: str = ""
    key_evidence: List[str] = Field(default_factory=list)
    final_action: str = ""
    approval_required: bool = False
    verification_passed: Optional[bool] = None
    human_verified: bool = False
    hypothesis_accuracy: Dict[str, float] = Field(default_factory=dict)
    actual_root_cause_hypothesis: str = ""
    selected_hypothesis_id: str = ""
    selected_ranker_features: Dict[str, float] = Field(default_factory=dict)
    final_conclusion: str = ""
    reviewed_by: str = ""
    reviewed_at: Optional[str] = None
    review_note: str = ""
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    closed_at: Optional[str] = None


class DiagnosisPlaybook(BaseModel):
    playbook_id: str = Field(default_factory=lambda: str(uuid4()))
    version: int = 1
    title: str = ""
    status: PlaybookStatus = "pending_review"
    human_verified: bool = False
    service_type: str = ""
    failure_modes: List[str] = Field(default_factory=list)
    environments: List[str] = Field(default_factory=list)
    trigger_conditions: List[str] = Field(default_factory=list)
    signal_patterns: List[str] = Field(default_factory=list)
    negative_conditions: List[str] = Field(default_factory=list)
    required_entities: List[str] = Field(default_factory=list)
    diagnostic_goal: str = ""
    diagnostic_steps: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_requirements: List[str] = Field(default_factory=list)
    guardrails: List[str] = Field(default_factory=list)
    common_false_positives: List[str] = Field(default_factory=list)
    source_case_ids: List[str] = Field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    last_eval_passed: Optional[bool] = None
    reviewed_by: str = ""
    reviewed_at: Optional[str] = None
    review_note: str = ""
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    retired_at: Optional[str] = None
