from __future__ import annotations

from abc import ABC, abstractmethod

from ..runtime.contracts import AgentResult, TaskEnvelope


class BaseDomainAgent(ABC):
    name: str
    domain: str

    @abstractmethod
    async def run(self, task: TaskEnvelope) -> AgentResult:
        raise NotImplementedError
