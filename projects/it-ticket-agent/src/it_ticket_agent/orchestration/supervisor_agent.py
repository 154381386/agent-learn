from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..llm_client import OpenAICompatToolLLM
from ..settings import Settings
from ..state.models import ContextSnapshot, Hypothesis, RankedResult, SimilarIncidentCase, VerificationResult
from .hypothesis_generator import HypothesisGenerator
from .ranker import Ranker


@dataclass
class SupervisorPlan:
    hypotheses: list[Hypothesis]
    metadata: dict[str, Any]


@dataclass
class SupervisorSelection:
    ranked_result: RankedResult
    metadata: dict[str, Any]


class SupervisorAgent:
    def __init__(
        self,
        *,
        hypothesis_generator: HypothesisGenerator,
        ranker: Ranker,
        settings: Settings | None = None,
        llm: OpenAICompatToolLLM | None = None,
    ) -> None:
        self.hypothesis_generator = hypothesis_generator
        self.ranker = ranker
        self.settings = settings or Settings()
        self.llm = llm or OpenAICompatToolLLM(self.settings)

    async def plan_verification(
        self,
        context_snapshot: ContextSnapshot,
    ) -> SupervisorPlan:
        hypotheses = await self.hypothesis_generator.generate(context_snapshot)
        if self.llm.enabled and hypotheses:
            planned = await self._enrich_with_llm(context_snapshot, hypotheses)
            if planned.hypotheses:
                return planned
        return self._enrich_with_rules(context_snapshot, hypotheses)

    def select_primary_outcome(
        self,
        verification_results: list[VerificationResult],
        *,
        similar_cases: list[SimilarIncidentCase] | None = None,
        feedback_cases: list[dict] | None = None,
    ) -> SupervisorSelection:
        ranked = self.ranker.rank(
            verification_results,
            similar_cases=similar_cases,
            feedback_cases=feedback_cases,
        )
        metadata = {
            "selected_by": "supervisor_agent",
            "selection_strategy": "ranker_score_then_confidence",
            "candidate_count": len(verification_results),
        }
        if ranked.primary is not None:
            ranked.ranking_metadata["selected_by"] = "supervisor_agent"
            ranked.ranking_metadata["selection_strategy"] = metadata["selection_strategy"]
        return SupervisorSelection(ranked_result=ranked, metadata=metadata)

    async def _enrich_with_llm(
        self,
        context_snapshot: ContextSnapshot,
        hypotheses: list[Hypothesis],
    ) -> SupervisorPlan:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是验证编排 Supervisor。"
                    "请为每个根因假设补充 subagent 策略，包括 priority、max_rounds、focus_skills、retrieval_focus。"
                    "输出 JSON："
                    "{\"plan_summary\": ..., \"global_strategy\": ..., \"hypotheses\": [{\"hypothesis_id\": ..., \"metadata\": {...}}]}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "request": dict(context_snapshot.request or {}),
                        "rag_context": context_snapshot.rag_context.model_dump() if context_snapshot.rag_context is not None else {},
                        "similar_cases": [case.model_dump() for case in context_snapshot.similar_cases[:3]],
                        "available_skills": [item.model_dump() for item in context_snapshot.available_skills],
                        "hypotheses": [item.model_dump() for item in hypotheses],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            response = await self.llm.chat(messages)
            payload = self.llm.extract_json(str(response.get("content") or ""))
        except Exception:
            return SupervisorPlan(hypotheses=[], metadata={})

        metadata_by_id = {
            str(item.get("hypothesis_id") or ""): dict(item.get("metadata") or {})
            for item in list(payload.get("hypotheses") or [])
            if isinstance(item, dict)
        }
        enriched: list[Hypothesis] = []
        for hypothesis in hypotheses:
            extra = metadata_by_id.get(hypothesis.hypothesis_id, {})
            enriched.append(
                hypothesis.model_copy(
                    update={
                        "metadata": {
                            **dict(hypothesis.metadata or {}),
                            "supervisor": extra,
                        }
                    }
                )
            )
        return SupervisorPlan(
            hypotheses=enriched,
            metadata={
                "planner": "supervisor_agent_llm",
                "plan_summary": str(payload.get("plan_summary") or ""),
                "global_strategy": str(payload.get("global_strategy") or ""),
            },
        )

    def _enrich_with_rules(
        self,
        context_snapshot: ContextSnapshot,
        hypotheses: list[Hypothesis],
    ) -> SupervisorPlan:
        available_skill_names = {item.name for item in context_snapshot.available_skills}
        enriched: list[Hypothesis] = []
        for hypothesis in hypotheses:
            root = hypothesis.root_cause.lower()
            supervisor_meta: dict[str, Any] = {
                "priority": "medium",
                "max_rounds": 2,
                "focus_skills": [],
                "retrieval_focus": [],
                "subagent_type": "general_verifier",
            }
            if any(token in root for token in ["pod", "oom", "资源", "probe"]):
                supervisor_meta.update(
                    {
                        "priority": "high",
                        "max_rounds": 3,
                        "focus_skills": [
                            name for name in ["diagnose_pod_crash", "check_pod_health", "check_memory_trend", "check_resource_limits"]
                            if name in available_skill_names
                        ],
                        "retrieval_focus": ["k8s", "resource_exhaustion"],
                        "subagent_type": "k8s_react_verifier",
                    }
                )
            elif any(token in root for token in ["网络", "ingress", "timeout", "链路"]):
                supervisor_meta.update(
                    {
                        "priority": "high",
                        "max_rounds": 3,
                        "focus_skills": [
                            name for name in ["check_network_latency", "check_ingress_rules", "check_dns_resolution"]
                            if name in available_skill_names
                        ],
                        "retrieval_focus": ["network_path_instability", "dependency_timeout"],
                        "subagent_type": "network_react_verifier",
                    }
                )
            elif any(token in root for token in ["数据库", "连接池", "慢查询"]):
                supervisor_meta.update(
                    {
                        "priority": "high",
                        "max_rounds": 3,
                        "focus_skills": [
                            name for name in ["check_db_health", "check_replication_lag"]
                            if name in available_skill_names
                        ],
                        "retrieval_focus": ["database_degradation", "db_pool_saturation"],
                        "subagent_type": "database_react_verifier",
                    }
                )
            elif any(token in root for token in ["发布", "流水线", "回归"]):
                supervisor_meta.update(
                    {
                        "priority": "high",
                        "max_rounds": 2,
                        "focus_skills": [
                            name for name in ["check_recent_deploys", "check_pipeline_status"]
                            if name in available_skill_names
                        ],
                        "retrieval_focus": ["deploy_regression", "release_regression"],
                        "subagent_type": "cicd_react_verifier",
                    }
                )
            elif any(token in root for token in ["日志", "告警"]):
                supervisor_meta.update(
                    {
                        "priority": "medium",
                        "max_rounds": 2,
                        "focus_skills": [
                            name for name in ["check_log_errors", "check_alert_history"]
                            if name in available_skill_names
                        ],
                        "retrieval_focus": ["runtime_signal_amplification"],
                        "subagent_type": "observability_react_verifier",
                    }
                )
            enriched.append(
                hypothesis.model_copy(
                    update={
                        "metadata": {
                            **dict(hypothesis.metadata or {}),
                            "supervisor": supervisor_meta,
                        }
                    }
                )
            )
        return SupervisorPlan(
            hypotheses=enriched,
            metadata={
                "planner": "supervisor_agent_rules",
                "plan_summary": "Supervisor 已为每个假设分配验证优先级、轮数和 focus skills。",
                "global_strategy": "parallel_subagents_with_controlled_react",
            },
        )
