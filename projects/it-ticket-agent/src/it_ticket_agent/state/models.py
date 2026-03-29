from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


RiskLevel = Literal["low", "medium", "high", "critical"]
VerificationStatus = Literal["not_run", "passed", "failed", "inconclusive"]
ExecutionStatus = Literal["pending", "completed", "failed", "skipped"]
ApprovalStatus = Literal["approved", "rejected", "cancelled"]


class IncidentFinding(BaseModel):
    title: str
    detail: str
    severity: str = "info"


class ToolResultSnapshot(BaseModel):
    tool_name: str
    status: str
    summary: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[str] = Field(default_factory=list)
    risk: RiskLevel = "low"


class KnowledgeHit(BaseModel):
    chunk_id: str = ""
    title: str = ""
    section: str = ""
    path: str = ""
    category: str = ""
    score: float = 0.0
    snippet: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RAGContextBundle(BaseModel):
    query: str = ""
    query_type: str = "search"
    should_respond_directly: bool = False
    direct_answer: Optional[str] = None
    hits: List[KnowledgeHit] = Field(default_factory=list)
    context: List[KnowledgeHit] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)
    facts: List[Dict[str, Any]] = Field(default_factory=list)
    index_info: Dict[str, Any] = Field(default_factory=dict)
    raw_response: Dict[str, Any] = Field(default_factory=dict)


class ApprovalProposal(BaseModel):
    proposal_id: str
    source_agent: str
    action: str
    risk: RiskLevel = "low"
    reason: str
    params: Dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = True
    title: Optional[str] = None
    target: Optional[str] = None
    evidence: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ApprovedAction(BaseModel):
    proposal_id: Optional[str] = None
    approval_id: Optional[str] = None
    action: str
    risk: RiskLevel = "low"
    reason: str
    params: Dict[str, Any] = Field(default_factory=dict)
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    comment: Optional[str] = None
    status: ApprovalStatus = "approved"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    action: str
    status: ExecutionStatus = "pending"
    summary: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[str] = Field(default_factory=list)
    risk: RiskLevel = "low"
    executor: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VerificationPlan(BaseModel):
    objective: str
    checks: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    verifier: str = "default"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    status: VerificationStatus = "not_run"
    summary: str
    checks_passed: List[str] = Field(default_factory=list)
    checks_failed: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SubAgentResult(BaseModel):
    agent_name: str
    domain: str
    status: str
    summary: str
    execution_path: str = "legacy"
    findings: List[IncidentFinding] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    tool_results: List[ToolResultSnapshot] = Field(default_factory=list)
    approval_proposals: List[ApprovalProposal] = Field(default_factory=list)
    risk_level: RiskLevel = "low"
    confidence: float = 0.0
    open_questions: List[str] = Field(default_factory=list)
    needs_handoff: bool = False
    raw_refs: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
