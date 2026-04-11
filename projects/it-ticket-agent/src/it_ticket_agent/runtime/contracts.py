from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

class TaskEnvelope(BaseModel):
    task_id: str
    ticket_id: str
    goal: str
    mode: Literal["pipeline"] = "pipeline"
    shared_context: Dict[str, Any] = Field(default_factory=dict)
    upstream_findings: List[Dict[str, Any]] = Field(default_factory=list)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    priority: str = "normal"
    deadline: Optional[str] = None
    allowed_actions: List[str] = Field(default_factory=list)


RouteIntent = Literal["direct_answer", "hypothesis_graph"]


class SmartRouterDecision(BaseModel):
    intent: RouteIntent
    route_source: str = "rule"
    reason: str
    confidence: float = 0.0
    matched_signals: List[str] = Field(default_factory=list)
    rag_score: float = 0.0
    should_respond_directly: bool = False
