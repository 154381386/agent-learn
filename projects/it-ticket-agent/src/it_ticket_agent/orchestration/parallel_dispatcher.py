from __future__ import annotations

import asyncio
from typing import Mapping, Sequence

from pydantic import BaseModel, Field

from ..agents.base import BaseDomainAgent
from ..runtime.contracts import AgentResult, TaskEnvelope


class DispatchFailure(BaseModel):
    agent_name: str
    error_type: str
    message: str
    timed_out: bool = False


class DispatchBatchResult(BaseModel):
    results: list[AgentResult] = Field(default_factory=list)
    failures: list[DispatchFailure] = Field(default_factory=list)


class ParallelDispatcher:
    def __init__(self, *, max_concurrency: int = 3, timeout_sec: float = 20.0) -> None:
        self.max_concurrency = max(1, int(max_concurrency))
        self.timeout_sec = max(0.1, float(timeout_sec))

    async def dispatch(
        self,
        *,
        task: TaskEnvelope,
        candidate_agents: Sequence[str],
        agents: Mapping[str, BaseDomainAgent],
    ) -> DispatchBatchResult:
        ordered_candidates: list[str] = []
        seen: set[str] = set()
        for agent_name in candidate_agents:
            normalized = str(agent_name or "").strip()
            if not normalized or normalized in seen:
                continue
            ordered_candidates.append(normalized)
            seen.add(normalized)

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _run(agent_name: str) -> tuple[str, AgentResult | None, DispatchFailure | None]:
            agent = agents.get(agent_name)
            if agent is None:
                return (
                    agent_name,
                    None,
                    DispatchFailure(
                        agent_name=agent_name,
                        error_type="AgentNotConfigured",
                        message=f"agent not configured: {agent_name}",
                    ),
                )

            sub_task = task.model_copy(update={"task_id": f"{task.task_id}:{agent_name}", "mode": "fan_out"})
            try:
                async with semaphore:
                    result = await asyncio.wait_for(agent.run(sub_task), timeout=self.timeout_sec)
                return agent_name, result, None
            except asyncio.TimeoutError:
                return (
                    agent_name,
                    None,
                    DispatchFailure(
                        agent_name=agent_name,
                        error_type="TimeoutError",
                        message=f"agent execution timed out after {self.timeout_sec:.1f}s",
                        timed_out=True,
                    ),
                )
            except Exception as exc:  # pragma: no cover - exercised through callers
                return (
                    agent_name,
                    None,
                    DispatchFailure(
                        agent_name=agent_name,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    ),
                )

        dispatched = await asyncio.gather(*[_run(agent_name) for agent_name in ordered_candidates])
        results: list[AgentResult] = []
        failures: list[DispatchFailure] = []
        for _agent_name, result, failure in dispatched:
            if result is not None:
                results.append(result)
            if failure is not None:
                failures.append(failure)
        return DispatchBatchResult(results=results, failures=failures)
