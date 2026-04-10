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


class VerificationAgent:
    def __init__(self, skill_registry: SkillRegistry) -> None:
        self.skill_registry = skill_registry

    async def verify(self, hypothesis: Hypothesis, context_snapshot: ContextSnapshot) -> VerificationResult:
        evidence_items: list[EvidenceItem] = []
        checks_passed: list[str] = []
        checks_failed: list[str] = []
        flattened_evidence: list[str] = []

        for step in hypothesis.verification_plan:
            skill_result = self._run_skill(step.skill_name, step.params, context_snapshot, hypothesis)
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
            metadata={"verification_mode": "rule_based_skill_execution"},
        )

    def _run_skill(
        self,
        skill_name: str,
        params: dict[str, Any],
        context_snapshot: ContextSnapshot,
        hypothesis: Hypothesis,
    ) -> SkillResult:
        signature = self.skill_registry.get_signature(skill_name)
        skill_category = signature.category if signature is not None else ""
        haystack_parts = [
            str(context_snapshot.request.get("message") or ""),
            hypothesis.root_cause,
            hypothesis.expected_evidence,
        ]
        if context_snapshot.rag_context is not None:
            for item in list(context_snapshot.rag_context.context or context_snapshot.rag_context.hits)[:4]:
                haystack_parts.extend([str(item.title or ""), str(item.section or ""), str(item.snippet or "")])
        for item in context_snapshot.similar_cases[:3]:
            haystack_parts.extend([item.symptom, item.root_cause, item.summary, item.final_action])
        haystack = " ".join(part.lower() for part in haystack_parts if part).strip()

        strong_keywords = {
            "check_recent_deploys": ["发布", "deploy", "release", "pipeline", "回滚", "变更"],
            "check_pipeline_status": ["pipeline", "构建", "发布"],
            "check_ingress_rules": ["ingress", "502", "503", "504", "网关", "超时"],
            "check_network_latency": ["network", "延迟", "timeout", "超时", "链路"],
            "check_pod_health": ["pod", "重启", "crashloop", "oom", "探针"],
            "check_memory_trend": ["oom", "memory", "内存", "rss"],
            "check_db_health": ["db", "database", "mysql", "慢查询", "连接池"],
            "check_log_errors": ["error", "错误", "异常", "日志"],
            "check_alert_history": ["告警", "报警", "alert"],
        }
        matched = any(keyword.lower() in haystack for keyword in strong_keywords.get(skill_name, []))
        if not matched and skill_category in (context_snapshot.matched_skill_categories or []):
            matched = True

        service = str(params.get("service") or context_snapshot.request.get("service") or "service")
        if matched:
            return SkillResult(
                skill_name=skill_name,
                status="matched",
                summary=f"{skill_name} 发现与假设一致的证据",
                evidence=[
                    f"{service}: {skill_name} 与当前上下文/案例匹配",
                    f"expected={hypothesis.expected_evidence}",
                ],
                payload={"params": params, "matched": True},
            )

        return SkillResult(
            skill_name=skill_name,
            status="not_matched",
            summary=f"{skill_name} 暂未发现足够证据支持该假设",
            evidence=[f"{service}: {skill_name} 当前仅得到弱相关线索"],
            payload={"params": params, "matched": False},
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
