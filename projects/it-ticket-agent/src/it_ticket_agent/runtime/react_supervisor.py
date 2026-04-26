from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4
from typing import Any, Callable, Dict

from ..execution.tool_middleware import ToolExecutionMiddleware
from ..graph.nodes import OrchestratorGraphNodes
from ..orchestration.ranker import Ranker
from ..graph.react_state import ReactTicketGraphState
from ..llm_client import OpenAICompatToolLLM
from ..memory.working_memory import (
    compact_working_memory,
    compact_working_memory_with_llm,
    working_memory_compaction_trigger,
)
from ..runtime.contracts import TaskEnvelope
from ..settings import Settings
from ..state.models import EvidenceItem, Hypothesis, RAGContextBundle, SimilarIncidentCase, VerificationResult, VerificationStep
from ..tools.runtime import LocalToolRuntime


logger = logging.getLogger(__name__)


DOMAIN_TOOL_GROUPS: dict[str, list[str]] = {
    "cicd": [
        "check_recent_deployments",
        "check_pipeline_status",
        "check_canary_status",
        "get_change_records",
        "get_deployment_status",
        "get_rollback_history",
        "inspect_build_failure_logs",
        "check_service_health",
    ],
    "k8s": [
        "check_pod_status",
        "inspect_pod_logs",
        "inspect_pod_events",
        "inspect_jvm_memory",
        "inspect_cpu_saturation",
        "inspect_thread_pool_status",
        "check_service_health",
        "check_recent_alerts",
    ],
    "network": [
        "inspect_ingress_route",
        "inspect_vpc_connectivity",
        "inspect_upstream_dependency",
        "inspect_dns_resolution",
        "inspect_load_balancer_status",
        "inspect_egress_policy",
        "check_service_health",
        "check_recent_alerts",
    ],
    "db": [
        "inspect_connection_pool",
        "inspect_slow_queries",
        "inspect_db_instance_health",
        "inspect_deadlock_signals",
        "inspect_transaction_rollback_rate",
        "inspect_replication_status",
        "check_service_health",
    ],
    "sde": [
        "get_quota_status",
    ],
}

DOMAIN_EXPANSION_PRIORITIES: dict[str, list[str]] = {
    "cicd": [
        "check_recent_deployments",
        "check_pipeline_status",
        "check_canary_status",
        "get_change_records",
    ],
    "k8s": [
        "check_pod_status",
        "inspect_pod_logs",
        "inspect_pod_events",
    ],
    "network": [
        "inspect_upstream_dependency",
        "inspect_vpc_connectivity",
        "inspect_ingress_route",
    ],
    "db": [
        "inspect_connection_pool",
        "inspect_slow_queries",
        "inspect_db_instance_health",
    ],
    "sde": [
        "get_quota_status",
    ],
}

DOMAIN_ADJACENCY: dict[str, list[str]] = {
    "cicd": ["k8s", "network", "db"],
    "k8s": ["cicd", "network", "db"],
    "network": ["db", "cicd", "k8s"],
    "db": ["network", "cicd", "k8s"],
    "sde": ["k8s", "cicd"],
}


class ReactSupervisor:
    def __init__(
        self,
        legacy_nodes: OrchestratorGraphNodes,
        *,
        settings: Settings,
        max_iterations: int = 4,
        max_tool_calls: int = 8,
        confidence_threshold: float = 0.65,
        max_parallel_branches: int = 4,
        summary_after_n_steps: int = 3,
        max_context_tokens: int = 6000,
        activity_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.legacy_nodes = legacy_nodes
        self.settings = settings
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.confidence_threshold = confidence_threshold
        self.max_parallel_branches = max_parallel_branches
        self.summary_after_n_steps = summary_after_n_steps
        self.max_context_tokens = max_context_tokens
        self.llm = OpenAICompatToolLLM(settings)
        self.tool_runtime = LocalToolRuntime(settings=settings)
        self.tools = self.tool_runtime.tools
        self.tool_middleware = ToolExecutionMiddleware(self.tools)
        self.ranker = Ranker()
        self.activity_callback = activity_callback

    async def run_loop(self, state: ReactTicketGraphState) -> Dict[str, Any]:
        next_state: dict[str, Any] = dict(state)
        context_updates = await self.legacy_nodes.context_collector(next_state)
        next_state.update(context_updates)
        next_state.setdefault("transition_notes", []).append("react supervisor completed context collection")

        if not self.llm.enabled:
            next_state.setdefault("transition_notes", []).append("llm disabled, using rule-based react fallback")
            return await self._run_rule_based_loop(next_state)

        await self._maybe_compact_execution_working_memory(next_state)

        incident_state = next_state["incident_state"]
        context_snapshot = next_state.get("context_snapshot") or incident_state.context_snapshot
        request = next_state["request"]
        activity_context = self._activity_context_from_state(next_state, request)
        observations: list[dict[str, Any]] = list(next_state.get("observation_ledger") or [])
        tool_calls_used = int(next_state.get("tool_calls_used") or 0)
        tool_cache: dict[str, Any] = dict(next_state.get("tool_cache") or {})
        working_memory_summary = str(next_state.get("working_memory_summary") or "")
        pinned_findings: list[str] = list(next_state.get("pinned_findings") or [])
        expanded_domains_seen: list[str] = list(next_state.get("expanded_domains_seen") or [])
        expansion_probe_count = int(next_state.get("expansion_probe_count") or 0)
        expansion_probe_tools: list[str] = list(next_state.get("expansion_probe_tools") or [])
        rejected_tool_call_names: list[str] = list(next_state.get("rejected_tool_call_names") or [])
        rejected_tool_call_count = int(next_state.get("rejected_tool_call_count") or 0)
        context_snapshot = next_state.get("context_snapshot") or incident_state.context_snapshot
        candidate_domain_plan = self._resolve_candidate_domains(
            request=request,
            context_snapshot=context_snapshot,
            observations=observations,
        )
        candidate_tool_names = self._select_candidate_tool_names(
            observations=observations,
            candidate_domain_plan=candidate_domain_plan,
            context_snapshot=context_snapshot,
        )
        next_state["candidate_tool_names"] = candidate_tool_names
        next_state["candidate_domains"] = dict(candidate_domain_plan)

        for iteration in range(1, self.max_iterations + 1):
            next_state["iterations"] = iteration
            context_snapshot = next_state.get("context_snapshot") or incident_state.context_snapshot
            candidate_domain_plan = self._resolve_candidate_domains(
                request=request,
                context_snapshot=context_snapshot,
                observations=observations,
            )
            candidate_tool_names = self._select_candidate_tool_names(
                observations=observations,
                candidate_domain_plan=candidate_domain_plan,
                context_snapshot=context_snapshot,
            )
            next_state["candidate_tool_names"] = candidate_tool_names
            next_state["candidate_domains"] = dict(candidate_domain_plan)
            expanded_domains = list(candidate_domain_plan.get("expanded_domains") or [])
            if expanded_domains:
                note = f"react supervisor expanded domains: {','.join(expanded_domains)}"
                if note not in list(next_state.get("transition_notes") or []):
                    next_state.setdefault("transition_notes", []).append(note)
                for domain in expanded_domains:
                    if domain not in expanded_domains_seen:
                        expanded_domains_seen.append(domain)
                next_state["expanded_domains_seen"] = expanded_domains_seen
            if self._should_run_expansion_probe(
                candidate_domain_plan=candidate_domain_plan,
                observations=observations,
            ):
                probe_tool_names = self._expansion_probe_tool_names(
                    candidate_domain_plan=candidate_domain_plan,
                    observations=observations,
                )
                if probe_tool_names:
                    probe_results = await asyncio.gather(
                        *[
                            self._run_named_tool(
                                request=request,
                                tool_name=tool_name,
                                arguments={"service": request.service} if request.service else {},
                                tool_cache=tool_cache,
                                extra_shared_context=self._build_tool_shared_context(
                                    context_snapshot=next_state.get("context_snapshot") or incident_state.context_snapshot,
                                    incident_state=incident_state,
                                ),
                                activity_context=activity_context,
                            )
                            for tool_name in probe_tool_names
                        ]
                    )
                    expansion_probe_count += 1
                    next_state["expansion_probe_count"] = expansion_probe_count
                    for tool_name in probe_tool_names:
                        if tool_name not in expansion_probe_tools:
                            expansion_probe_tools.append(tool_name)
                    next_state["expansion_probe_tools"] = expansion_probe_tools
                    tool_calls_used, observations, pinned_findings, working_memory_summary, evidence_evaluation = self._apply_observation_results(
                        next_state=next_state,
                        incident_state=incident_state,
                        observations=observations,
                        results=probe_results,
                        tool_cache=tool_cache,
                        tool_calls_used=tool_calls_used,
                    )
                    next_state.setdefault("transition_notes", []).append(
                        f"react supervisor auto expansion probe: {','.join(probe_tool_names)}"
                    )
                    if self._should_stop_after_observations(
                        observations=observations,
                        evidence_evaluation=evidence_evaluation,
                        candidate_tool_names=candidate_tool_names,
                    ):
                        next_state.setdefault("transition_notes", []).append("react supervisor stopped after expansion probe")
                        next_state["stop_reason"] = "expanded_domain_probe_sufficient"
                        return self._build_final_response(
                            next_state=next_state,
                            request=request,
                            context_snapshot=context_snapshot,
                            observations=observations,
                            content=self._build_early_stop_answer(observations),
                            incident_state=incident_state,
                        )
                    if tool_calls_used >= self.max_tool_calls:
                        next_state.setdefault("transition_notes", []).append("tool budget reached during expansion probe")
                        next_state["stop_reason"] = "tool_budget_reached"
                        break
                    continue
            messages = self._build_iteration_messages(
                state=next_state,
                observations=observations,
                working_memory_summary=working_memory_summary,
                pinned_findings=pinned_findings,
            )
            response = await self.llm.chat(
                messages,
                tools=[self.tools[name].as_openai_tool() for name in candidate_tool_names],
            )
            tool_calls = response.get("tool_calls") if isinstance(response, dict) else None
            content = str(response.get("content") or "") if isinstance(response, dict) else ""
            next_state.setdefault("transition_notes", []).append(f"react iteration {iteration} completed")

            if not isinstance(tool_calls, list) or not tool_calls:
                forced_tool_names, forced_results = await self._run_initial_evidence_probe_if_needed(
                    request=request,
                    candidate_tool_names=candidate_tool_names,
                    observations=observations,
                    tool_cache=tool_cache,
                    context_snapshot=next_state.get("context_snapshot") or incident_state.context_snapshot,
                    incident_state=incident_state,
                    activity_context=activity_context,
                )
                if forced_results:
                    tool_calls_used, observations, pinned_findings, working_memory_summary, evidence_evaluation = self._apply_observation_results(
                        next_state=next_state,
                        incident_state=incident_state,
                        observations=observations,
                        results=forced_results,
                        tool_cache=tool_cache,
                        tool_calls_used=tool_calls_used,
                    )
                    next_state.setdefault("transition_notes", []).append(
                        f"react supervisor forced initial evidence probe: {','.join(forced_tool_names)}"
                    )
                    if self._should_stop_after_observations(
                        observations=observations,
                        evidence_evaluation=evidence_evaluation,
                        candidate_tool_names=candidate_tool_names,
                    ):
                        next_state.setdefault("transition_notes", []).append("react supervisor stopped after forced initial probe")
                        next_state["stop_reason"] = "forced_initial_probe_sufficient"
                        return self._build_final_response(
                            next_state=next_state,
                            request=request,
                            context_snapshot=context_snapshot,
                            observations=observations,
                            content=self._build_early_stop_answer(observations),
                            incident_state=incident_state,
                        )
                    if tool_calls_used >= self.max_tool_calls:
                        next_state.setdefault("transition_notes", []).append("tool budget reached during forced initial probe")
                        next_state["stop_reason"] = "tool_budget_reached"
                        break
                    continue
                return self._build_final_response(
                    next_state=next_state,
                    request=request,
                    context_snapshot=context_snapshot,
                    observations=observations,
                    content=content,
                    incident_state=incident_state,
                )

            batch_calls = tool_calls[: max(0, self.max_tool_calls - tool_calls_used)]
            valid_calls = []
            rejected_calls: list[str] = []
            for call in batch_calls:
                function = call.get("function") or {}
                tool_name = str(function.get("name") or "")
                if tool_name in candidate_tool_names:
                    valid_calls.append(call)
                elif tool_name:
                    rejected_calls.append(tool_name)
            if rejected_calls:
                rejected_tool_call_count += len(rejected_calls)
                next_state["rejected_tool_call_count"] = rejected_tool_call_count
                for tool_name in rejected_calls:
                    if tool_name not in rejected_tool_call_names:
                        rejected_tool_call_names.append(tool_name)
                next_state["rejected_tool_call_names"] = rejected_tool_call_names
                next_state.setdefault("transition_notes", []).append(
                    f"react supervisor rejected non-candidate tools: {','.join(rejected_calls)}"
                )
            if not valid_calls:
                forced_tool_names, forced_results = await self._run_initial_evidence_probe_if_needed(
                    request=request,
                    candidate_tool_names=candidate_tool_names,
                    observations=observations,
                    tool_cache=tool_cache,
                    context_snapshot=next_state.get("context_snapshot") or incident_state.context_snapshot,
                    incident_state=incident_state,
                    activity_context=activity_context,
                )
                if forced_results:
                    tool_calls_used, observations, pinned_findings, working_memory_summary, evidence_evaluation = self._apply_observation_results(
                        next_state=next_state,
                        incident_state=incident_state,
                        observations=observations,
                        results=forced_results,
                        tool_cache=tool_cache,
                        tool_calls_used=tool_calls_used,
                    )
                    next_state.setdefault("transition_notes", []).append(
                        f"react supervisor forced initial evidence probe after invalid calls: {','.join(forced_tool_names)}"
                    )
                    if tool_calls_used >= self.max_tool_calls:
                        next_state.setdefault("transition_notes", []).append("tool budget reached during forced initial probe")
                        next_state["stop_reason"] = "tool_budget_reached"
                        break
                    continue
                return self._build_final_response(
                    next_state=next_state,
                    request=request,
                    context_snapshot=context_snapshot,
                    observations=observations,
                    content=content,
                    incident_state=incident_state,
                )

            valid_calls = valid_calls[: self.max_parallel_branches]
            results = await asyncio.gather(
                *[
                    self._run_tool_call(
                        request=request,
                        call=call,
                        tool_cache=tool_cache,
                        extra_shared_context=self._build_tool_shared_context(
                            context_snapshot=next_state.get("context_snapshot") or incident_state.context_snapshot,
                            incident_state=incident_state,
                        ),
                        activity_context=activity_context,
                    )
                    for call in valid_calls
                ]
            )
            tool_calls_used, observations, pinned_findings, working_memory_summary, evidence_evaluation = self._apply_observation_results(
                next_state=next_state,
                incident_state=incident_state,
                observations=observations,
                results=results,
                tool_cache=tool_cache,
                tool_calls_used=tool_calls_used,
            )
            if self._should_stop_after_observations(
                observations=observations,
                evidence_evaluation=evidence_evaluation,
                candidate_tool_names=candidate_tool_names,
            ):
                next_state.setdefault("transition_notes", []).append("react supervisor stopped after sufficient domain evidence")
                next_state["stop_reason"] = "evidence_sufficient_early_stop"
                return self._build_final_response(
                    next_state=next_state,
                    request=request,
                    context_snapshot=context_snapshot,
                    observations=observations,
                    content=self._build_early_stop_answer(observations),
                    incident_state=incident_state,
                )

            if tool_calls_used >= self.max_tool_calls:
                next_state.setdefault("transition_notes", []).append("tool budget reached inside react supervisor")
                next_state["stop_reason"] = "tool_budget_reached"
                break

        evidence_evaluation = self._evaluate_evidence(observations)
        context_snapshot = next_state.get("context_snapshot") or incident_state.context_snapshot
        next_state["stop_reason"] = next_state.get("stop_reason") or "iteration_guardrail_reached"
        incident_state.status = "completed"
        incident_state.final_summary = "已达到当前轮次或工具预算上限。"
        react_runtime = self._build_react_runtime(next_state=next_state, observations=observations)
        confidence = float(next_state.get("confidence") or 0.0)
        user_report = self._build_user_diagnosis_report(
            request=request,
            observations=observations,
            confidence=confidence,
            stop_reason=str(next_state.get("stop_reason") or ""),
        )
        incident_state.final_message = user_report["message"]
        incident_state.metadata["react_runtime"] = react_runtime
        next_state["response"] = {
            "ticket_id": request.ticket_id,
            "status": "completed",
            "message": incident_state.final_message,
            "diagnosis": {
                "display_mode": "user_report",
                "summary": incident_state.final_summary,
                "conclusion": user_report["root_cause"],
                "user_report": user_report,
                "recommended_actions": user_report["recommended_actions"],
                "approval_explanation": user_report["approval_explanation"],
                "route": "react_tool_first",
                "sources": list(incident_state.rag_context.citations if incident_state.rag_context is not None else []),
                "context_snapshot": context_snapshot.model_dump() if context_snapshot is not None else None,
                "observations": observations,
                "evidence": user_report["evidence"],
                "raw_evidence": self._flatten_evidence(observations),
                "working_memory_summary": working_memory_summary,
                "pinned_findings": pinned_findings,
                "tool_calls_used": tool_calls_used,
                "confidence": confidence,
                "stop_reason": next_state.get("stop_reason"),
                "evidence_evaluation": react_runtime["evidence_evaluation"],
                "react_runtime": react_runtime,
                "incident_state": incident_state.model_dump(),
                "graph": {"transition_notes": list(next_state.get("transition_notes") or [])},
            },
        }
        next_state["pending_node"] = "finalize"
        return next_state

    def _build_iteration_messages(
        self,
        *,
        state: ReactTicketGraphState,
        observations: list[dict[str, Any]],
        working_memory_summary: str,
        pinned_findings: list[str],
    ) -> list[dict[str, Any]]:
        request = state["request"]
        incident_state = state["incident_state"]
        context_snapshot = state.get("context_snapshot") or incident_state.context_snapshot
        recent_observations = self._recent_observations_for_prompt(observations)
        candidate_tool_names = list(state.get("candidate_tool_names") or self.tools.keys())
        recent_session_events = self._recent_session_events_for_prompt(state)
        execution_context = state.get("execution_context")
        memory_summary = dict(getattr(execution_context, "memory_summary", {}) or {}) if execution_context is not None else {}
        working_memory = dict(memory_summary.get("working_memory") or {})
        payload = {
            "request": request.model_dump(),
            "working_memory": working_memory,
            "rag_context": context_snapshot.rag_context.model_dump() if context_snapshot and context_snapshot.rag_context is not None else {},
            "similar_cases": [item.model_dump() for item in list(context_snapshot.similar_cases or [])[:3]] if context_snapshot else [],
            "case_recall": dict(getattr(context_snapshot, "case_recall", {}) or {}) if context_snapshot else {},
            "diagnosis_playbooks": [item.model_dump() for item in list(getattr(context_snapshot, "diagnosis_playbooks", []) or [])[:2]] if context_snapshot else [],
            "playbook_recall": dict(getattr(context_snapshot, "playbook_recall", {}) or {}) if context_snapshot else {},
            "recent_session_events": recent_session_events,
            "pinned_findings": pinned_findings,
            "working_memory_summary": working_memory_summary,
            "recent_observations": recent_observations,
            "available_tools": candidate_tool_names,
        }
        content = json.dumps(payload, ensure_ascii=False)
        if len(content) > self.max_context_tokens:
            payload["recent_observations"] = recent_observations[-2:]
            payload["working_memory_summary"] = working_memory_summary[: max(500, self.max_context_tokens // 2)]
            payload["working_memory"] = self._compact_working_memory_for_prompt(working_memory)
            payload["pinned_findings"] = pinned_findings[:6]
            content = json.dumps(payload, ensure_ascii=False)
        if len(content) > self.max_context_tokens:
            payload["recent_observations"] = recent_observations[-1:]
            payload["working_memory_summary"] = working_memory_summary[: max(300, self.max_context_tokens // 3)]
            payload["working_memory"] = self._compact_working_memory_for_prompt(working_memory, tighter=True)
            payload["pinned_findings"] = pinned_findings[:4]
            content = json.dumps(payload, ensure_ascii=False)
        return [
            {
                "role": "system",
                "content": (
                    "你是一个运维诊断 ReAct Supervisor。"
                    "你可以直接调用提供给你的 tools。"
                    "优先选择最能验证当前主假设的最少工具，不要机械补查所有维度。"
                    "如果已有 2-3 个高价值异常证据指向同一问题域，立即停止继续调 tool。"
                    "如果多个只读检查互不依赖，可以一次返回多个 tool calls，但总是优先最关键的 2-3 个。"
                    "当 rag_context 为空、命中很少，或需要查已知故障/发布模式时，可先调用 search_knowledge_base 一次补充背景，再决定 live 检查。"
                    "working_memory 是当前会话的人工确认事实、纠错、摘要、来源引用和待决状态，优先于历史案例。"
                    "diagnosis_playbooks 是人工验证后的诊断方法卡，只能指导证据顺序，不能当成根因事实。"
                    "优先按 diagnosis_playbooks 的 recommended_steps 做最少只读检查，并满足 evidence_requirements 后再下结论。"
                    "similar_cases 只是一层历史背景提示，不等于现场证据。"
                    "只有在 service/环境已明确，且你已经拿到更具体的 symptom、failure mode 或根因方向时，才调用 search_similar_incidents。"
                    "如果 case_recall 显示 deferred_by_playbook，先执行 Playbook 推荐的 1-2 个关键只读检查，再决定是否查历史案例。"
                    "如果 case_recall 显示自动预召回被跳过，先做 1-2 个关键只读检查，再决定是否查历史案例。"
                    "如果知识搜索已经命中已知模式，且至少一个 live tool 证实了对应异常，优先直接给出阶段性结论，不要继续跨域扩查。"
                    "避免对同一个 query 重复搜索知识库。"
                    "避免对几乎相同的线索重复搜索历史案例。"
                    "当证据足够时，不要继续调 tool，直接输出 JSON：{\"final_answer\": string, \"confidence\": number}。"
                    "不要编造不存在的观测结果。"
                ),
            },
            {"role": "user", "content": content},
        ]

    async def _maybe_compact_execution_working_memory(self, state: dict[str, Any]) -> None:
        execution_context = state.get("execution_context")
        if execution_context is None:
            return
        memory_summary = dict(getattr(execution_context, "memory_summary", {}) or {})
        working_memory = memory_summary.get("working_memory")
        if not isinstance(working_memory, dict):
            return
        trigger = working_memory_compaction_trigger(working_memory)
        if not trigger:
            return
        compacted = await compact_working_memory_with_llm(working_memory, self.llm, trigger=trigger)
        memory_summary["working_memory"] = compacted
        execution_context.memory_summary = memory_summary
        state["execution_context"] = execution_context
        state["working_memory_compaction"] = dict(compacted.get("compaction") or {})
        state.setdefault("transition_notes", []).append(f"working memory compacted: {trigger}")

    @staticmethod
    def _compact_working_memory_for_prompt(working_memory: dict[str, Any], *, tighter: bool = False) -> dict[str, Any]:
        compacted = compact_working_memory(
            working_memory,
            trigger="prompt_context_budget_tighter" if tighter else "prompt_context_budget",
            source="prompt_budget",
        )
        limit = 3 if tighter else 6
        summary_limit = 500 if tighter else 900
        return {
            "task_focus": dict(compacted.get("task_focus") or {}),
            "narrative_summary": str(compacted.get("narrative_summary") or "")[-summary_limit:],
            "confirmed_facts": list(compacted.get("confirmed_facts") or [])[:limit],
            "constraints": list(compacted.get("constraints") or [])[:limit],
            "open_questions": list(compacted.get("open_questions") or [])[:limit],
            "hypotheses": list(compacted.get("hypotheses") or [])[:limit],
            "ruled_out_hypotheses": list(compacted.get("ruled_out_hypotheses") or [])[:limit],
            "key_evidence": list(compacted.get("key_evidence") or [])[:limit],
            "actions_taken": list(compacted.get("actions_taken") or [])[:limit],
            "user_corrections": list(compacted.get("user_corrections") or [])[:limit],
            "source_refs": list(compacted.get("source_refs") or [])[:limit],
            "decision_state": dict(compacted.get("decision_state") or {}),
            "compaction": dict(compacted.get("compaction") or {}),
        }

    @staticmethod
    def _recent_session_events_for_prompt(state: ReactTicketGraphState) -> list[dict[str, Any]]:
        execution_context = state.get("execution_context")
        if execution_context is None:
            return []
        memory_summary = dict(getattr(execution_context, "memory_summary", {}) or {})
        session_memory = dict(memory_summary.get("session_memory") or {})
        queue = list(session_memory.get("session_event_queue") or [])
        compact: list[dict[str, Any]] = []
        for item in queue[-3:]:
            if not isinstance(item, dict):
                continue
            compact.append(
                {
                    "source": item.get("source"),
                    "event_type": item.get("event_type"),
                    "message": item.get("message"),
                    "reason_tags": list(dict(item.get("metadata") or {}).get("reason_tags") or [])[:3],
                    "created_at": item.get("created_at"),
                    "consumed_at": item.get("consumed_at"),
                }
            )
        return compact

    def _build_final_response(
        self,
        *,
        next_state: dict[str, Any],
        request,
        context_snapshot,
        observations: list[dict[str, Any]],
        content: str,
        incident_state,
    ) -> Dict[str, Any]:
        parsed_answer = self._parse_final_answer(content)
        final_message = parsed_answer["final_answer"] or content.strip() or "已完成诊断，但模型未返回明确结论。"
        confidence = parsed_answer["confidence"]
        evidence_evaluation = self._evaluate_evidence(observations)
        incident_state.status = "completed"
        incident_state.final_summary = "已完成工具优先诊断。"
        next_state["confidence"] = confidence
        if confidence >= self.confidence_threshold:
            next_state["stop_reason"] = "model_answered"
        elif evidence_evaluation.get("enough_for_output"):
            next_state["stop_reason"] = "evidence_sufficient_low_model_confidence"
        else:
            next_state["stop_reason"] = "low_confidence"
        react_runtime = self._build_react_runtime(next_state=next_state, observations=observations)
        user_report = self._build_user_diagnosis_report(
            request=request,
            observations=observations,
            confidence=confidence,
            stop_reason=str(next_state.get("stop_reason") or ""),
            model_root_cause=final_message,
        )
        final_message = user_report["message"]
        incident_state.final_message = final_message
        incident_state.metadata["react_runtime"] = react_runtime
        next_state["response"] = {
            "ticket_id": request.ticket_id,
            "status": "completed",
            "message": final_message,
            "diagnosis": {
                "display_mode": "user_report",
                "summary": incident_state.final_summary,
                "conclusion": user_report["root_cause"],
                "user_report": user_report,
                "recommended_actions": user_report["recommended_actions"],
                "approval_explanation": user_report["approval_explanation"],
                "route": "react_tool_first",
                "sources": list(incident_state.rag_context.citations if incident_state.rag_context is not None else []),
                "context_snapshot": context_snapshot.model_dump() if context_snapshot is not None else None,
                "observations": observations,
                "evidence": user_report["evidence"],
                "raw_evidence": self._flatten_evidence(observations),
                "working_memory_summary": react_runtime["working_memory_summary"],
                "pinned_findings": react_runtime["pinned_findings"],
                "tool_calls_used": react_runtime["tool_calls_used"],
                "confidence": confidence,
                "stop_reason": next_state.get("stop_reason"),
                "evidence_evaluation": react_runtime["evidence_evaluation"],
                "react_runtime": react_runtime,
                "incident_state": incident_state.model_dump(),
                "graph": {"transition_notes": list(next_state.get("transition_notes") or [])},
            },
        }
        next_state["pending_node"] = "finalize"
        return next_state

    @staticmethod
    def _compact_tool_payload(payload: dict[str, Any], *, list_limit: int = 3, depth: int = 0) -> dict[str, Any]:
        if depth > 3:
            return {}
        compact: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                compact[key] = ReactSupervisor._compact_tool_payload(value, list_limit=list_limit, depth=depth + 1)
            elif isinstance(value, list):
                items: list[Any] = []
                for entry in value[:list_limit]:
                    if isinstance(entry, dict):
                        items.append(ReactSupervisor._compact_tool_payload(entry, list_limit=list_limit, depth=depth + 1))
                    else:
                        items.append(entry)
                compact[key] = items
            else:
                compact[key] = value
        return compact

    @staticmethod
    def _model_visible_tool_result(result: dict[str, Any]) -> dict[str, Any]:
        visible = {
            "tool_name": result.get("tool_name") or result.get("name"),
            "status": result.get("status"),
            "payload": result.get("payload") or {},
        }
        if result.get("risk"):
            visible["risk"] = result.get("risk")
        return visible

    @classmethod
    def _recent_observations_for_prompt(cls, observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for item in observations[-3:]:
            result = dict(item.get("result") or {})
            compact.append(
                {
                    "tool_name": item.get("tool_name"),
                    "arguments": item.get("arguments") or {},
                    "status": result.get("status"),
                    "payload": cls._compact_tool_payload(dict(result.get("payload") or {})),
                }
            )
        return compact

    @staticmethod
    def _derive_payload_evidence(tool_name: str, payload: dict[str, Any]) -> list[str]:
        evidence: list[str] = []

        def add(text: object) -> None:
            value = str(text or "").strip()
            if value and value not in evidence:
                evidence.append(value)

        source = str(payload.get("source") or tool_name).strip()
        if tool_name == "search_knowledge_base":
            for hit in list(payload.get("hits") or [])[:3]:
                if isinstance(hit, dict):
                    title = str(hit.get("title") or "未命名文档").strip()
                    section = str(hit.get("section") or "摘要").strip()
                    add(f"知识库命中：{title} / {section}")
        elif tool_name == "check_service_health":
            current = dict(payload.get("current") or {})
            error_rate = current.get("http_5xx_rate_percent", payload.get("error_rate_percent"))
            p99_latency = current.get("p99_latency_ms", payload.get("p99_latency_ms"))
            health_status = payload.get("health_status")
            if health_status:
                add(f"{source}: health_status={health_status}")
            if error_rate is not None:
                add(f"{source}: 5xx_rate={error_rate}%")
            if p99_latency is not None:
                add(f"{source}: p99_latency_ms={p99_latency}")
        elif tool_name == "check_recent_alerts":
            for alert in list(payload.get("alerts") or [])[:3]:
                if isinstance(alert, dict):
                    add(f"{source}: alert={alert.get('name')} status={alert.get('status')} severity={alert.get('severity')}")
        elif tool_name == "inspect_error_budget_burn":
            add(f"{source}: burn_state={payload.get('burn_state')} burn_rate={payload.get('burn_rate')}")
        elif tool_name == "check_recent_deployments":
            if payload.get("latest_revision"):
                add(f"{source}: latest_revision={payload.get('latest_revision')}")
            if payload.get("previous_revision"):
                add(f"{source}: previous_revision={payload.get('previous_revision')}")
            correlation = dict(payload.get("correlation") or {})
            if correlation.get("lag_after_deploy_minutes") is not None:
                add(f"{source}: symptom_lag_after_deploy_minutes={correlation.get('lag_after_deploy_minutes')}")
            for record in list(payload.get("release_records") or [])[:2]:
                if isinstance(record, dict):
                    add(f"{source}: release revision={record.get('revision')} health={record.get('health')}")
        elif tool_name == "get_deployment_status":
            add(f"{source}: rollout_status={payload.get('rollout_status') or dict(payload.get('argocd') or {}).get('health_status')}")
            if payload.get("current_revision"):
                add(f"{source}: current_revision={payload.get('current_revision')}")
            argocd = dict(payload.get("argocd") or {})
            if argocd:
                add(f"{source}: argocd_sync={argocd.get('sync_status')} health={argocd.get('health_status')} revision={argocd.get('revision')}")
        elif tool_name == "check_pipeline_status":
            pipeline = dict(payload.get("pipeline") or {})
            add(f"{source}: pipeline_status={pipeline.get('status') or payload.get('pipeline_status')}")
        elif tool_name == "get_change_records":
            compare = dict(payload.get("compare") or {})
            if compare:
                add(f"{source}: compare={compare.get('from_revision')}..{compare.get('to_revision')} commits={compare.get('commit_count')}")
            for change in list(payload.get("changes") or [])[:3]:
                if not isinstance(change, dict):
                    continue
                diff = str(change.get("diff_summary") or "").strip()
                if not diff:
                    hunks = [hunk for hunk in list(change.get("diff_hunks") or []) if isinstance(hunk, dict)]
                    diff = str(hunks[0].get("patch") or "").replace("\n", " ").strip() if hunks else ""
                add(f"{source}: commit={change.get('commit_id') or change.get('change_id')} file={change.get('file')} diff={diff}")
        elif tool_name == "get_rollback_history":
            candidate = dict(payload.get("rollback_candidate") or {})
            target_revision = candidate.get("target_revision") or payload.get("last_known_stable_revision")
            if target_revision:
                add(f"{source}: rollback_target={target_revision}")
        elif tool_name == "check_canary_status":
            add(f"{source}: canary_status={payload.get('canary_status')}")
            add(f"{source}: canary_weight={payload.get('current_weight_percent')}%")
            metrics = dict(payload.get("metrics") or {})
            if metrics:
                add(f"{source}: analysis_status={metrics.get('analysis_status')} success_rate={metrics.get('success_rate_percent')} p99={metrics.get('p99_latency_ms')}")
        elif tool_name == "check_pod_status":
            if payload.get("ready_replicas") is not None and payload.get("desired_replicas") is not None:
                add(f"{source}: ready={payload.get('ready_replicas')}/{payload.get('desired_replicas')}")
            for pod in list(payload.get("pods") or [])[:3]:
                if isinstance(pod, dict):
                    add(f"{source}: pod={pod.get('name')} status={pod.get('status')} restarts={pod.get('restarts')}")
        elif tool_name == "inspect_pod_logs":
            if payload.get("error_pattern"):
                add(f"{source}: error_pattern={payload.get('error_pattern')}")
            counts = dict(payload.get("parsed_error_counts") or {})
            for error_name, count in counts.items():
                if count:
                    add(f"{source}: {error_name}={count}")
            for stream in list(payload.get("log_streams") or [])[:2]:
                if not isinstance(stream, dict):
                    continue
                for entry in list(stream.get("entries") or [])[:2]:
                    if isinstance(entry, dict):
                        add(f"{source}: {entry.get('level')} {entry.get('message')}")
        elif tool_name == "inspect_pod_events":
            add(f"{source}: last_termination_reason={payload.get('last_termination_reason')}")
            for event in list(payload.get("events") or [])[:2]:
                if isinstance(event, dict):
                    add(f"{source}: event={event.get('type')}/{event.get('reason')} {event.get('message')}")
        elif tool_name == "inspect_jvm_memory":
            heap = dict(payload.get("heap") or {})
            add(f"{source}: heap_usage_ratio={heap.get('usage_ratio') or payload.get('heap_usage_ratio')}")
            add(f"{source}: gc_pressure={payload.get('gc_pressure')}")
        else:
            for key in (
                "connectivity_status",
                "dependency_status",
                "route_status",
                "resolution_status",
                "policy_status",
                "pool_state",
                "db_health",
                "slow_query_count",
                "deadlock_count",
                "rollback_rate",
                "timeout_ratio",
                "lb_status",
                "quota_state",
                "cpu_saturation",
            ):
                value = payload.get(key)
                if value is not None and value != "" and value != []:
                    add(f"{source}: {key}={value}")
            for snippet in list(payload.get("log_snippets") or [])[:2]:
                add(f"{source}: {snippet}")

        return evidence[:8]

    @staticmethod
    def _derive_observation_evidence(item: dict[str, Any]) -> list[str]:
        tool_name = str(item.get("tool_name") or "")
        result = dict(item.get("result") or {})
        return ReactSupervisor._derive_payload_evidence(tool_name, dict(result.get("payload") or {}))

    def _summarize_observations(self, observations: list[dict[str, Any]]) -> str:
        if len(observations) <= self.summary_after_n_steps:
            return ""
        summary_lines: list[str] = []
        for item in observations[:-2]:
            tool_name = str(item.get("tool_name") or "")
            evidence = ", ".join(self._derive_observation_evidence(item)[:2])
            line = f"{tool_name}: {evidence}".strip()
            if line and line not in summary_lines:
                summary_lines.append(line)
        return "\n".join(summary_lines[:8])

    @staticmethod
    def _extract_pinned_findings(observations: list[dict[str, Any]]) -> list[str]:
        pinned: list[str] = []
        for item in observations:
            for entry in ReactSupervisor._derive_observation_evidence(item):
                text = str(entry).strip()
                if text and text not in pinned:
                    pinned.append(text)
                if len(pinned) >= 8:
                    return pinned
        return pinned

    def _evaluate_evidence(self, observations: list[dict[str, Any]]) -> dict[str, Any]:
        evidence = self._flatten_evidence(observations)
        observation_count = len(observations)
        unique_evidence_count = len(evidence)
        anomalous_observation_count = self._count_anomalous_observations(observations)
        evidence_strength = min(1.0, unique_evidence_count / 6)
        enough_for_output = bool(unique_evidence_count >= 3)
        return {
            "observation_count": observation_count,
            "unique_evidence_count": unique_evidence_count,
            "anomalous_observation_count": anomalous_observation_count,
            "evidence_strength": round(evidence_strength, 3),
            "enough_for_output": enough_for_output,
        }

    @staticmethod
    def _flatten_evidence(observations: list[dict[str, Any]]) -> list[str]:
        evidence: list[str] = []
        for item in observations:
            for entry in ReactSupervisor._derive_observation_evidence(item):
                text = str(entry).strip()
                if text and text not in evidence:
                    evidence.append(text)
                if len(evidence) >= 8:
                    return evidence
        return evidence

    def _build_user_diagnosis_report(
        self,
        *,
        request,
        observations: list[dict[str, Any]],
        confidence: float,
        stop_reason: str | None,
        model_root_cause: str | None = None,
    ) -> dict[str, Any]:
        evidence = self._user_facing_evidence(observations)
        inferred_root_cause = self._infer_user_root_cause(request=request, observations=observations)
        model_root_cause_text = str(model_root_cause or "").strip()
        root_cause = (
            model_root_cause_text
            if model_root_cause_text and not self._is_low_value_model_root_cause(model_root_cause_text)
            else inferred_root_cause
        )
        ruled_out = self._infer_ruled_out_findings(observations)
        recommended_actions = self._build_user_recommended_actions(request=request, observations=observations)
        approval_explanation = self._build_approval_explanation(observations)
        confidence_text = f"{confidence:.2f}" if isinstance(confidence, (float, int)) else "-"
        lines = [f"初步根因判断：{root_cause}", "", "关键证据："]
        if evidence:
            lines.extend([f"- {item}" for item in evidence[:6]])
        else:
            lines.append("- 当前还没有收集到足够强的异常证据。")
        if ruled_out:
            lines.extend(["", "已初步排除/弱化："])
            lines.extend([f"- {item}" for item in ruled_out[:4]])
        lines.extend(["", "建议下一步："])
        lines.extend([f"- {item}" for item in recommended_actions[:5]])
        lines.extend([
            "",
            f"为什么没有弹出执行审批：{approval_explanation}",
            f"置信度：{confidence_text}",
        ])
        if stop_reason:
            lines.append(f"停止原因：{stop_reason}")
        return {
            "message": "\n".join(lines).strip(),
            "root_cause": root_cause,
            "evidence": evidence,
            "ruled_out": ruled_out,
            "recommended_actions": recommended_actions,
            "approval_explanation": approval_explanation,
            "confidence": float(confidence or 0.0),
            "stop_reason": stop_reason or "",
        }

    def _user_facing_evidence(self, observations: list[dict[str, Any]]) -> list[str]:
        evidence: list[str] = []
        for item in observations:
            tool_name = str(item.get("tool_name") or "")
            result = dict(item.get("result") or {})
            payload = dict(result.get("payload") or {})
            if tool_name == "search_knowledge_base":
                for hit in list(payload.get("hits") or [])[:3]:
                    if isinstance(hit, dict):
                        title = str(hit.get("title") or "未命名文档").strip()
                        section = str(hit.get("section") or "摘要").strip()
                        evidence.append(f"知识库命中：{title} / {section}。")
            elif tool_name == "check_pod_status":
                ready = payload.get("ready_replicas")
                desired = payload.get("desired_replicas")
                if ready is not None and desired is not None:
                    try:
                        ready_count = int(ready)
                        desired_count = int(desired)
                    except (TypeError, ValueError):
                        ready_count = desired_count = -1
                    if desired_count >= 0 and ready_count >= desired_count:
                        evidence.append(f"Pod 就绪副本 {ready}/{desired}，副本数正常。")
                    else:
                        evidence.append(f"Pod 就绪副本 {ready}/{desired}，存在副本不可用。")
                for pod in list(payload.get("pods") or [])[:3]:
                    status = str(pod.get("status") or "")
                    restarts = pod.get("restarts")
                    if status and status.lower() != "running":
                        evidence.append(f"{pod.get('name') or 'pod'} 当前状态为 {status}，restarts={restarts}。")
                    elif restarts and int(restarts or 0) > 0:
                        evidence.append(f"{pod.get('name') or 'pod'} 发生过重启，restarts={restarts}。")
            elif tool_name == "inspect_pod_events":
                reason = str(payload.get("last_termination_reason") or "")
                event_count = payload.get("event_count")
                if reason and reason.lower() not in {"none", "healthy", "running"}:
                    evidence.append(f"Pod 事件显示最近终止/重启原因：{reason}。")
                elif reason.lower() in {"none", "healthy", "running"}:
                    evidence.append("Pod 事件未显示异常终止原因。")
                if event_count is not None:
                    evidence.append(f"Pod 事件数：{event_count}。")
            elif tool_name == "inspect_pod_logs":
                if payload.get("oom_detected"):
                    evidence.append("Pod 日志中检测到 OOM/内存不足信号。")
                error_pattern = str(payload.get("error_pattern") or "")
                if error_pattern and error_pattern.lower() not in {"none", "healthy"}:
                    evidence.append(f"Pod 日志错误模式：{error_pattern}。")
                elif not payload.get("oom_detected"):
                    evidence.append("Pod 日志未发现明显 OOM 或应用异常模式。")
            elif tool_name == "check_recent_deployments":
                if payload.get("has_recent_deploy"):
                    latest = str(payload.get("latest_revision") or "unknown")
                    previous = str(payload.get("previous_revision") or "")
                    suffix = f"，上一稳定版本 {previous}" if previous else ""
                    evidence.append(f"故障窗口附近存在发布：{latest}{suffix}。")
                for signal in list(payload.get("signals") or [])[:2]:
                    evidence.append(str(signal))
            elif tool_name == "get_deployment_status":
                rollout_status = str(payload.get("rollout_status") or "")
                if rollout_status and rollout_status.lower() not in {"stable", "healthy", "success"}:
                    evidence.append(f"当前 rollout 状态为 {rollout_status}。")
            elif tool_name == "get_change_records":
                changes = list(payload.get("changes") or [])
                change_ids = []
                for change in changes[:3]:
                    if not isinstance(change, dict):
                        continue
                    commit_id = str(change.get("commit_id") or change.get("change_id") or "").strip()
                    summary = str(change.get("summary") or change.get("diff_summary") or change.get("file") or "").strip()
                    if commit_id:
                        change_ids.append(f"{commit_id}{f'（{summary}）' if summary else ''}")
                if change_ids:
                    evidence.append(f"故障窗口附近存在变更记录：{', '.join(change_ids)}。")
            elif tool_name == "get_rollback_history":
                stable_revision = str(payload.get("last_known_stable_revision") or "").strip()
                if stable_revision:
                    evidence.append(f"可回滚上一稳定版本：{stable_revision}。")
            elif tool_name == "inspect_vpc_connectivity":
                connectivity_status = str(payload.get("connectivity_status") or "")
                if connectivity_status and connectivity_status.lower() != "healthy":
                    evidence.append(f"VPC 连通性检查结果为 {connectivity_status}。")
            elif tool_name == "inspect_upstream_dependency":
                dependency_status = str(payload.get("dependency_status") or "")
                timeout_ratio = payload.get("timeout_ratio")
                if dependency_status and dependency_status.lower() != "healthy":
                    suffix = f"，timeout_ratio={timeout_ratio}" if timeout_ratio is not None else ""
                    evidence.append(f"上游依赖状态为 {dependency_status}{suffix}。")
            elif tool_name == "inspect_connection_pool":
                pool_state = str(payload.get("pool_state") or "")
                active = payload.get("active_connections")
                maximum = payload.get("max_connections")
                if pool_state and pool_state.lower() != "healthy":
                    suffix = f"，连接数 {active}/{maximum}" if active is not None and maximum is not None else ""
                    evidence.append(f"数据库连接池状态为 {pool_state}{suffix}。")
            elif tool_name == "inspect_slow_queries":
                slow_query_count = payload.get("slow_query_count")
                max_latency_ms = payload.get("max_latency_ms")
                if slow_query_count is not None:
                    suffix = f"，最大延迟 {max_latency_ms}ms" if max_latency_ms is not None else ""
                    evidence.append(f"慢查询数量为 {slow_query_count}{suffix}。")
            for entry in list(result.get("evidence") or []):
                text = str(entry).strip()
                if text and text not in evidence and not self._is_low_value_evidence(text):
                    evidence.append(text)
            if len(evidence) >= 8:
                break
        deduped: list[str] = []
        for item in evidence:
            if item and item not in deduped:
                deduped.append(item)
        return deduped[:8]

    @staticmethod
    def _is_low_value_evidence(text: str) -> bool:
        lowered = text.lower()
        low_value_tokens = (
            "request completed",
            "latency within baseline",
            "dependency=healthy",
            "timeout_ratio=0.0",
            "connectivity=healthy",
            "pool=healthy",
        )
        if any(token in lowered for token in low_value_tokens):
            return True
        return lowered.startswith(("ready ", "last_termination_reason=", "event_count="))

    @staticmethod
    def _is_low_value_model_root_cause(text: str) -> bool:
        lowered = text.lower().strip()
        if lowered in {"", "none", "unknown", "n/a"}:
            return True
        low_value_tokens = (
            "当前证据已经足够",
            "基于以下事实给出诊断结论",
            "已完成诊断",
            "模型未返回明确结论",
        )
        return any(token in text for token in low_value_tokens)

    def _infer_user_root_cause(self, *, request, observations: list[dict[str, Any]]) -> str:
        payloads = {str(item.get("tool_name") or ""): dict(dict(item.get("result") or {}).get("payload") or {}) for item in observations}
        pod_events = payloads.get("inspect_pod_events") or {}
        pod_logs = payloads.get("inspect_pod_logs") or {}
        pod_status = payloads.get("check_pod_status") or {}
        vpc = payloads.get("inspect_vpc_connectivity") or {}
        upstream = payloads.get("inspect_upstream_dependency") or {}
        pool = payloads.get("inspect_connection_pool") or {}
        slow_queries = payloads.get("inspect_slow_queries") or {}
        changes = list((payloads.get("get_change_records") or {}).get("changes") or [])
        reason = str(pod_events.get("last_termination_reason") or "")
        has_unready = any(str(pod.get("status") or "").lower() not in {"", "running"} for pod in list(pod_status.get("pods") or []))
        has_restarts = any(int(pod.get("restarts") or 0) > 0 for pod in list(pod_status.get("pods") or []))
        message = str(getattr(request, "message", "") or "")
        has_recent_change = bool(changes) or "发布" in message or "变更" in message
        connectivity_status = str(vpc.get("connectivity_status") or "").lower()
        dependency_status = str(upstream.get("dependency_status") or "").lower()
        pool_state = str(pool.get("pool_state") or "").lower()
        slow_query_count = int(slow_queries.get("slow_query_count") or 0)
        deploy_payload = {
            "check_recent_deployments": payloads.get("check_recent_deployments") or {},
            "get_deployment_status": payloads.get("get_deployment_status") or {},
            "check_service_health": payloads.get("check_service_health") or {},
            "get_change_records": payloads.get("get_change_records") or {},
        }
        if self._has_deploy_regression_evidence(deploy_payload):
            change = self._select_suspect_change(deploy_payload)
            commit_id = str(change.get("commit_id") or change.get("change_id") or "").strip()
            change_summary = str(change.get("summary") or change.get("diff_summary") or change.get("file") or "").strip()
            if commit_id:
                return f"高概率是近期发布回归：commit {commit_id}（{change_summary or '变更内容'}）与错误率/延迟升高时间窗口重合。"
            return "高概率是近期发布或配置变更回归，变更窗口与服务错误率/延迟升高时间重合。"
        if connectivity_status in {"blocked", "degraded", "unstable"} or dependency_status in {"degraded", "timeout", "unhealthy"}:
            return "高概率是网络链路或上游依赖退化导致请求超时，需要优先核对 VPC 连通性、依赖状态和超时比例。"
        if pool_state in {"saturated", "degraded", "exhausted"}:
            return "高概率是数据库连接池饱和导致请求排队或超时，需要优先核对连接数、慢查询和连接释放情况。"
        if slow_query_count > 0:
            return "高概率是数据库慢查询或实例退化拖慢请求链路，需要结合慢查询和连接池状态继续确认。"
        error_pattern = str(pod_logs.get("error_pattern") or "").lower()
        if error_pattern and error_pattern not in {"none", "healthy"}:
            suffix = "；故障窗口附近存在变更，需要优先核对本次发布/配置是否引入该错误。" if has_recent_change else "。"
            return f"高概率是应用运行时错误导致请求失败或超时，日志错误模式为 {error_pattern}{suffix}"
        if reason.lower() == "oomkilled" or bool(pod_logs.get("oom_detected")):
            suffix = "；且故障窗口附近存在变更，需重点核对本次发布/配置是否改变了内存占用或资源限制。" if has_recent_change else "。"
            return f"高概率是 Pod 内存不足/OOMKilled 导致容器反复重启{suffix}"
        if reason and reason.lower() not in {"", "none", "running"}:
            suffix = "；近期变更与故障现象存在时间相关性，需要优先核对发布和配置差异。" if has_recent_change else "。"
            return f"高概率是部分 Pod 容器异常退出导致服务副本不可用，当前事件原因为 {reason}{suffix}"
        if has_unready or has_restarts:
            suffix = "；近期发布/配置变更是当前最可疑的触发因素。" if has_recent_change else "。"
            return f"高概率是 Pod 健康状态异常或重启导致可用副本不足{suffix}"
        if has_recent_change:
            return "当前已发现故障窗口附近存在变更，但 Pod、日志、事件或依赖检查尚未形成异常闭环；需要继续核对变更 diff、指标拐点和错误样本后再确认是否为发布回归。"
        return "当前证据还不足以确认单一根因；已完成的只读检查未形成强异常信号，需要补充更具体的错误样本、指标和变更内容。"

    @staticmethod
    def _infer_ruled_out_findings(observations: list[dict[str, Any]]) -> list[str]:
        ruled_out: list[str] = []
        for item in observations:
            tool_name = str(item.get("tool_name") or "")
            payload = dict(dict(item.get("result") or {}).get("payload") or {})
            if tool_name == "inspect_upstream_dependency" and str(payload.get("dependency_status") or "").lower() == "healthy":
                ruled_out.append("上游依赖当前为 healthy，暂不优先作为主因。")
            if tool_name == "inspect_vpc_connectivity" and str(payload.get("connectivity_status") or "").lower() == "healthy":
                ruled_out.append("VPC 连通性当前为 healthy，网络链路故障优先级降低。")
            if tool_name == "check_pipeline_status" and str(payload.get("pipeline_status") or "").lower() == "healthy":
                ruled_out.append("流水线状态为 healthy，单纯流水线失败不是当前主因。")
        return list(dict.fromkeys(ruled_out))

    def _build_user_recommended_actions(self, *, request, observations: list[dict[str, Any]]) -> list[str]:
        payloads = {str(item.get("tool_name") or ""): dict(dict(item.get("result") or {}).get("payload") or {}) for item in observations}
        observed_tools = set(payloads.keys())
        pod_status = payloads.get("check_pod_status") or {}
        pod_events = payloads.get("inspect_pod_events") or {}
        pod_logs = payloads.get("inspect_pod_logs") or {}
        vpc = payloads.get("inspect_vpc_connectivity") or {}
        upstream = payloads.get("inspect_upstream_dependency") or {}
        pool = payloads.get("inspect_connection_pool") or {}
        changes = list((payloads.get("get_change_records") or {}).get("changes") or [])
        reason = str(pod_events.get("last_termination_reason") or "")
        error_pattern = str(pod_logs.get("error_pattern") or "")
        message = str(getattr(request, "message", "") or "").lower()
        mentions_db = any(token in message for token in ("数据库", "db", "连接池", "慢查询"))
        mentions_latency = any(token in message for token in ("超时", "timeout", "延迟", "p95", "502"))

        def ready_is_normal() -> bool:
            ready = pod_status.get("ready_replicas")
            desired = pod_status.get("desired_replicas")
            if ready is None or desired is None:
                return False
            try:
                return int(ready) >= int(desired)
            except (TypeError, ValueError):
                return False

        pod_logs_checked = "inspect_pod_logs" in observed_tools
        pod_events_checked = "inspect_pod_events" in observed_tools
        pod_status_checked = "check_pod_status" in observed_tools
        pod_logs_clean = pod_logs_checked and not bool(pod_logs.get("oom_detected")) and error_pattern.lower() in {"", "none", "healthy"}
        pod_events_clean = pod_events_checked and reason.lower() in {"", "none", "healthy", "running"}
        pod_status_normal = pod_status_checked and ready_is_normal()
        upstream_healthy = str(upstream.get("dependency_status") or "").lower() == "healthy"
        vpc_healthy = str(vpc.get("connectivity_status") or "").lower() == "healthy"
        pool_state = str(pool.get("pool_state") or "").lower()

        actions: list[str] = []
        deploy_payload = {
            "check_recent_deployments": payloads.get("check_recent_deployments") or {},
            "get_deployment_status": payloads.get("get_deployment_status") or {},
            "check_service_health": payloads.get("check_service_health") or {},
            "get_change_records": payloads.get("get_change_records") or {},
        }
        if changes:
            formatted_changes = []
            for change in changes[:3]:
                if not isinstance(change, dict):
                    continue
                commit_id = str(change.get("commit_id") or change.get("change_id") or "").strip()
                summary = str(change.get("summary") or change.get("diff_summary") or change.get("file") or "").strip()
                if commit_id:
                    formatted_changes.append(f"{commit_id}{f'（{summary}）' if summary else ''}")
            actions.append(f"已查询到变更记录 {', '.join(formatted_changes) or '若干变更'}，下一步应打开变更 diff、部署版本和配置项，和故障时间线逐项对齐。")
            if self._has_deploy_regression_evidence(deploy_payload):
                rollback = payloads.get("get_rollback_history") or {}
                stable_revision = str(rollback.get("last_known_stable_revision") or "上一稳定版本")
                actions.append(f"当前证据已满足发布回归候选，建议发起回滚到 {stable_revision} 的审批；审批通过后再执行回滚动作。")
        elif "get_change_records" not in observed_tools:
            actions.append("先查询最近变更记录，再判断发布/配置变更是否和故障时间窗口重合。")

        if reason.lower() == "oomkilled" or bool(pod_logs.get("oom_detected")):
            actions.append("已发现 OOM/内存不足信号，下一步核对 memory request/limit、JVM heap 参数和本次发布的内存占用变化。")
        elif error_pattern and error_pattern.lower() not in {"none", "healthy"}:
            actions.append(f"已在 Pod 日志发现 {error_pattern}，下一步应核对对应异常堆栈、变更 diff 和受影响接口。")
        elif pod_status_normal and pod_logs_clean and pod_events_clean:
            actions.append("Pod 状态、日志和事件已检查且未见明显容器异常，当前不应继续把 Pod 重启作为主方向。")
        elif not (pod_status_checked and pod_logs_checked and pod_events_checked):
            actions.append("补齐 Pod 状态、上一轮容器日志和事件检查后，再判断是否存在容器退出或探针失败。")

        if pool_state in {"saturated", "degraded", "exhausted"}:
            actions.append("已发现数据库连接池异常，下一步核对活跃连接数、慢查询和连接释放路径。")
        elif mentions_db and "inspect_connection_pool" not in observed_tools:
            actions.append("问题描述提到数据库/连接池，但本轮还没有连接池证据；需要补查连接池和慢查询后才能排除 DB 方向。")
        elif mentions_latency and "inspect_connection_pool" not in observed_tools and "inspect_slow_queries" not in observed_tools:
            actions.append("请求延迟/502 场景建议补查数据库连接池和慢查询，避免只凭 Pod 与变更证据下结论。")

        if upstream_healthy and vpc_healthy:
            actions.append("上游依赖和 VPC 已检查为 healthy，除非出现新的错误样本，否则网络/依赖方向优先级降低。")
        elif "inspect_upstream_dependency" not in observed_tools and mentions_latency:
            actions.append("补查上游依赖超时比例和 VPC 连通性，确认 502/超时是否来自依赖或网络链路。")

        actions.append("在确认影响范围和证据闭环前，不建议直接自动重启、扩容或回滚生产服务。")
        if self._has_deploy_regression_evidence(deploy_payload):
            actions.append("回滚动作已在执行注册表中登记；如果本轮排序选择发布回归为主因，会进入人工审批后再执行。")
        else:
            actions.append("如果需要由 Agent 自动执行回滚、扩缩容或重启，需要先注册对应 action tool，并进入人工审批。")
        return list(dict.fromkeys(action for action in actions if action))

    @staticmethod
    def _build_approval_explanation(observations: list[dict[str, Any]]) -> str:
        approval_required_tools = [
            str(item.get("tool_name") or "")
            for item in observations
            if bool(dict(item.get("result") or {}).get("approval_required"))
        ]
        if approval_required_tools:
            return f"工具 {', '.join(approval_required_tools)} 返回需要审批，但本轮尚未形成可执行的审批动作。"
        return (
            "本轮只调用了只读诊断工具，并没有生成已注册的高风险执行动作；"
            "因此不会弹出审批卡片。建议中的回滚、扩容或重启仍需人工确认，"
            "或先接入对应 action tool 后再由 Agent 发起审批。"
        )

    def _parse_final_answer(self, content: str) -> dict[str, Any]:
        try:
            payload = self.llm.extract_json(content)
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        final_answer = str(payload.get("final_answer") or "").strip()
        confidence_raw = payload.get("confidence")
        try:
            confidence = float(confidence_raw)
        except Exception:
            confidence = 0.5
        confidence = max(0.0, min(confidence, 1.0))
        return {"final_answer": final_answer, "confidence": confidence}

    def _build_react_runtime(
        self,
        *,
        next_state: dict[str, Any],
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "iterations": next_state.get("iterations", 0),
            "tool_calls_used": next_state.get("tool_calls_used", 0),
            "stop_reason": next_state.get("stop_reason"),
            "working_memory_summary": str(next_state.get("working_memory_summary") or ""),
            "pinned_findings": list(next_state.get("pinned_findings") or []),
            "evidence_evaluation": self._evaluate_evidence(observations),
            "expanded_domains": list(next_state.get("expanded_domains_seen") or []),
            "expansion_probe_count": int(next_state.get("expansion_probe_count") or 0),
            "expansion_probe_tools": list(next_state.get("expansion_probe_tools") or []),
            "rejected_tool_call_count": int(next_state.get("rejected_tool_call_count") or 0),
            "rejected_tool_call_names": list(next_state.get("rejected_tool_call_names") or []),
        }

    def _apply_observation_results(
        self,
        *,
        next_state: dict[str, Any],
        incident_state,
        observations: list[dict[str, Any]],
        results: list[dict[str, Any]],
        tool_cache: dict[str, Any],
        tool_calls_used: int,
    ) -> tuple[int, list[dict[str, Any]], list[str], str, dict[str, Any]]:
        new_observations = list(observations)
        applied_count = 0
        for result in results:
            observation = result.get("observation") if isinstance(result, dict) else None
            if isinstance(observation, dict):
                new_observations.append(observation)
                self._merge_search_knowledge_observation(
                    next_state=next_state,
                    incident_state=incident_state,
                    observation=observation,
                )
                self._merge_search_similar_case_observation(
                    next_state=next_state,
                    incident_state=incident_state,
                    observation=observation,
                )
                applied_count += 1
        new_observations = new_observations[-20:]
        tool_calls_used += applied_count
        pinned_findings = self._extract_pinned_findings(new_observations)
        working_memory_summary = self._summarize_observations(new_observations)
        evidence_evaluation = self._evaluate_evidence(new_observations)
        next_state["tool_calls_used"] = tool_calls_used
        next_state["tool_cache"] = tool_cache
        next_state["observation_ledger"] = new_observations
        next_state["working_memory_summary"] = working_memory_summary
        next_state["pinned_findings"] = pinned_findings
        next_state["evidence_evaluation"] = evidence_evaluation
        incident_state.metadata["react_observations"] = new_observations
        incident_state.metadata["working_memory_summary"] = working_memory_summary
        incident_state.metadata["pinned_findings"] = pinned_findings
        incident_state.metadata["evidence_evaluation"] = evidence_evaluation
        return tool_calls_used, new_observations, pinned_findings, working_memory_summary, evidence_evaluation

    def _merge_search_knowledge_observation(
        self,
        *,
        next_state: dict[str, Any],
        incident_state,
        observation: dict[str, Any],
    ) -> None:
        if str(observation.get("tool_name") or "") != "search_knowledge_base":
            return
        result = dict(observation.get("result") or {})
        payload = dict(result.get("payload") or {})
        hits = list(payload.get("hits") or [])
        citations = [str(item) for item in list(payload.get("citations") or []) if str(item or "").strip()]
        if not hits and not citations:
            return
        query = str(dict(observation.get("arguments") or {}).get("query") or "")
        extra_bundle = RAGContextBundle.model_validate(
            {
                "query": query,
                "query_type": "tool_search",
                "hits": hits,
                "context": hits,
                "citations": citations,
                "index_info": {"source": "search_knowledge_base"},
            }
        )
        current_bundle = incident_state.rag_context if incident_state.rag_context is not None else RAGContextBundle()
        merged_bundle = self._merge_rag_bundles(current_bundle, extra_bundle)
        incident_state.rag_context = merged_bundle
        context_snapshot = next_state.get("context_snapshot") or incident_state.context_snapshot
        if context_snapshot is not None:
            context_snapshot.rag_context = merged_bundle
            incident_state.context_snapshot = context_snapshot
            next_state["context_snapshot"] = context_snapshot

    def _merge_search_similar_case_observation(
        self,
        *,
        next_state: dict[str, Any],
        incident_state,
        observation: dict[str, Any],
    ) -> None:
        if str(observation.get("tool_name") or "") != "search_similar_incidents":
            return
        result = dict(observation.get("result") or {})
        payload = dict(result.get("payload") or {})
        raw_cases = [dict(item) for item in list(payload.get("cases") or []) if isinstance(item, dict)]
        context_snapshot = next_state.get("context_snapshot") or incident_state.context_snapshot
        if context_snapshot is None:
            return
        merged_cases: dict[str, SimilarIncidentCase] = {
            str(item.case_id): item
            for item in list(getattr(context_snapshot, "similar_cases", []) or [])
            if str(getattr(item, "case_id", "") or "").strip()
        }
        added_case_hits = 0
        for item in raw_cases:
            normalized = SimilarIncidentCase.model_validate(
                {
                    "case_id": str(item.get("case_id") or ""),
                    "service": str(item.get("service") or ""),
                    "failure_mode": str(item.get("failure_mode") or ""),
                    "root_cause_taxonomy": str(item.get("root_cause_taxonomy") or ""),
                    "signal_pattern": str(item.get("signal_pattern") or ""),
                    "action_pattern": str(item.get("action_pattern") or ""),
                    "symptom": str(item.get("symptom") or ""),
                    "root_cause": str(item.get("root_cause") or ""),
                    "final_action": str(item.get("final_action") or ""),
                    "summary": str(item.get("summary") or ""),
                    "recall_source": str(item.get("recall_source") or "tool_search"),
                    "recall_score": round(float(item.get("score") or item.get("recall_score") or 0.0), 4),
                }
            )
            existing = merged_cases.get(normalized.case_id)
            if existing is None:
                merged_cases[normalized.case_id] = normalized
                added_case_hits += 1
                continue
            if normalized.recall_score > existing.recall_score:
                merged_cases[normalized.case_id] = normalized
        if raw_cases:
            context_snapshot.similar_cases = sorted(
                merged_cases.values(),
                key=lambda item: float(item.recall_score or 0.0),
                reverse=True,
            )[:6]
        index_info = dict(payload.get("index_info") or {})
        case_recall = dict(getattr(context_snapshot, "case_recall", {}) or {})
        tool_failures = list(case_recall.get("tool_failures") or [])
        if result.get("status") == "error" or index_info.get("error"):
            failure = {
                "query": str(payload.get("query") or ""),
                "error": str(index_info.get("error") or result.get("error_type") or result.get("summary") or "case_memory_search_failed"),
            }
            if failure not in tool_failures:
                tool_failures.append(failure)
        case_recall.update(
            {
                "tool_search_count": int(case_recall.get("tool_search_count") or 0) + 1,
                "last_tool_query": str(payload.get("query") or ""),
                "last_tool_status": str(result.get("status") or "completed"),
                "last_tool_hit_count": len(raw_cases),
                "tool_added_case_hits": int(case_recall.get("tool_added_case_hits") or 0) + added_case_hits,
            }
        )
        if tool_failures:
            case_recall["tool_failures"] = tool_failures[-5:]
        context_snapshot.case_recall = case_recall
        incident_state.context_snapshot = context_snapshot
        next_state["context_snapshot"] = context_snapshot

    @staticmethod
    def _merge_rag_bundles(base: RAGContextBundle, extra: RAGContextBundle) -> RAGContextBundle:
        merged = base.model_copy(deep=True)
        seen = {(item.chunk_id, item.path, item.section) for item in list(merged.context or merged.hits)}
        for item in list(extra.context or extra.hits):
            key = (item.chunk_id, item.path, item.section)
            if key in seen:
                continue
            seen.add(key)
            merged.hits.append(item)
            merged.context.append(item)
        merged.citations = list(dict.fromkeys([*merged.citations, *extra.citations]))
        merged.facts = list(merged.facts) + [fact for fact in extra.facts if fact not in merged.facts]
        merged.index_info = {
            **dict(merged.index_info or {}),
            "agentic_search_tool": True,
        }
        return merged

    async def _run_initial_evidence_probe_if_needed(
        self,
        *,
        request,
        candidate_tool_names: list[str],
        observations: list[dict[str, Any]],
        tool_cache: dict[str, Any],
        context_snapshot,
        incident_state,
        activity_context: dict[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        if self._live_observation_count(observations) > 0:
            return [], []
        probe_tool_names = self._initial_evidence_probe_tool_names(candidate_tool_names)
        if not probe_tool_names:
            return [], []
        results = await asyncio.gather(
            *[
                self._run_named_tool(
                    request=request,
                    tool_name=tool_name,
                    arguments={"service": request.service} if request.service else {},
                    tool_cache=tool_cache,
                    extra_shared_context=self._build_tool_shared_context(
                        context_snapshot=context_snapshot,
                        incident_state=incident_state,
                    ),
                    activity_context=activity_context,
                )
                for tool_name in probe_tool_names
            ]
        )
        return probe_tool_names, list(results)

    def _initial_evidence_probe_tool_names(self, candidate_tool_names: list[str]) -> list[str]:
        excluded_tools = {"search_knowledge_base", "search_similar_incidents"}
        probe_limit = max(1, min(self.max_parallel_branches, 3))
        probe_tool_names: list[str] = []
        for tool_name in candidate_tool_names:
            if tool_name in excluded_tools or tool_name not in self.tools:
                continue
            if tool_name not in probe_tool_names:
                probe_tool_names.append(tool_name)
            if len(probe_tool_names) >= probe_limit:
                break
        return probe_tool_names

    def _should_run_expansion_probe(
        self,
        *,
        candidate_domain_plan: dict[str, list[str]],
        observations: list[dict[str, Any]],
    ) -> bool:
        expanded_domains = list(candidate_domain_plan.get("expanded_domains") or [])
        if not expanded_domains or self._live_observation_count(observations) < 2:
            return False
        evidence_evaluation = self._evaluate_evidence(observations)
        if (
            evidence_evaluation.get("enough_for_output")
            and int(evidence_evaluation.get("anomalous_observation_count") or 0) >= 2
        ):
            return False
        return bool(
            self._expansion_probe_tool_names(
                candidate_domain_plan=candidate_domain_plan,
                observations=observations,
            )
        )

    def _expansion_probe_tool_names(
        self,
        *,
        candidate_domain_plan: dict[str, list[str]],
        observations: list[dict[str, Any]],
    ) -> list[str]:
        expanded_domains = list(candidate_domain_plan.get("expanded_domains") or [])
        if not expanded_domains:
            return []
        observed_tool_names = {
            str(item.get("tool_name") or "")
            for item in observations
            if str(item.get("tool_name") or "")
        }
        probe_tool_names: list[str] = []
        for domain in expanded_domains:
            remaining = [
                tool_name
                for tool_name in self._domain_expansion_priority_tool_names(domain)
                if tool_name in self.tools and tool_name not in observed_tool_names
            ]
            for tool_name in remaining:
                if tool_name not in probe_tool_names:
                    probe_tool_names.append(tool_name)
                if len(probe_tool_names) >= 2:
                    return probe_tool_names
            if probe_tool_names:
                return probe_tool_names
        return probe_tool_names

    def _resolve_candidate_domains(
        self,
        *,
        request,
        context_snapshot,
        observations: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        matched_domains = list(getattr(context_snapshot, "matched_tool_domains", []) or [])
        message_lower = str(request.message or "").lower()
        explicit_domains: list[str] = []
        if any(token in message_lower for token in ("deploy", "release", "发布", "变更", "pipeline", "回滚", "canary", "灰度")):
            explicit_domains.append("cicd")
        if any(token in message_lower for token in ("oom", "pod", "重启", "container", "oomkilled", "cpu", "线程", "thread", "throttle", "限流")):
            explicit_domains.append("k8s")
        if any(token in message_lower for token in ("502", "timeout", "超时", "ingress", "gateway", "依赖", "network")):
            explicit_domains.append("network")
        if any(token in message_lower for token in ("数据库", "db", "慢查询", "连接池", "deadlock", "复制延迟")):
            explicit_domains.append("db")
        if any(token in message_lower for token in ("quota", "配额", "bootstrap", "provision", "创建失败")):
            explicit_domains.append("sde")

        prioritized_domains: list[str] = []
        seed_domains = explicit_domains if explicit_domains else matched_domains
        for domain in seed_domains:
            value = str(domain or "").strip()
            if value and value in DOMAIN_TOOL_GROUPS and value not in prioritized_domains:
                prioritized_domains.append(value)
        matched_only_domains: list[str] = []
        for domain in matched_domains:
            value = str(domain or "").strip()
            if value and value in DOMAIN_TOOL_GROUPS and value not in prioritized_domains and value not in matched_only_domains:
                matched_only_domains.append(value)

        primary_domains = prioritized_domains[:2]
        expanded_domains: list[str] = []
        if primary_domains and self._should_expand_to_adjacent_domains(
            primary_domains=primary_domains,
            observations=observations,
        ):
            expansion_sources: list[str] = []
            if explicit_domains:
                for domain in primary_domains:
                    for adjacent in DOMAIN_ADJACENCY.get(domain, []):
                        if adjacent not in primary_domains and adjacent not in expansion_sources:
                            expansion_sources.append(adjacent)
                for domain in matched_only_domains:
                    if domain not in primary_domains and domain not in expansion_sources:
                        expansion_sources.append(domain)
            else:
                for domain in matched_only_domains:
                    if domain not in primary_domains and domain not in expansion_sources:
                        expansion_sources.append(domain)
                for domain in primary_domains:
                    for adjacent in DOMAIN_ADJACENCY.get(domain, []):
                        if adjacent not in primary_domains and adjacent not in expansion_sources:
                            expansion_sources.append(adjacent)
            for domain in expansion_sources:
                if domain not in expanded_domains:
                    expanded_domains.append(domain)
                if len(expanded_domains) >= 2:
                    break
        return {
            "prioritized_domains": prioritized_domains,
            "primary_domains": primary_domains,
            "matched_only_domains": matched_only_domains,
            "expanded_domains": expanded_domains,
        }

    def _select_candidate_tool_names(
        self,
        *,
        observations: list[dict[str, Any]],
        candidate_domain_plan: dict[str, list[str]],
        context_snapshot=None,
    ) -> list[str]:
        candidate_tool_names: list[str] = []
        for tool_name in self._playbook_recommended_tool_names(
            context_snapshot=context_snapshot,
            observations=observations,
        ):
            if tool_name not in candidate_tool_names:
                candidate_tool_names.append(tool_name)
        if self._should_include_knowledge_tool(
            context_snapshot=context_snapshot,
            observations=observations,
        ):
            candidate_tool_names.append("search_knowledge_base")
        if self._should_include_similar_case_tool(
            context_snapshot=context_snapshot,
            observations=observations,
        ):
            candidate_tool_names.append("search_similar_incidents")
        primary_domains = list(candidate_domain_plan.get("primary_domains") or [])
        expanded_domains = list(candidate_domain_plan.get("expanded_domains") or [])
        if primary_domains:
            for domain in primary_domains:
                native_tools = self._domain_native_tool_names(domain)
                helper_tools = [] if expanded_domains else self._domain_helper_tool_names(domain)
                selected_group = native_tools if not observations else native_tools + helper_tools
                for tool_name in selected_group:
                    if tool_name in self.tools and tool_name not in candidate_tool_names:
                        candidate_tool_names.append(tool_name)
            for domain in expanded_domains:
                for tool_name in self._domain_expansion_priority_tool_names(domain):
                    if tool_name in self.tools and tool_name not in candidate_tool_names:
                        candidate_tool_names.append(tool_name)
        else:
            candidate_tool_names.extend(
                [
                    "check_service_health",
                    "check_recent_alerts",
                    "check_pod_status",
                    "inspect_upstream_dependency",
                    "check_recent_deployments",
                ]
            )

        for item in observations:
            tool_name = str(item.get("tool_name") or "")
            if tool_name in self.tools and tool_name not in candidate_tool_names:
                candidate_tool_names.append(tool_name)

        normalized = [name for name in candidate_tool_names if name in self.tools]
        return normalized[:10] if normalized else list(self.tools.keys())


    def _playbook_recommended_tool_names(self, *, context_snapshot, observations: list[dict[str, Any]]) -> list[str]:
        if context_snapshot is None:
            return []
        observed_tool_names = {
            str(item.get("tool_name") or "")
            for item in observations
            if str(item.get("tool_name") or "")
        }
        tool_names: list[str] = []
        for playbook in list(getattr(context_snapshot, "diagnosis_playbooks", []) or [])[:2]:
            for step in list(getattr(playbook, "recommended_steps", []) or [])[:5]:
                if not isinstance(step, dict):
                    continue
                tool_name = str(step.get("tool_name") or step.get("tool") or "").strip()
                if tool_name and tool_name in self.tools and tool_name not in observed_tool_names and tool_name not in tool_names:
                    tool_names.append(tool_name)
                if len(tool_names) >= 4:
                    return tool_names
        return tool_names

    def _should_include_knowledge_tool(
        self,
        *,
        context_snapshot,
        observations: list[dict[str, Any]],
    ) -> bool:
        if "search_knowledge_base" not in self.tools:
            return False
        rag_context = getattr(context_snapshot, "rag_context", None)
        retrieval_expansion = getattr(context_snapshot, "retrieval_expansion", None)
        rag_hits = list(getattr(rag_context, "context", None) or getattr(rag_context, "hits", None) or [])
        subqueries = list(getattr(retrieval_expansion, "subqueries", None) or [])
        if any(str(getattr(item, "target", "") or "") in {"knowledge", "both"} for item in subqueries):
            return True
        if not observations:
            return len(rag_hits) <= 1
        evidence_evaluation = self._evaluate_evidence(observations)
        return len(rag_hits) <= 2 and not bool(evidence_evaluation.get("enough_for_output"))

    def _should_include_similar_case_tool(
        self,
        *,
        context_snapshot,
        observations: list[dict[str, Any]],
    ) -> bool:
        if "search_similar_incidents" not in self.tools or context_snapshot is None:
            return False
        request_payload = dict(getattr(context_snapshot, "request", {}) or {})
        if not str(request_payload.get("service") or "").strip():
            return False
        retrieval_expansion = getattr(context_snapshot, "retrieval_expansion", None)
        subqueries = list(getattr(retrieval_expansion, "subqueries", None) or [])
        if any(str(getattr(item, "target", "") or "") in {"cases", "both"} for item in subqueries):
            return True
        if not observations:
            return False
        similar_cases = list(getattr(context_snapshot, "similar_cases", []) or [])
        evidence_evaluation = self._evaluate_evidence(observations)
        return len(similar_cases) <= 1 and not bool(evidence_evaluation.get("enough_for_output"))

    @staticmethod
    def _domain_native_tool_names(domain: str) -> list[str]:
        group = list(DOMAIN_TOOL_GROUPS.get(domain, []))
        return [name for name in group if name not in {"check_service_health", "check_recent_alerts"}]

    @staticmethod
    def _domain_helper_tool_names(domain: str) -> list[str]:
        group = list(DOMAIN_TOOL_GROUPS.get(domain, []))
        return [name for name in group if name in {"check_service_health", "check_recent_alerts"}]

    @staticmethod
    def _domain_expansion_priority_tool_names(domain: str) -> list[str]:
        return list(DOMAIN_EXPANSION_PRIORITIES.get(domain, []))

    def _should_expand_to_adjacent_domains(
        self,
        *,
        primary_domains: list[str],
        observations: list[dict[str, Any]],
    ) -> bool:
        if self._live_observation_count(observations) < 2:
            return False
        anomaly_counts = self._domain_anomaly_counts(observations)
        primary_domain_anomalies = sum(anomaly_counts.get(domain, 0) for domain in primary_domains)
        if self._has_tool_observation(observations, "search_knowledge_base") and primary_domain_anomalies >= 1:
            return False
        if primary_domain_anomalies >= 2:
            return False
        return True

    @staticmethod
    def _live_observation_count(observations: list[dict[str, Any]]) -> int:
        return sum(1 for item in observations if str(item.get("tool_name") or "") != "search_knowledge_base")

    @staticmethod
    def _has_tool_observation(observations: list[dict[str, Any]], tool_name: str) -> bool:
        return any(str(item.get("tool_name") or "") == tool_name for item in observations)

    def _domain_anomaly_counts(self, observations: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in observations:
            tool_name = str(item.get("tool_name") or "")
            result = dict(item.get("result") or {})
            if not self._observation_has_anomaly(result):
                continue
            for domain in self._tool_domains(tool_name):
                counts[domain] = counts.get(domain, 0) + 1
        return counts

    @staticmethod
    def _tool_domains(tool_name: str) -> list[str]:
        domains: list[str] = []
        for domain, tool_names in DOMAIN_TOOL_GROUPS.items():
            if tool_name in tool_names and domain not in domains:
                domains.append(domain)
        return domains

    def _should_stop_after_observations(
        self,
        *,
        observations: list[dict[str, Any]],
        evidence_evaluation: dict[str, Any],
        candidate_tool_names: list[str],
    ) -> bool:
        if len(observations) < min(3, max(2, len(candidate_tool_names) // 2)):
            return False
        if not evidence_evaluation.get("enough_for_output"):
            return False
        anomalous_observation_count = int(evidence_evaluation.get("anomalous_observation_count") or 0)
        if anomalous_observation_count < 2:
            return False
        return True

    def _build_early_stop_answer(self, observations: list[dict[str, Any]]) -> str:
        evidence = self._flatten_evidence(observations)[:4]
        evidence_text = "；".join(evidence) if evidence else "已收集到足够工具证据"
        return json.dumps(
            {
                "final_answer": f"当前证据已经足够，先基于以下事实给出诊断结论：{evidence_text}",
                "confidence": 0.82,
            },
            ensure_ascii=False,
        )

    def _count_anomalous_observations(self, observations: list[dict[str, Any]]) -> int:
        count = 0
        for item in observations:
            result = dict(item.get("result") or {})
            if self._observation_has_anomaly(result):
                count += 1
        return count

    @staticmethod
    def _observation_has_anomaly(result: dict[str, Any]) -> bool:
        payload = dict(result.get("payload") or {})
        current = dict(payload.get("current") or {})
        argocd = dict(payload.get("argocd") or {})
        parsed_error_counts = dict(payload.get("parsed_error_counts") or {})
        if float(current.get("http_5xx_rate_percent") or payload.get("error_rate_percent") or 0.0) > 1.0:
            return True
        if int(current.get("p99_latency_ms") or payload.get("p99_latency_ms") or 0) > 1000:
            return True
        if any(int(count or 0) > 0 for count in parsed_error_counts.values()):
            return True
        if str(argocd.get("health_status") or "").lower() in {"degraded", "failed", "unhealthy"}:
            return True
        if payload.get("oom_detected") is True:
            return True
        desired_replicas = payload.get("desired_replicas")
        ready_replicas = payload.get("ready_replicas")
        if desired_replicas is not None and ready_replicas is not None and int(ready_replicas or 0) < int(desired_replicas or 0):
            return True
        for pod in list(payload.get("pods") or []):
            if not isinstance(pod, dict):
                continue
            pod_status = str(pod.get("status") or "").lower()
            last_reason = str(pod.get("last_reason") or "").lower()
            if pod_status not in {"", "running", "succeeded"} or last_reason not in {"", "none"}:
                return True
        heap_usage = float(payload.get("heap_usage_ratio") or dict(payload.get("heap") or {}).get("usage_ratio") or 0.0)
        if heap_usage >= 0.85:
            return True
        if str(payload.get("gc_pressure") or "").lower() in {"high", "critical", "degraded"}:
            return True
        if payload.get("has_recent_deploy") is True:
            return True
        if int(payload.get("change_count") or 0) > 0:
            return True
        if str(payload.get("rollout_status") or "").lower() in {"degraded", "failed", "unhealthy", "partial", "mismatch"}:
            return True
        if str(payload.get("health_status") or "").lower() in {"degraded", "unhealthy", "failed"}:
            return True
        if str(payload.get("pipeline_status") or "").lower() in {"failed", "error", "degraded"}:
            return True
        if str(payload.get("canary_status") or "").lower() in {"failed", "degraded", "unhealthy", "paused"}:
            return True
        metrics = dict(payload.get("metrics") or {})
        if str(metrics.get("analysis_status") or "").lower() in {"failed", "degraded", "error"}:
            return True
        if str(payload.get("quota_state") or "").lower() in {"exhausted", "insufficient", "blocked", "depleted"}:
            return True
        if str(payload.get("cpu_saturation") or "").lower() in {"saturated", "critical", "degraded"}:
            return True
        if payload.get("dependency_status") == "degraded":
            return True
        if payload.get("connectivity_status") == "blocked":
            return True
        if payload.get("route_status") == "mismatch_or_unhealthy":
            return True
        if payload.get("lb_status") == "degraded":
            return True
        if payload.get("pool_state") == "saturated":
            return True
        if payload.get("db_health") == "degraded":
            return True
        if int(payload.get("slow_query_count") or 0) > 0:
            return True
        if payload.get("pipeline_status") == "failed":
            return True
        if payload.get("has_recent_deploy") is True:
            return True
        if str(payload.get("error_pattern") or "").lower() not in {"", "none"}:
            return True
        if str(payload.get("health_status") or "").lower() in {"degraded", "unhealthy"}:
            return True
        if str(payload.get("last_termination_reason") or "").lower() not in {"", "none"}:
            return True
        return False

    def _activity_context_from_state(self, state: dict[str, Any], request) -> dict[str, str]:
        session_id = str(state.get("session_id") or getattr(request, "ticket_id", "") or "")
        thread_id = str(state.get("thread_id") or session_id)
        ticket_id = str(getattr(request, "ticket_id", "") or session_id)
        return {"session_id": session_id, "thread_id": thread_id, "ticket_id": ticket_id}

    def _emit_tool_activity(
        self,
        event_type: str,
        *,
        activity_context: dict[str, Any] | None,
        tool_name: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.activity_callback is None:
            return
        context = dict(activity_context or {})
        session_id = str(context.get("session_id") or "")
        thread_id = str(context.get("thread_id") or session_id)
        ticket_id = str(context.get("ticket_id") or session_id)
        if not session_id:
            return
        try:
            self.activity_callback(
                {
                    "session_id": session_id,
                    "thread_id": thread_id,
                    "ticket_id": ticket_id,
                    "event_type": event_type,
                    "payload": {"tool_name": tool_name, **dict(payload or {})},
                    "metadata": {"source": "react_supervisor"},
                }
            )
        except Exception as exc:  # pragma: no cover - activity events must not break diagnosis
            logger.warning("failed to emit tool activity event: %s", exc)

    async def _run_tool_call(
        self,
        *,
        request,
        call: dict[str, Any],
        tool_cache: dict[str, Any],
        extra_shared_context: dict[str, Any] | None = None,
        activity_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        function = call.get("function") or {}
        tool_name = str(function.get("name") or "")
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments or {})
        except Exception:
            arguments = {}
        cache_key = json.dumps({"tool_name": tool_name, "arguments": arguments}, ensure_ascii=False, sort_keys=True)
        if cache_key in tool_cache:
            payload = dict(tool_cache[cache_key])
            self._emit_tool_activity("tool.cached", activity_context=activity_context, tool_name=tool_name)
            model_payload = self._model_visible_tool_result(payload)
            return {
                "observation": {"tool_name": tool_name, "arguments": arguments, "result": payload, "cached": True},
                "tool_message": {
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or f"{tool_name}-{uuid4().hex[:8]}"),
                    "name": tool_name,
                    "content": json.dumps(model_payload, ensure_ascii=False),
                },
            }
        task = TaskEnvelope(
            task_id=f"react-{uuid4()}",
            ticket_id=str(request.ticket_id),
            goal="react_diagnosis",
            shared_context={
                "message": str(request.message),
                "service": str(request.service or arguments.get("service") or ""),
                "cluster": str(request.cluster),
                "namespace": str(request.namespace),
                "mock_scenario": str(request.mock_scenario or ""),
                "mock_scenarios": dict(request.mock_scenarios or {}),
                "mock_tool_responses": dict(request.mock_tool_responses or {}),
                **dict(extra_shared_context or {}),
            },
            allowed_actions=["run_tool"],
        )
        self._emit_tool_activity("tool.started", activity_context=activity_context, tool_name=tool_name)
        try:
            result = await self.tool_middleware.run(tool_name, task=task, arguments=arguments)
        except Exception as exc:
            self._emit_tool_activity(
                "tool.failed",
                activity_context=activity_context,
                tool_name=tool_name,
                payload={"error_type": exc.__class__.__name__},
            )
            raise
        payload = result.model_dump()
        self._emit_tool_activity(
            "tool.completed",
            activity_context=activity_context,
            tool_name=tool_name,
            payload={"status": payload.get("status"), "latency_ms": payload.get("latency_ms")},
        )
        tool_cache[cache_key] = payload
        model_payload = self._model_visible_tool_result(payload)
        return {
            "observation": {"tool_name": tool_name, "arguments": arguments, "result": payload},
            "tool_message": {
                "role": "tool",
                "tool_call_id": str(call.get("id") or f"{tool_name}-{uuid4().hex[:8]}"),
                "name": tool_name,
                "content": json.dumps(model_payload, ensure_ascii=False),
            },
        }

    async def _run_rule_based_loop(self, next_state: dict[str, Any]) -> Dict[str, Any]:
        incident_state = next_state["incident_state"]
        request = next_state["request"]
        activity_context = self._activity_context_from_state(next_state, request)
        context_snapshot = next_state.get("context_snapshot") or incident_state.context_snapshot
        hypotheses = self._build_rule_based_hypotheses(request=request, context_snapshot=context_snapshot)
        observations: list[dict[str, Any]] = []
        verification_results: list[VerificationResult] = []
        tool_cache: dict[str, Any] = {}

        for hypothesis in hypotheses:
            evidence_items: list[EvidenceItem] = []
            hypothesis_observations: list[dict[str, Any]] = []
            for step in hypothesis.verification_plan:
                result = await self._run_named_tool(
                    request=request,
                    tool_name=step.tool_name,
                    arguments=dict(step.params),
                    tool_cache=tool_cache,
                    extra_shared_context=self._build_tool_shared_context(
                        context_snapshot=context_snapshot,
                        incident_state=incident_state,
                    ),
                    activity_context=activity_context,
                )
                observation = result["observation"]
                observations.append(observation)
                hypothesis_observations.append(observation)
                envelope = dict(observation.get("result") or {})
                evidence_items.append(
                    EvidenceItem(
                        skill=step.tool_name,
                        purpose=step.purpose,
                        result=envelope,
                        matches_expected=self._matches_expected_signal(envelope),
                    )
                )
            verification_results.append(
                self._build_rule_based_verification_result(
                    hypothesis=hypothesis,
                    evidence_items=evidence_items,
                    observations=hypothesis_observations,
                )
            )

        ranked_result = self.ranker.rank(
            verification_results,
            similar_cases=list(context_snapshot.similar_cases or []) if context_snapshot is not None else [],
        )
        incident_state.hypotheses = hypotheses
        incident_state.verification_results = verification_results
        incident_state.ranked_result = ranked_result
        incident_state.metadata["selected_root_cause"] = (
            ranked_result.primary.root_cause if ranked_result.primary is not None else ""
        )
        incident_state.metadata["rule_based_fallback"] = True
        incident_state.metadata["react_observations"] = observations

        working_memory_summary = self._summarize_observations(observations)
        pinned_findings = self._extract_pinned_findings(observations)
        evidence_evaluation = self._evaluate_evidence(observations)
        next_state["expanded_domains_seen"] = []
        next_state["expansion_probe_count"] = 0
        next_state["expansion_probe_tools"] = []
        next_state["rejected_tool_call_count"] = 0
        next_state["rejected_tool_call_names"] = []
        next_state["observation_ledger"] = observations
        next_state["working_memory_summary"] = working_memory_summary
        next_state["pinned_findings"] = pinned_findings
        next_state["evidence_evaluation"] = evidence_evaluation
        next_state["tool_calls_used"] = len(observations)
        next_state["confidence"] = float(ranked_result.primary.confidence if ranked_result.primary is not None else 0.0)
        next_state["stop_reason"] = "rule_based_no_llm"
        react_runtime = self._build_react_runtime(next_state=next_state, observations=observations)
        incident_state.metadata["working_memory_summary"] = working_memory_summary
        incident_state.metadata["pinned_findings"] = pinned_findings
        incident_state.metadata["evidence_evaluation"] = evidence_evaluation
        incident_state.metadata["react_runtime"] = react_runtime

        if ranked_result.primary is not None:
            incident_state.final_summary = f"rule-based react fallback selected {ranked_result.primary.root_cause}"
            incident_state.final_message = self._build_rule_based_message(ranked_result.primary, observations)
            incident_state.approval_proposals = self.legacy_nodes._build_primary_approval_proposals(ranked_result)
            if incident_state.approval_proposals:
                incident_state.status = "ready_for_action"
                next_state["pending_node"] = None
                return next_state

        incident_state.status = "completed"
        confidence = float(next_state.get("confidence") or 0.0)
        user_report = self._build_user_diagnosis_report(
            request=request,
            observations=observations,
            confidence=confidence,
            stop_reason="rule_based_no_llm",
        )
        incident_state.final_message = user_report["message"]
        next_state["response"] = {
            "ticket_id": request.ticket_id,
            "status": "completed",
            "message": incident_state.final_message or "已完成基于规则的诊断。",
            "diagnosis": {
                **self.legacy_nodes._render_hypothesis_diagnosis(
                    route_decision=next_state.get("route_decision"),
                    incident_state=incident_state,
                    transition_notes=list(next_state.get("transition_notes") or []),
                    ranked_result=ranked_result,
                ),
                "display_mode": "user_report",
                "conclusion": user_report["root_cause"],
                "user_report": user_report,
                "recommended_actions": user_report["recommended_actions"],
                "approval_explanation": user_report["approval_explanation"],
                "route": "react_tool_first",
                "sources": list(incident_state.rag_context.citations if incident_state.rag_context is not None else []),
                "observations": observations,
                "evidence": user_report["evidence"],
                "raw_evidence": self._flatten_evidence(observations),
                "working_memory_summary": working_memory_summary,
                "pinned_findings": pinned_findings,
                "tool_calls_used": len(observations),
                "confidence": confidence,
                "stop_reason": "rule_based_no_llm",
                "evidence_evaluation": evidence_evaluation,
                "react_runtime": react_runtime,
            },
        }
        next_state["pending_node"] = "finalize"
        return next_state

    async def _run_named_tool(
        self,
        *,
        request,
        tool_name: str,
        arguments: dict[str, Any],
        tool_cache: dict[str, Any],
        extra_shared_context: dict[str, Any] | None = None,
        activity_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        call = {
            "id": f"rule-{tool_name}-{uuid4().hex[:8]}",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        }
        return await self._run_tool_call(
            request=request,
            call=call,
            tool_cache=tool_cache,
            extra_shared_context=extra_shared_context,
            activity_context=activity_context,
        )

    @staticmethod
    def _build_tool_shared_context(*, context_snapshot, incident_state) -> dict[str, Any]:
        shared: dict[str, Any] = {}
        rag_context = getattr(context_snapshot, "rag_context", None)
        if rag_context is None and incident_state is not None:
            rag_context = getattr(incident_state, "rag_context", None)
        if hasattr(rag_context, "model_dump"):
            shared["rag_context"] = rag_context.model_dump()
        elif isinstance(rag_context, dict):
            shared["rag_context"] = dict(rag_context)
        similar_cases = list(getattr(context_snapshot, "similar_cases", []) or []) if context_snapshot is not None else []
        if similar_cases:
            shared["similar_cases"] = [
                item.model_dump() if hasattr(item, "model_dump") else dict(item)
                for item in similar_cases[:6]
            ]
        case_recall = dict(getattr(context_snapshot, "case_recall", {}) or {}) if context_snapshot is not None else {}
        if case_recall:
            shared["case_recall"] = case_recall
        diagnosis_playbooks = list(getattr(context_snapshot, "diagnosis_playbooks", []) or []) if context_snapshot is not None else []
        if diagnosis_playbooks:
            shared["diagnosis_playbooks"] = [
                item.model_dump() if hasattr(item, "model_dump") else dict(item)
                for item in diagnosis_playbooks[:2]
            ]
        playbook_recall = dict(getattr(context_snapshot, "playbook_recall", {}) or {}) if context_snapshot is not None else {}
        if playbook_recall:
            shared["playbook_recall"] = playbook_recall
        return shared

    def _build_rule_based_hypotheses(self, *, request, context_snapshot) -> list[Hypothesis]:
        message = str(request.message or "")
        service = str(request.service or "")
        cluster = str(request.cluster or "")
        namespace = str(request.namespace or "")
        environment = str(request.environment or "")
        matched_domains = list(getattr(context_snapshot, "matched_tool_domains", []) or [])
        message_lower = message.lower()
        hypotheses: list[Hypothesis] = []
        for playbook in list(getattr(context_snapshot, "diagnosis_playbooks", []) or [])[:2]:
            verification_steps: list[VerificationStep] = []
            for step in list(getattr(playbook, "recommended_steps", []) or [])[:5]:
                if not isinstance(step, dict):
                    continue
                tool_name = str(step.get("tool_name") or step.get("tool") or "").strip()
                if not tool_name:
                    continue
                params = dict(step.get("params") or {}) if isinstance(step.get("params"), dict) else {}
                params.setdefault("service", service)
                if namespace:
                    params.setdefault("namespace", namespace)
                verification_steps.append(
                    VerificationStep(
                        tool_name=tool_name,
                        params=params,
                        purpose=str(step.get("purpose") or "按 Playbook 推荐顺序采集证据"),
                    )
                )
            if verification_steps:
                playbook_id = str(getattr(playbook, "playbook_id", "") or "playbook")
                title = str(getattr(playbook, "title", "") or playbook_id)
                hypotheses.append(
                    Hypothesis(
                        hypothesis_id=f"H-PB-{playbook_id[:24]}",
                        root_cause=f"按已验证 Playbook《{title}》指导的故障模式进行证据验证",
                        confidence_prior=max(0.62, min(0.86, float(getattr(playbook, "recall_score", 0.0) or 0.0))),
                        verification_plan=verification_steps,
                        expected_evidence="; ".join(list(getattr(playbook, "evidence_requirements", []) or [])[:4]) or "需要实时工具证据满足 Playbook 的证据要求。",
                        metadata={"source": "diagnosis_playbook", "playbook_id": playbook_id, "recall_reason": str(getattr(playbook, "recall_reason", "") or "")},
                    )
                )
        explicit_deploy_signal = any(token in message_lower for token in ("deploy", "release", "发布", "变更", "pipeline"))
        explicit_db_signal = any(token in message_lower for token in ("数据库", "db", "慢查询", "连接池", "deadlock"))
        explicit_network_signal = any(token in message_lower for token in ("502", "timeout", "超时", "ingress", "gateway", "依赖"))
        explicit_k8s_signal = any(token in message_lower for token in ("oom", "pod", "重启", "container", "oomkilled"))

        if any(token in message for token in ("低风险", "自动修复", "自动恢复")):
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="H-OBSERVE",
                    root_cause="当前更适合先执行低风险观测动作确认服务状态",
                    confidence_prior=0.7,
                    verification_plan=[
                        VerificationStep(
                            tool_name="check_service_health",
                            params={"service": service},
                            purpose="确认服务当前是否仍处于异常状态",
                        ),
                        VerificationStep(
                            tool_name="check_recent_alerts",
                            params={"service": service},
                            purpose="确认是否存在持续告警需要继续观察",
                        ),
                    ],
                    expected_evidence="服务有异常但暂不需要直接高风险干预。",
                    recommended_action="observe_service",
                    action_risk="low",
                    action_params={"service": service},
                )
            )
            return hypotheses

        if explicit_deploy_signal or ("cicd" in matched_domains and not explicit_k8s_signal and not explicit_network_signal and not explicit_db_signal):
            action = ""
            action_risk = "low"
            action_params: dict[str, Any] = {}
            if any(token in message for token in ("回滚", "deploy 失败", "发布失败", "最近变更")):
                action = "cicd.rollback_release"
                action_risk = "high"
                action_params = {
                    "service": service,
                    "environment": environment,
                    "cluster": cluster,
                    "namespace": namespace,
                    "reason": "rule_based_react_detected_recent_deploy_regression",
                }
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="H-CICD",
                    root_cause="近期发布或变更导致服务异常",
                    confidence_prior=0.84 if action else 0.76,
                    verification_plan=[
                        VerificationStep(
                            tool_name="check_service_health",
                            params={"service": service},
                            purpose="确认服务错误率、延迟和影响接口是否异常",
                        ),
                        VerificationStep(
                            tool_name="check_recent_deployments",
                            params={"service": service},
                            purpose="确认是否存在近期发布窗口",
                        ),
                        VerificationStep(
                            tool_name="get_deployment_status",
                            params={"service": service},
                            purpose="确认当前发布版本 rollout 是否退化",
                        ),
                        VerificationStep(
                            tool_name="check_pipeline_status",
                            params={"service": service},
                            purpose="确认部署流水线是否失败或抖动",
                        ),
                        VerificationStep(
                            tool_name="get_change_records",
                            params={"service": service},
                            purpose="确认最近 commit / 配置变更是否指向当前故障窗口",
                        ),
                        VerificationStep(
                            tool_name="get_rollback_history",
                            params={"service": service},
                            purpose="确认可回滚的上一稳定版本",
                        ),
                    ],
                    expected_evidence="最近存在部署、回滚或变更异常信号。",
                    recommended_action=action,
                    action_risk=action_risk,  # type: ignore[arg-type]
                    action_params=action_params,
                )
            )

        if explicit_network_signal or ("network" in matched_domains and not explicit_deploy_signal and not explicit_db_signal):
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="H-NET",
                    root_cause="网络链路或上游依赖抖动导致超时或 5xx",
                    confidence_prior=0.7,
                    verification_plan=[
                        VerificationStep(
                            tool_name="inspect_ingress_route",
                            params={"service": service},
                            purpose="确认 ingress / gateway 路由是否异常",
                        ),
                        VerificationStep(
                            tool_name="inspect_vpc_connectivity",
                            params={"service": service},
                            purpose="确认 VPC 链路是否阻塞",
                        ),
                        VerificationStep(
                            tool_name="inspect_upstream_dependency",
                            params={"service": service},
                            purpose="确认上游依赖是否退化或 timeout 升高",
                        ),
                    ],
                    expected_evidence="存在网络阻塞、路由异常或上游依赖退化。",
                )
            )

        if explicit_db_signal or ("db" in matched_domains and not explicit_deploy_signal and not explicit_k8s_signal and not explicit_network_signal):
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="H-DB",
                    root_cause="数据库连接池、慢查询或事务回滚导致依赖超时",
                    confidence_prior=0.68,
                    verification_plan=[
                        VerificationStep(
                            tool_name="inspect_db_instance_health",
                            params={"service": service},
                            purpose="确认数据库实例健康状态",
                        ),
                        VerificationStep(
                            tool_name="inspect_connection_pool",
                            params={"service": service},
                            purpose="确认连接池是否饱和",
                        ),
                        VerificationStep(
                            tool_name="inspect_slow_queries",
                            params={"service": service},
                            purpose="确认是否存在明显慢查询",
                        ),
                    ],
                    expected_evidence="出现连接池饱和或慢查询异常。",
                )
            )

        if explicit_k8s_signal or ("k8s" in matched_domains and not explicit_deploy_signal and not explicit_network_signal and not explicit_db_signal):
            action = ""
            action_risk = "low"
            action_params: dict[str, Any] = {}
            if any(token in message_lower for token in ("oom", "pod 重启", "oomkilled")):
                action = "restart_pods"
                action_risk = "high"
                action_params = {"service": service, "namespace": namespace}
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="H-K8S",
                    root_cause="Pod 健康异常或资源瓶颈导致服务不稳定",
                    confidence_prior=0.72,
                    verification_plan=[
                        VerificationStep(
                            tool_name="check_pod_status",
                            params={"service": service},
                            purpose="确认 Pod 状态是否异常",
                        ),
                        VerificationStep(
                            tool_name="inspect_pod_logs",
                            params={"service": service},
                            purpose="确认日志中是否存在明显异常模式",
                        ),
                        VerificationStep(
                            tool_name="inspect_pod_events",
                            params={"service": service},
                            purpose="确认事件中是否出现 OOMKilled 或重启",
                        ),
                    ],
                    expected_evidence="Pod 重启、OOM 或健康状态异常。",
                    recommended_action=action,
                    action_risk=action_risk,  # type: ignore[arg-type]
                    action_params=action_params,
                )
            )

        if not hypotheses:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="H-GENERIC",
                    root_cause="服务健康或近期告警异常导致当前问题",
                    confidence_prior=0.55,
                    verification_plan=[
                        VerificationStep(
                            tool_name="check_service_health",
                            params={"service": service},
                            purpose="确认服务健康状态",
                        ),
                        VerificationStep(
                            tool_name="check_recent_alerts",
                            params={"service": service},
                            purpose="确认近期告警是否持续出现",
                        ),
                    ],
                    expected_evidence="服务存在可观察的健康或告警异常。",
                )
            )

        return hypotheses[:3]

    def _build_rule_based_verification_result(
        self,
        *,
        hypothesis: Hypothesis,
        evidence_items: list[EvidenceItem],
        observations: list[dict[str, Any]],
    ) -> VerificationResult:
        strong_signals = 0
        evidence: list[str] = []
        checks_passed: list[str] = []
        checks_failed: list[str] = []
        for item in evidence_items:
            envelope = item.result
            status = str(envelope.get("status") or "")
            tool_evidence = self._derive_payload_evidence(item.skill, dict(envelope.get("payload") or {}))
            evidence.extend(entry for entry in tool_evidence if entry not in evidence)
            if self._matches_expected_signal(envelope):
                strong_signals += 1
                checks_passed.append(item.skill)
            elif status == "completed":
                checks_failed.append(item.skill)

        evidence_strength = min(1.0, max(len(evidence), strong_signals * 2) / 6)
        confidence = min(0.95, hypothesis.confidence_prior + strong_signals * 0.08)
        status = "passed" if strong_signals > 0 else "inconclusive"
        payload = {
            item.skill: item.result.get("payload") or {}
            for item in evidence_items
        }
        root_cause = self._derive_rule_based_root_cause(hypothesis=hypothesis, payload=payload)
        recommended_action, action_risk, action_params = self._derive_rule_based_action(
            hypothesis=hypothesis,
            payload=payload,
        )
        return VerificationResult(
            hypothesis_id=hypothesis.hypothesis_id,
            root_cause=root_cause,
            confidence=round(confidence, 3),
            evidence_strength=round(evidence_strength, 3),
            evidence_items=evidence_items,
            recommended_action=recommended_action,
            action_risk=action_risk,
            action_params=action_params,
            status=status,
            summary=f"已基于 {len(observations)} 个检查项完成规则诊断。",
            checks_passed=checks_passed[:5],
            checks_failed=checks_failed[:5],
            evidence=evidence[:8],
            payload=payload,
            metadata={"verification_mode": "rule_based_react_fallback", "react_rounds": 1},
        )

    def _derive_rule_based_root_cause(self, *, hypothesis: Hypothesis, payload: dict[str, Any]) -> str:
        if hypothesis.hypothesis_id == "H-CICD" and self._has_deploy_regression_evidence(payload):
            change = self._select_suspect_change(payload)
            recent = dict(payload.get("check_recent_deployments") or {})
            latest_revision = str(recent.get("latest_revision") or "当前发布版本").strip() or "当前发布版本"
            commit_id = str(change.get("commit_id") or change.get("change_id") or "").strip()
            change_summary = str(
                change.get("summary")
                or change.get("diff_summary")
                or change.get("title")
                or change.get("file")
                or "变更内容"
            ).strip()
            if commit_id:
                return f"近期发布 {latest_revision} 中的 commit {commit_id}（{change_summary}）与故障窗口重合，属于发布回归。"
            return f"近期发布 {latest_revision} 与故障窗口重合，属于发布或配置变更回归。"
        if hypothesis.hypothesis_id == "H-K8S":
            pod_logs = dict(payload.get("inspect_pod_logs") or {})
            pod_events = dict(payload.get("inspect_pod_events") or {})
            reason = str(pod_events.get("last_termination_reason") or "").lower()
            if reason == "oomkilled" or bool(pod_logs.get("oom_detected")):
                return "Pod 内存不足/OOMKilled 导致容器反复重启，服务可用副本不足。"
        return hypothesis.root_cause

    def _derive_rule_based_action(self, *, hypothesis: Hypothesis, payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        action = str(hypothesis.recommended_action or "")
        risk = str(hypothesis.action_risk or "low")
        params = dict(hypothesis.action_params or {})
        if action:
            return action, risk, params

        if hypothesis.hypothesis_id == "H-CICD" and self._has_deploy_regression_evidence(payload):
            recent = dict(payload.get("check_recent_deployments") or {})
            rollback = dict(payload.get("get_rollback_history") or {})
            change = self._select_suspect_change(payload)
            service = str(
                recent.get("service")
                or rollback.get("service")
                or dict(payload.get("get_change_records") or {}).get("service")
                or ""
            )
            target_revision = str(
                rollback.get("last_known_stable_revision")
                or recent.get("previous_revision")
                or "last-known-stable"
            )
            reason = "deploy_regression_detected_by_tools"
            commit_id = str(change.get("commit_id") or change.get("change_id") or "").strip()
            if commit_id:
                reason = f"deploy_regression_detected_by_tools:{commit_id}"
            return "cicd.rollback_release", "high", {
                "service": service,
                "environment": str(recent.get("environment") or ""),
                "cluster": str(recent.get("environment") or ""),
                "namespace": str(recent.get("namespace") or "default"),
                "target_revision": target_revision,
                "reason": reason,
            }

        if hypothesis.hypothesis_id == "H-K8S":
            pod_logs = dict(payload.get("inspect_pod_logs") or {})
            pod_events = dict(payload.get("inspect_pod_events") or {})
            pod_status = dict(payload.get("check_pod_status") or {})
            reason = str(pod_events.get("last_termination_reason") or "").lower()
            if reason == "oomkilled" or bool(pod_logs.get("oom_detected")):
                return "restart_pods", "high", {
                    "service": str(pod_status.get("service") or pod_logs.get("service") or ""),
                    "namespace": str(pod_status.get("namespace") or pod_logs.get("namespace") or "default"),
                }
        return action, risk, params

    @staticmethod
    def _select_suspect_change(payload: dict[str, Any]) -> dict[str, Any]:
        changes = [item for item in list(dict(payload.get("get_change_records") or {}).get("changes") or []) if isinstance(item, dict)]
        for change in changes:
            if bool(change.get("suspect") or change.get("correlates_with_incident") or change.get("is_suspect")):
                return dict(change)
        return dict(changes[0]) if changes else {}

    def _has_deploy_regression_evidence(self, payload: dict[str, Any]) -> bool:
        recent = dict(payload.get("check_recent_deployments") or {})
        deployment = dict(payload.get("get_deployment_status") or {})
        health = dict(payload.get("check_service_health") or {})
        change_payload = dict(payload.get("get_change_records") or {})
        changes = [item for item in list(change_payload.get("changes") or []) if isinstance(item, dict)]
        has_recent_deploy = bool(recent.get("has_recent_deploy")) or bool(changes)
        rollout_status = str(deployment.get("rollout_status") or "").lower()
        health_status = str(health.get("health_status") or "").lower()
        has_correlated_change = bool(self._select_suspect_change(payload)) or int(change_payload.get("change_count") or 0) > 0
        rollout_bad = rollout_status in {"degraded", "failed", "unhealthy", "partial", "mismatch"}
        health_bad = health_status in {"degraded", "unhealthy", "failed"}
        return bool(has_recent_deploy and (has_correlated_change or rollout_bad or health_bad))

    @staticmethod
    def _matches_expected_signal(envelope: dict[str, Any]) -> bool:
        payload = dict(envelope.get("payload") or {})
        current = dict(payload.get("current") or {})
        argocd = dict(payload.get("argocd") or {})
        parsed_error_counts = dict(payload.get("parsed_error_counts") or {})
        if float(current.get("http_5xx_rate_percent") or payload.get("error_rate_percent") or 0.0) > 1.0:
            return True
        if int(current.get("p99_latency_ms") or payload.get("p99_latency_ms") or 0) > 1000:
            return True
        if any(int(count or 0) > 0 for count in parsed_error_counts.values()):
            return True
        if str(argocd.get("health_status") or "").lower() in {"degraded", "failed", "unhealthy"}:
            return True
        if payload.get("oom_detected") is True:
            return True
        if payload.get("pool_state") == "saturated":
            return True
        if payload.get("dependency_status") == "degraded":
            return True
        if payload.get("connectivity_status") == "blocked":
            return True
        if payload.get("route_status") == "mismatch_or_unhealthy":
            return True
        if payload.get("lb_status") == "degraded":
            return True
        if payload.get("db_health") == "degraded":
            return True
        if str(payload.get("error_pattern") or "").lower() not in {"", "none"}:
            return True
        if int(payload.get("slow_query_count") or 0) > 0:
            return True
        if int(payload.get("deadlock_count") or 0) > 0:
            return True
        if float(payload.get("rollback_rate") or 0.0) > 0.05:
            return True
        if float(payload.get("timeout_ratio") or 0.0) > 0.05:
            return True
        if int(payload.get("ready_replicas") or 0) < int(payload.get("desired_replicas") or 0):
            return True
        if str(payload.get("last_termination_reason") or "").lower() not in {"", "none"}:
            return True
        for pod in list(payload.get("pods") or []):
            if not isinstance(pod, dict):
                continue
            if str(pod.get("status") or "").lower() not in {"", "running"}:
                return True
            if int(pod.get("restarts") or 0) > 0:
                return True
        return False

    def _build_rule_based_message(self, primary: VerificationResult, observations: list[dict[str, Any]]) -> str:
        evidence = self._flatten_evidence(observations)[:4]
        evidence_text = "；".join(evidence) if evidence else "当前还没有足够强的异常证据"
        return f"当前更接近的根因是：{primary.root_cause}。\n\n关键证据：{evidence_text}"
