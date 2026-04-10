from __future__ import annotations

import asyncio

from ..state.models import ContextSnapshot, Hypothesis, VerificationResult
from .verification_agent import VerificationAgent


class ParallelVerifier:
    def __init__(self, verification_agent: VerificationAgent, *, max_concurrency: int = 3) -> None:
        self.verification_agent = verification_agent
        self.max_concurrency = max(1, int(max_concurrency))

    async def verify_all(
        self,
        *,
        hypotheses: list[Hypothesis],
        context_snapshot: ContextSnapshot,
    ) -> list[VerificationResult]:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _run(hypothesis: Hypothesis) -> VerificationResult:
            async with semaphore:
                return await self.verification_agent.verify(hypothesis, context_snapshot)

        return await asyncio.gather(*[_run(item) for item in hypotheses])
