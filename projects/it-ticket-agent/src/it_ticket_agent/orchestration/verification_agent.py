from __future__ import annotations

from typing import Any

from ..state.models import (
    ContextSnapshot,
    EvidenceItem,
    Hypothesis,
    SkillResult,
    VerificationResult,
)
from ..skills import SkillRegistry
from ..skills.local_executor import LocalSkillExecutor


class VerificationAgent:
    def __init__(
        self,
        skill_registry: SkillRegistry,
        *,
        skill_executor: LocalSkillExecutor | None = None,
    ) -> None:
        self.skill_registry = skill_registry
        self.skill_executor = skill_executor or LocalSkillExecutor()

    async def verify(self, hypothesis: Hypothesis, context_snapshot: ContextSnapshot) -> VerificationResult:
        evidence_items: list[EvidenceItem] = []
        checks_passed: list[str] = []
        checks_failed: list[str] = []
        flattened_evidence: list[str] = []

        for step in hypothesis.verification_plan:
            skill_result = await self._run_skill(step.skill_name, step.params, context_snapshot, hypothesis)
            matches_expected = skill_result.status in {"confirmed", "unhealthy", "matched"}
            evidence_items.append(
                EvidenceItem(
                    skill=step.skill_name,
                    purpose=step.purpose,
                    result=skill_result.model_dump(),
                    matches_expected=matches_expected,
                )
            )
            if matches_expected:
                checks_passed.append(step.skill_name)
            else:
                checks_failed.append(step.skill_name)
            flattened_evidence.extend(skill_result.evidence)

        total_steps = max(1, len(hypothesis.verification_plan))
        evidence_strength = len(checks_passed) / total_steps
        confidence = min(1.0, hypothesis.confidence_prior * 0.6 + evidence_strength * 0.4)
        if evidence_strength >= 0.7:
            status = "passed"
        elif evidence_strength >= 0.35:
            status = "inconclusive"
        else:
            status = "failed"

        return VerificationResult(
            hypothesis_id=hypothesis.hypothesis_id,
            root_cause=hypothesis.root_cause,
            confidence=confidence,
            evidence_strength=evidence_strength,
            evidence_items=evidence_items,
            recommended_action=hypothesis.recommended_action,
            action_risk=hypothesis.action_risk,
            action_params=dict(hypothesis.action_params),
            status=status,
            summary=self._build_summary(hypothesis, status, checks_passed, checks_failed),
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            evidence=flattened_evidence[:8],
            payload={
                "expected_evidence": hypothesis.expected_evidence,
                "step_count": len(hypothesis.verification_plan),
            },
            metadata={"verification_mode": "skill_executor"},
        )

    async def _run_skill(
        self,
        skill_name: str,
        params: dict[str, Any],
        context_snapshot: ContextSnapshot,
        hypothesis: Hypothesis,
    ) -> SkillResult:
        signature = self.skill_registry.get_signature(skill_name)
        if signature is None:
            return SkillResult(
                skill_name=skill_name,
                status="error",
                summary=f"未注册的 skill: {skill_name}",
                evidence=[],
                payload={"params": dict(params), "expected_evidence": hypothesis.expected_evidence},
            )
        try:
            return await self.skill_executor.execute_skill(
                skill_name,
                params=params,
                context_snapshot=context_snapshot,
            )
        except Exception as exc:
            return SkillResult(
                skill_name=skill_name,
                status="error",
                summary=f"{skill_name} 执行失败",
                evidence=[str(exc)],
                payload={"params": dict(params), "expected_evidence": hypothesis.expected_evidence},
            )

    @staticmethod
    def _build_summary(
        hypothesis: Hypothesis,
        status: str,
        checks_passed: list[str],
        checks_failed: list[str],
    ) -> str:
        if status == "passed":
            return f"{hypothesis.hypothesis_id} 已得到主要验证步骤支持。"
        if status == "inconclusive":
            return f"{hypothesis.hypothesis_id} 得到部分支持，但仍需更多证据。"
        return f"{hypothesis.hypothesis_id} 当前证据不足，关键失败步骤: {', '.join(checks_failed[:2]) or 'none'}。"
