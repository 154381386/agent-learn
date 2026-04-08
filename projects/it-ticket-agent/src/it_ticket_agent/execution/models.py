from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from ..session.models import utc_now

ExecutionPlanStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
ExecutionStepStatus = Literal["pending", "running", "completed", "failed", "skipped"]
RetryStrategy = Literal["fixed", "linear", "exponential", "manual"]
CompensationMode = Literal["none", "manual", "automatic"]
RecoveryAction = Literal["none", "execute_primary_step", "retry_execution_step", "finalize_execution", "manual_intervention"]


class ExecutionRetryPolicy(BaseModel):
    max_attempts: int = 1
    backoff_seconds: int = 0
    strategy: RetryStrategy = "fixed"
    retryable_errors: List[str] = Field(default_factory=list)
    operator_hint: str = ""


class ExecutionCompensationPolicy(BaseModel):
    mode: CompensationMode = "none"
    action: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    operator_hint: str = ""


class ExecutionRecoveryMetadata(BaseModel):
    can_resume: bool = False
    recovery_action: RecoveryAction = "none"
    recovery_reason: str = ""
    resume_from_step_id: Optional[str] = None
    failed_step_id: Optional[str] = None
    last_completed_step_id: Optional[str] = None
    suggested_retry_count: int = 0
    hints: List[str] = Field(default_factory=list)


class ExecutionPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    thread_id: str
    ticket_id: str
    status: ExecutionPlanStatus = "pending"
    steps: List[str] = Field(default_factory=list)
    current_step_id: Optional[str] = None
    summary: str = ""
    recovery: ExecutionRecoveryMetadata = Field(default_factory=ExecutionRecoveryMetadata)
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
    sequence: int = 0
    dependencies: List[str] = Field(default_factory=list)
    retry_policy: ExecutionRetryPolicy = Field(default_factory=ExecutionRetryPolicy)
    compensation: Optional[ExecutionCompensationPolicy] = None
    attempt: int = 0
    last_error: Dict[str, Any] = Field(default_factory=dict)
    status: ExecutionStepStatus = "pending"
    result_summary: str = ""
    evidence: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
