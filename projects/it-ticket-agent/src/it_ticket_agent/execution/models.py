from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from ..session.models import utc_now

ExecutionPlanStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
ExecutionStepStatus = Literal["pending", "running", "completed", "failed", "skipped"]


class ExecutionPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    thread_id: str
    ticket_id: str
    status: ExecutionPlanStatus = "pending"
    steps: List[str] = Field(default_factory=list)
    summary: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class ExecutionStep(BaseModel):
    step_id: str = Field(default_factory=lambda: str(uuid4()))
    plan_id: str
    session_id: str
    action: str
    tool_name: str
    params: Dict[str, Any] = Field(default_factory=dict)
    status: ExecutionStepStatus = "pending"
    result_summary: str = ""
    evidence: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
