from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from .models import ExecutionStep


class ExecutionStepExecutionRequest(BaseModel):
    plan_id: str
    step: ExecutionStep
    session_context: Dict[str, Any] = Field(default_factory=dict)
    approval_context: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class ExecutionStepExecutionResult(BaseModel):
    status: str
    summary: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    compensation_required: bool = False


class ExecutionDriver(ABC):
    @abstractmethod
    async def execute_step(self, request: ExecutionStepExecutionRequest) -> ExecutionStepExecutionResult:
        raise NotImplementedError

    async def compensate_step(
        self,
        request: ExecutionStepExecutionRequest,
        *,
        reason: Optional[str] = None,
    ) -> ExecutionStepExecutionResult:
        return ExecutionStepExecutionResult(
            status="skipped",
            summary=reason or "当前执行驱动未实现自动补偿。",
            payload={},
            evidence=[],
            metadata={"compensation_mode": "not_implemented"},
            retryable=False,
            compensation_required=False,
        )
