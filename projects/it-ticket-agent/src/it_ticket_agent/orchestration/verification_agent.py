from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..case_retrieval import CaseRetriever, infer_failure_mode, infer_root_cause_taxonomy
from ..knowledge import KnowledgeService
from ..llm_client import OpenAICompatToolLLM
from ..settings import Settings
from ..skills import SkillRegistry
from ..skills.local_executor import LocalSkillExecutor
from ..state.models import (
    ContextSnapshot,
    EvidenceItem,
    Hypothesis,
    KnowledgeHit,
    RAGContextBundle,
    RetrievalSubquery,
    SkillResult,
    VerificationResult,
    VerificationStep,
)


@dataclass
class ReactPlan:
    next_steps: list[VerificationStep]
    retrieval_queries: list[RetrievalSubquery]


class VerificationAgent:
    def __init__(
        self,
        skill_registry: SkillRegistry,
        *,
        skill_executor: LocalSkillExecutor | None = None,
        settings: Settings | None = None,
        llm: OpenAICompatToolLLM | None = None,
        knowledge_service: KnowledgeService | None = None,
        case_retriever: CaseRetriever | None = None,
        max_rounds: int = 3,
    ) -> None:
        self.skill_registry = skill_registry
        self.settings = settings or Settings()
        self.skill_executor = skill_executor or LocalSkillExecutor(settings=self.settings, skill_registry=skill_registry)
        self.llm = llm or OpenAICompatToolLLM(self.settings)
        self.knowledge_service = knowledge_service
        self.case_retriever = case_retriever
        self.max_rounds = max(1, int(max_rounds))

    async def verify(self, hypothesis: Hypothesis, context_snapshot: ContextSnapshot) -> VerificationResult:
        working_snapshot = context_snapshot.model_copy(deep=True)
        supervisor_meta = dict(hypothesis.metadata.get("supervisor") or {})
        evidence_items: list[EvidenceItem] = []
        checks_passed: list[str] = []
        checks_failed: list[str] = []
        flattened_evidence: list[str] = []
        retrieval_queries: list[dict[str, Any]] = []

        executed_keys: set[str] = set()
        pending_steps = list(hypothesis.verification_plan)
        rounds = 0
        max_rounds = max(1, int(supervisor_meta.get("max_rounds") or self.max_rounds))
        while pending_steps and rounds < max_rounds:
            rounds += 1
            current_steps = pending_steps
            pending_steps = []
            for step in current_steps:
                step_key = self._step_key(step)
                if step_key in executed_keys:
                    continue
                executed_keys.add(step_key)
                skill_result = await self._run_skill(step.skill_name, step.params, working_snapshot, hypothesis)
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

            interim_strength = len(checks_passed) / max(1, len(executed_keys))
            if rounds >= max_rounds or interim_strength >= 0.7:
                break

            react_plan = await self._plan_next_actions(
                hypothesis=hypothesis,
                context_snapshot=working_snapshot,
                evidence_items=evidence_items,
                executed_skill_names=[item.skill for item in evidence_items],
                supervisor_meta=supervisor_meta,
            )
            if not react_plan.next_steps and not react_plan.retrieval_queries:
                break
            if react_plan.retrieval_queries:
                retrieval_queries.extend([item.model_dump() for item in react_plan.retrieval_queries])
                working_snapshot = await self._expand_context(working_snapshot, react_plan.retrieval_queries)
            for step in react_plan.next_steps:
                if self._step_key(step) not in executed_keys:
                    pending_steps.append(step)

        total_steps = max(1, len(executed_keys))
        evidence_strength = len(checks_passed) / total_steps
        confidence = min(1.0, hypothesis.confidence_prior * 0.55 + evidence_strength * 0.45)
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
            summary=self._build_summary(hypothesis, status, checks_passed, checks_failed, rounds),
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            evidence=flattened_evidence[:8],
            payload={
                "expected_evidence": hypothesis.expected_evidence,
                "step_count": len(executed_keys),
                "react_rounds": rounds,
            },
            metadata={
                "verification_mode": "subagent_react",
                "subagent_name": str(supervisor_meta.get("subagent_type") or f"verifier::{hypothesis.hypothesis_id}"),
                "react_rounds": rounds,
                "max_rounds": max_rounds,
                "supervisor": supervisor_meta,
                "retrieval_queries": retrieval_queries,
            },
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

    async def _plan_next_actions(
        self,
        *,
        hypothesis: Hypothesis,
        context_snapshot: ContextSnapshot,
        evidence_items: list[EvidenceItem],
        executed_skill_names: list[str],
        supervisor_meta: dict[str, Any],
    ) -> ReactPlan:
        if self.llm.enabled:
            planned = await self._plan_next_actions_with_llm(
                hypothesis=hypothesis,
                context_snapshot=context_snapshot,
                evidence_items=evidence_items,
                executed_skill_names=executed_skill_names,
                supervisor_meta=supervisor_meta,
            )
            if planned.next_steps or planned.retrieval_queries:
                return planned
        return self._plan_next_actions_with_rules(
            hypothesis=hypothesis,
            context_snapshot=context_snapshot,
            executed_skill_names=executed_skill_names,
            supervisor_meta=supervisor_meta,
        )

    async def _plan_next_actions_with_llm(
        self,
        *,
        hypothesis: Hypothesis,
        context_snapshot: ContextSnapshot,
        evidence_items: list[EvidenceItem],
        executed_skill_names: list[str],
        supervisor_meta: dict[str, Any],
    ) -> ReactPlan:
        available_skills = [
            item.model_dump() for item in context_snapshot.available_skills if item.name not in set(executed_skill_names)
        ]
        allowed_focus_skills = set(supervisor_meta.get("focus_skills") or [])
        if allowed_focus_skills:
            available_skills = [item for item in available_skills if item.get("name") in allowed_focus_skills]
        if not available_skills:
            return ReactPlan([], [])
        messages = [
            {
                "role": "system",
                "content": (
                    "你是单个根因假设的验证 subagent。"
                    "请基于当前已执行证据，决定是否需要补检索或追加 1-2 个 skill。"
                    "输出 JSON："
                    "{\"retrieval_queries\": [...], \"next_steps\": [...]}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "hypothesis": hypothesis.model_dump(),
                        "request": dict(context_snapshot.request or {}),
                        "rag_context": context_snapshot.rag_context.model_dump() if context_snapshot.rag_context is not None else {},
                        "similar_cases": [item.model_dump() for item in context_snapshot.similar_cases[:3]],
                        "executed_skills": executed_skill_names,
                        "evidence_items": [item.model_dump() for item in evidence_items[-4:]],
                        "available_skills": available_skills,
                        "supervisor_meta": supervisor_meta,
                        "rules": {"max_next_steps": 2, "max_queries": 2},
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            response = await self.llm.chat(messages)
            payload = self.llm.extract_json(str(response.get("content") or ""))
        except Exception:
            return ReactPlan([], [])
        next_steps: list[VerificationStep] = []
        for item in list(payload.get("next_steps") or [])[:2]:
            try:
                candidate = VerificationStep.model_validate(item)
            except Exception:
                continue
            if self.skill_registry.get_signature(candidate.skill_name) is None:
                continue
            next_steps.append(candidate)
        retrieval_queries: list[RetrievalSubquery] = []
        for item in list(payload.get("retrieval_queries") or [])[:2]:
            try:
                retrieval_queries.append(RetrievalSubquery.model_validate(item))
            except Exception:
                continue
        return ReactPlan(next_steps=next_steps, retrieval_queries=retrieval_queries)

    def _plan_next_actions_with_rules(
        self,
        *,
        hypothesis: Hypothesis,
        context_snapshot: ContextSnapshot,
        executed_skill_names: list[str],
        supervisor_meta: dict[str, Any],
    ) -> ReactPlan:
        root = hypothesis.root_cause.lower()
        request = dict(context_snapshot.request or {})
        service = str(request.get("service") or "")
        namespace = str(request.get("namespace") or "default")
        steps: list[VerificationStep] = []
        queries: list[RetrievalSubquery] = []
        executed = set(executed_skill_names)
        focus_skills = set(supervisor_meta.get("focus_skills") or [])
        retrieval_focus = set(supervisor_meta.get("retrieval_focus") or [])

        if any(token in root for token in ["pod", "oom", "资源", "probe"]) and "check_resource_limits" not in executed:
            steps.append(
                VerificationStep(
                    skill_name="check_resource_limits",
                    params={"service": service},
                    purpose="补充资源配额与限制信号，确认是否属于资源耗尽。",
                )
            )
            queries.append(
                RetrievalSubquery(
                    query=f"{service} OOMKilled heap pressure quota limits",
                    target="both",
                    reason="补充资源耗尽类证据",
                    failure_mode="oom",
                    root_cause_taxonomy="resource_exhaustion",
                )
            )
        elif any(token in root for token in ["网络", "ingress", "timeout", "链路"]) and "check_dns_resolution" not in executed:
            steps.append(
                VerificationStep(
                    skill_name="check_dns_resolution",
                    params={"service": service},
                    purpose="补充 DNS/域名解析信号，确认是否存在入口或域名异常。",
                )
            )
            queries.append(
                RetrievalSubquery(
                    query=f"{service} ingress timeout upstream dependency jitter",
                    target="both",
                    reason="补充网络链路与上游依赖证据",
                    failure_mode="dependency_timeout",
                    root_cause_taxonomy="network_path_instability",
                )
            )
        elif any(token in root for token in ["数据库", "连接池", "慢查询"]) and "check_replication_lag" not in executed:
            steps.append(
                VerificationStep(
                    skill_name="check_replication_lag",
                    params={"service": service},
                    purpose="补充数据库复制延迟信号，确认是否存在数据库退化。",
                )
            )
            queries.append(
                RetrievalSubquery(
                    query=f"{service} db pool saturation replication lag slow query",
                    target="both",
                    reason="补充数据库侧证据",
                    failure_mode="db_pool_saturation",
                    root_cause_taxonomy="database_degradation",
                )
            )
        elif "check_log_errors" not in executed:
            steps.append(
                VerificationStep(
                    skill_name="check_log_errors",
                    params={"service": service, "namespace": namespace, "window": "30m"},
                    purpose="在证据不足时补查日志与告警。",
                )
            )
        if focus_skills:
            steps = [step for step in steps if step.skill_name in focus_skills]
        if retrieval_focus:
            filtered_queries = []
            for item in queries:
                if item.failure_mode and item.failure_mode in retrieval_focus:
                    filtered_queries.append(item)
                    continue
                if item.root_cause_taxonomy and item.root_cause_taxonomy in retrieval_focus:
                    filtered_queries.append(item)
                    continue
            if filtered_queries:
                queries = filtered_queries
        return ReactPlan(next_steps=steps[:2], retrieval_queries=queries[:2])

    async def _expand_context(
        self,
        context_snapshot: ContextSnapshot,
        retrieval_queries: list[RetrievalSubquery],
    ) -> ContextSnapshot:
        snapshot = context_snapshot.model_copy(deep=True)
        request = dict(snapshot.request or {})
        service = str(request.get("service") or "")
        cluster = str(request.get("cluster") or "")
        namespace = str(request.get("namespace") or "default")
        session_id = str(request.get("ticket_id") or "")
        for item in retrieval_queries:
            if item.target in {"knowledge", "both"} and self.knowledge_service is not None:
                bundle = await self.knowledge_service.retrieve_query(query=item.query, service=service, top_k=2)
                snapshot.rag_context = self._merge_rag(snapshot.rag_context, bundle)
            if item.target in {"cases", "both"} and self.case_retriever is not None:
                extra_cases = await self.case_retriever.recall(
                    service=service,
                    cluster=cluster,
                    namespace=namespace,
                    message=item.query,
                    session_id=session_id,
                    limit=3,
                    failure_mode=item.failure_mode,
                    root_cause_taxonomy=item.root_cause_taxonomy,
                )
                merged_cases = {case.case_id: case for case in snapshot.similar_cases}
                for case in extra_cases:
                    existing = merged_cases.get(case.case_id)
                    if existing is None or case.recall_score > existing.recall_score:
                        merged_cases[case.case_id] = case
                snapshot.similar_cases = list(merged_cases.values())
        return snapshot

    @staticmethod
    def _merge_rag(
        base: RAGContextBundle | None,
        extra: RAGContextBundle,
    ) -> RAGContextBundle:
        merged = base.model_copy(deep=True) if isinstance(base, RAGContextBundle) else RAGContextBundle()
        seen = {(item.chunk_id, item.path, item.section) for item in list(merged.context or merged.hits)}
        for item in list(extra.context or extra.hits):
            key = (item.chunk_id, item.path, item.section)
            if key in seen:
                continue
            seen.add(key)
            merged.hits.append(KnowledgeHit.model_validate(item))
            merged.context.append(KnowledgeHit.model_validate(item))
        merged.citations = list(dict.fromkeys([*merged.citations, *extra.citations]))
        merged.facts = list(merged.facts) + [fact for fact in extra.facts if fact not in merged.facts]
        merged.index_info = {
            **dict(merged.index_info or {}),
            "subagent_expansion": True,
        }
        return merged

    @staticmethod
    def _step_key(step: VerificationStep) -> str:
        return json.dumps(
            {"skill_name": step.skill_name, "params": dict(step.params), "purpose": step.purpose},
            ensure_ascii=False,
            sort_keys=True,
        )

    @staticmethod
    def _build_summary(
        hypothesis: Hypothesis,
        status: str,
        checks_passed: list[str],
        checks_failed: list[str],
        rounds: int,
    ) -> str:
        if status == "passed":
            return f"{hypothesis.hypothesis_id} 已得到主要验证步骤支持，subagent 共执行 {rounds} 轮。"
        if status == "inconclusive":
            return f"{hypothesis.hypothesis_id} 得到部分支持，subagent 共执行 {rounds} 轮，仍需更多证据。"
        return f"{hypothesis.hypothesis_id} 当前证据不足，subagent 共执行 {rounds} 轮，关键失败步骤: {', '.join(checks_failed[:2]) or 'none'}。"
