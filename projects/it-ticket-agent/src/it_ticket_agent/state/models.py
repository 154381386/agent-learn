from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


RiskLevel = Literal["low", "medium", "high", "critical"]
VerificationStatus = Literal["not_run", "passed", "failed", "inconclusive"]
ExecutionStatus = Literal["pending", "completed", "failed", "skipped"]
ApprovalStatus = Literal["approved", "rejected", "cancelled"]


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


class SimilarIncidentCase(BaseModel):
    case_id: str = ""
    service: str = ""
    failure_mode: str = ""
    root_cause_taxonomy: str = ""
    signal_pattern: str = ""
    action_pattern: str = ""
    symptom: str = ""
    root_cause: str = ""
    final_action: str = ""
    approval_required: bool = False
    verification_passed: Optional[bool] = None
    summary: str = ""
    recall_source: str = ""
    recall_score: float = 0.0


class RetrievalSubquery(BaseModel):
    query: str
    target: Literal["knowledge", "cases", "both"] = "both"
    reason: str = ""
    failure_mode: str = ""
    root_cause_taxonomy: str = ""


class RetrievalExpansion(BaseModel):
    subqueries: List[RetrievalSubquery] = Field(default_factory=list)
    added_rag_hits: int = 0
    added_case_hits: int = 0
    missing_evidence: List[str] = Field(default_factory=list)


class SkillCategory(BaseModel):
    name: str
    description: str
    skill_count: int = 0
    match_keywords: List[str] = Field(default_factory=list)


class SkillSignature(BaseModel):
    name: str
    params: str
    description: str
    risk_level: RiskLevel = "low"
    category: str
    executor: str = ""
    planning_mode: str = ""
    tool_names: List[str] = Field(default_factory=list)
    when_to_use: str = ""
    sop_summary: str = ""
    pack_name: str = ""
    guide_path: str = ""


class ContextSnapshot(BaseModel):
    request: Dict[str, Any] = Field(default_factory=dict)
    rag_context: Optional[RAGContextBundle] = None
    similar_cases: List[SimilarIncidentCase] = Field(default_factory=list)
    live_signals: Dict[str, Any] = Field(default_factory=dict)
    context_quality: float = 0.0
    available_skills: List[SkillSignature] = Field(default_factory=list)
    matched_skill_categories: List[str] = Field(default_factory=list)
    retrieval_expansion: RetrievalExpansion = Field(default_factory=RetrievalExpansion)


class VerificationStep(BaseModel):
    skill_name: str
    params: Dict[str, Any] = Field(default_factory=dict)
    purpose: str


class Hypothesis(BaseModel):
    hypothesis_id: str
    root_cause: str
    confidence_prior: float = 0.0
    verification_plan: List[VerificationStep] = Field(default_factory=list)
    expected_evidence: str = ""
    recommended_action: str = ""
    action_risk: RiskLevel = "low"
    action_params: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SkillResult(BaseModel):
    skill_name: str
    status: str
    summary: str
    evidence: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    skill: str
    purpose: str
    result: Dict[str, Any] = Field(default_factory=dict)
    matches_expected: bool = False


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
    hypothesis_id: str = ""
    root_cause: str = ""
    confidence: float = 0.0
    evidence_strength: float = 0.0
    evidence_items: List[EvidenceItem] = Field(default_factory=list)
    recommended_action: str = ""
    action_risk: RiskLevel = "low"
    action_params: Dict[str, Any] = Field(default_factory=dict)
    status: VerificationStatus = "not_run"
    summary: str
    checks_passed: List[str] = Field(default_factory=list)
    checks_failed: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RankedResult(BaseModel):
    primary: Optional[VerificationResult] = None
    secondary: List[VerificationResult] = Field(default_factory=list)
    rejected: List[VerificationResult] = Field(default_factory=list)
    ranking_metadata: Dict[str, Any] = Field(default_factory=dict)
