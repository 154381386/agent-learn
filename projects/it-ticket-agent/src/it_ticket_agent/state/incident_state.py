from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .models import (
    ApprovedAction,
    ApprovalProposal,
    ExecutionResult,
    RAGContextBundle,
    SubAgentResult,
    VerificationPlan,
    VerificationResult,
)


class IncidentState(BaseModel):
    ticket_id: str
    user_id: str
    message: str
    thread_id: Optional[str] = None
    service: Optional[str] = None
    cluster: str = "prod-shanghai-1"
    namespace: str = "default"
    channel: str = "feishu"
    status: str = "received"
    routing: Dict[str, Any] = Field(default_factory=dict)
    shared_context: Dict[str, Any] = Field(default_factory=dict)
    rag_context: Optional[RAGContextBundle] = None
    subagent_results: List[SubAgentResult] = Field(default_factory=list)
    approval_proposals: List[ApprovalProposal] = Field(default_factory=list)
    approved_actions: List[ApprovedAction] = Field(default_factory=list)
    execution_results: List[ExecutionResult] = Field(default_factory=list)
    verification_plan: Optional[VerificationPlan] = None
    verification_results: List[VerificationResult] = Field(default_factory=list)
    final_summary: Optional[str] = None
    final_message: Optional[str] = None
    open_questions: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
