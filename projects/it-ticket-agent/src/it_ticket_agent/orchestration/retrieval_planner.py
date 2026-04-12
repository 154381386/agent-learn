from __future__ import annotations

import json
from typing import Any

from ..llm_client import OpenAICompatToolLLM
from ..settings import Settings
from ..state.models import RetrievalExpansion, RetrievalSubquery, SimilarIncidentCase


class RetrievalPlanner:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: OpenAICompatToolLLM | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm or OpenAICompatToolLLM(settings)

    async def plan(
        self,
        *,
        request: dict[str, Any],
        rag_context: dict[str, Any],
        similar_cases: list[SimilarIncidentCase],
        matched_skill_categories: list[str],
    ) -> RetrievalExpansion:
        if self.llm.enabled:
            planned = await self._plan_with_llm(
                request=request,
                rag_context=rag_context,
                similar_cases=similar_cases,
                matched_skill_categories=matched_skill_categories,
            )
            if planned.subqueries:
                return planned
        return self._plan_with_rules(
            request=request,
            rag_context=rag_context,
            similar_cases=similar_cases,
            matched_skill_categories=matched_skill_categories,
        )

    async def _plan_with_llm(
        self,
        *,
        request: dict[str, Any],
        rag_context: dict[str, Any],
        similar_cases: list[SimilarIncidentCase],
        matched_skill_categories: list[str],
    ) -> RetrievalExpansion:
        payload = {
            "request": request,
            "rag_context": rag_context,
            "similar_cases": [case.model_dump() for case in similar_cases[:3]],
            "matched_skill_categories": matched_skill_categories,
            "rules": {
                "max_subqueries": 3,
                "targets": ["knowledge", "cases", "both"],
                "goal": "identify missing evidence and rewrite focused retrieval queries",
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是检索规划器。"
                    "请基于当前问题、初始知识命中和相似案例，识别证据缺口，并输出 0-3 个后续检索子查询。"
                    "输出 JSON，格式必须是 "
                    "{\"missing_evidence\": [...], \"subqueries\": [{\"query\": ..., \"target\": ..., \"reason\": ..., "
                    "\"failure_mode\": ..., \"root_cause_taxonomy\": ...}]}"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            response = await self.llm.chat(messages)
            parsed = self.llm.extract_json(str(response.get("content") or ""))
        except Exception:
            return RetrievalExpansion()
        subqueries_raw = parsed.get("subqueries") if isinstance(parsed, dict) else []
        items: list[RetrievalSubquery] = []
        for item in subqueries_raw or []:
            try:
                candidate = RetrievalSubquery.model_validate(item)
            except Exception:
                continue
            if candidate.query.strip():
                items.append(candidate)
        return RetrievalExpansion(
            subqueries=items[:3],
            missing_evidence=[str(item) for item in list(parsed.get("missing_evidence") or [])[:4]],
        )

    def _plan_with_rules(
        self,
        *,
        request: dict[str, Any],
        rag_context: dict[str, Any],
        similar_cases: list[SimilarIncidentCase],
        matched_skill_categories: list[str],
    ) -> RetrievalExpansion:
        message = str(request.get("message") or "")
        service = str(request.get("service") or "")
        subqueries: list[RetrievalSubquery] = []
        missing_evidence: list[str] = []
        lowered = message.lower()
        rag_titles = " ".join(
            str(item.get("title") or "") + " " + str(item.get("snippet") or "")
            for item in list(rag_context.get("hits") or [])[:3]
            if isinstance(item, dict)
        ).lower()
        case_haystack = " ".join(f"{case.failure_mode} {case.root_cause_taxonomy} {case.summary}".lower() for case in similar_cases)

        if any(token in lowered for token in ["timeout", "超时"]):
            if "network_path_instability" not in case_haystack:
                missing_evidence.append("是否存在上游依赖或网络链路抖动")
                subqueries.append(
                    RetrievalSubquery(
                        query=f"{service} upstream dependency timeout ingress gateway jitter".strip(),
                        target="both",
                        reason="确认超时是否来自入口链路或上游依赖",
                        failure_mode="dependency_timeout",
                        root_cause_taxonomy="network_path_instability",
                    )
                )
            if "database" not in rag_titles and "database_degradation" not in case_haystack and "db" in " ".join(matched_skill_categories + [lowered]):
                missing_evidence.append("是否存在数据库连接池或慢查询放大")
                subqueries.append(
                    RetrievalSubquery(
                        query=f"{service} db pool saturation slow query timeout".strip(),
                        target="both",
                        reason="确认超时是否由数据库退化导致",
                        failure_mode="db_pool_saturation",
                        root_cause_taxonomy="database_degradation",
                    )
                )

        if any(token in lowered for token in ["oom", "oomkilled", "内存", "heap"]):
            missing_evidence.append("是否存在内存压力与 Pod 重启证据")
            subqueries.append(
                RetrievalSubquery(
                    query=f"{service} OOMKilled heap pressure pod restart".strip(),
                    target="both",
                    reason="确认是否存在资源耗尽或 JVM 堆内存异常",
                    failure_mode="oom",
                    root_cause_taxonomy="resource_exhaustion",
                )
            )

        if any(token in lowered for token in ["deploy", "发布", "回滚", "pipeline"]):
            missing_evidence.append("是否存在近期发布回归或配置变更")
            subqueries.append(
                RetrievalSubquery(
                    query=f"{service} release regression deploy rollback 5xx".strip(),
                    target="both",
                    reason="确认是否存在发布窗口内的回归问题",
                    failure_mode="deploy_regression",
                    root_cause_taxonomy="release_regression",
                )
            )

        deduped: list[RetrievalSubquery] = []
        seen = set()
        for item in subqueries:
            key = (item.query, item.target, item.failure_mode, item.root_cause_taxonomy)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return RetrievalExpansion(subqueries=deduped[:3], missing_evidence=missing_evidence[:4])
