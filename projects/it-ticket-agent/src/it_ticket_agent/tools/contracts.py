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
    input_schema: Dict[str, Any] = {"type": "object", "properties": {}}

    @abstractmethod
    async def run(self, task: TaskEnvelope, arguments: Dict[str, Any] | None = None) -> ToolExecutionResult:
        raise NotImplementedError

    def as_openai_tool(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.summary,
                "parameters": self.input_schema,
            },
        }
