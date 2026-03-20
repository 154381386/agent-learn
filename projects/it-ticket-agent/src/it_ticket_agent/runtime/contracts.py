from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


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
    findings: List[AgentFinding] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    tool_results: List[Dict[str, Any]] = Field(default_factory=list)
    recommended_actions: List[AgentAction] = Field(default_factory=list)
    risk_level: str = "low"
    confidence: float = 0.0
    open_questions: List[str] = Field(default_factory=list)
    needs_handoff: bool = False
    raw_refs: List[str] = Field(default_factory=list)


class RoutingDecision(BaseModel):
    agent_name: str
    mode: Literal["router", "fan_out", "pipeline"] = "router"
    reason: str
    confidence: float = 0.0
    candidate_agents: List[str] = Field(default_factory=list)
