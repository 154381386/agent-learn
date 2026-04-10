from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class FieldRequirement(BaseModel):
    name: str
    type: Literal["string", "enum", "integer", "number", "boolean", "timestamp"] = "string"
    description: str
    required: bool = True
    values: List[str] = Field(default_factory=list)
    priority: Literal["critical", "high", "low"] = "high"


class ClarificationField(BaseModel):
    name: str
    type: Literal["string", "enum", "integer", "number", "boolean", "timestamp"] = "string"
    description: str
    required: bool = True
    values: List[str] = Field(default_factory=list)
    priority: Literal["critical", "high", "low"] = "high"
    requested_by: List[str] = Field(default_factory=list)


class ClarificationRequest(BaseModel):
    agent_name: str
    domain: str
    reason: str
    question: str
    fields: List[ClarificationField] = Field(default_factory=list)


class ValidationResult(BaseModel):
    valid: bool = True
    missing_fields: List[FieldRequirement] = Field(default_factory=list)


class AgentDescriptor(BaseModel):
    agent_name: str
    domain: str
    display_name: str
    description: str = ""
    required_fields: List[FieldRequirement] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    routing_keywords: List[str] = Field(default_factory=list)
    tool_names: List[str] = Field(default_factory=list)


class AgentAction(BaseModel):
    action: str
    risk: str = "low"
    reason: str
    params: Dict[str, Any] = Field(default_factory=dict)


class AgentFinding(BaseModel):
    title: str
    detail: str
    severity: str = "info"


class TaskEnvelope(BaseModel):
    task_id: str
    ticket_id: str
    goal: str
    mode: Literal["router", "fan_out", "pipeline"] = "router"
    shared_context: Dict[str, Any] = Field(default_factory=dict)
    upstream_findings: List[Dict[str, Any]] = Field(default_factory=list)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    priority: str = "normal"
    deadline: Optional[str] = None
    allowed_actions: List[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    agent_name: str
    domain: str
    status: str
    summary: str
    execution_path: str = "fallback"
    findings: List[AgentFinding] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    tool_results: List[Dict[str, Any]] = Field(default_factory=list)
    recommended_actions: List[AgentAction] = Field(default_factory=list)
    risk_level: str = "low"
    confidence: float = 0.0
    open_questions: List[str] = Field(default_factory=list)
    needs_handoff: bool = False
    raw_refs: List[str] = Field(default_factory=list)
    clarification_request: Optional[ClarificationRequest] = None


class RoutingDecision(BaseModel):
    agent_name: str
    mode: Literal["router", "fan_out", "pipeline"] = "router"
    route_source: str = "rule"
    reason: str
    confidence: float = 0.0
    candidate_agents: List[str] = Field(default_factory=list)


RouteIntent = Literal["direct_answer", "hypothesis_graph"]


class SmartRouterDecision(BaseModel):
    intent: RouteIntent
    route_source: str = "rule"
    reason: str
    confidence: float = 0.0
    matched_signals: List[str] = Field(default_factory=list)
    rag_score: float = 0.0
    should_respond_directly: bool = False
