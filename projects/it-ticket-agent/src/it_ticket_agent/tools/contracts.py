from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from ..runtime.contracts import TaskEnvelope


class ToolExecutionResult(BaseModel):
    tool_name: str
    status: str
    summary: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[str] = Field(default_factory=list)
    risk: str = "low"


class BaseTool(ABC):
    name: str
    summary: str

    @abstractmethod
    async def run(self, task: TaskEnvelope) -> ToolExecutionResult:
        raise NotImplementedError
