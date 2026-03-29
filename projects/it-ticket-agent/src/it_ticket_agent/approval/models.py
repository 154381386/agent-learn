from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high", "critical"]
ApprovalStatus = Literal["pending", "approved", "rejected"]
PolicyDecision = Literal["requires_approval", "auto_approve", "reject"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalVerificationPlan(BaseModel):
    summary: str = ""
    steps: List[str] = Field(default_factory=list)
    window_minutes: int = 15
    success_signals: List[str] = Field(default_factory=list)


class ApprovalProposal(BaseModel):
    proposal_id: str
    agent: str
    action: str
    resource: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = "low"
    reason: str
    expected_outcome: str = ""
    verification_plan: ApprovalVerificationPlan = Field(default_factory=ApprovalVerificationPlan)
    source_refs: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)

    @property
    def dedupe_key(self) -> str:
        resource = self.resource.strip().lower()
        return f"{self.action.strip().lower()}::{resource}::{self._stable_params()}"

    def _stable_params(self) -> str:
        if not self.params:
            return "{}"
        items = sorted((str(key), repr(value)) for key, value in self.params.items())
        return "|".join(f"{key}={value}" for key, value in items)


class ApprovalPolicyResult(BaseModel):
    proposal_id: str
    decision: PolicyDecision
    reasons: List[str] = Field(default_factory=list)
    normalized_risk: RiskLevel = "low"


class ApprovalRequest(BaseModel):
    approval_id: str
    ticket_id: str
    thread_id: str
    status: ApprovalStatus = "pending"
    proposals: List[ApprovalProposal] = Field(default_factory=list)
    highest_risk: RiskLevel = "low"
    summary: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)
    approver_id: Optional[str] = None
    comment: Optional[str] = None
    decided_at: Optional[str] = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class ApprovalDecisionRecord(BaseModel):
    approval_id: str
    approved: bool
    approver_id: str
    comment: Optional[str] = None
    decided_at: str = Field(default_factory=utc_now)


class ApprovedAction(BaseModel):
    approval_id: str
    proposal_id: str
    action: str
    resource: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = "low"
    reason: str = ""
    expected_outcome: str = ""
    verification_plan: ApprovalVerificationPlan = Field(default_factory=ApprovalVerificationPlan)
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    comment: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ApprovalGateInput(BaseModel):
    ticket_id: str
    thread_id: str
    proposals: List[ApprovalProposal] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)


class ApprovalGateResult(BaseModel):
    approval_request: Optional[ApprovalRequest] = None
    approved_actions: List[ApprovedAction] = Field(default_factory=list)
    rejected_proposals: List[ApprovalProposal] = Field(default_factory=list)
    auto_approved_proposals: List[ApprovalProposal] = Field(default_factory=list)
    policy_results: List[ApprovalPolicyResult] = Field(default_factory=list)


class ApprovalAuditEvent(BaseModel):
    approval_id: str
    event_type: Literal["created", "decision_recorded"]
    actor_id: str = "system"
    detail: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
