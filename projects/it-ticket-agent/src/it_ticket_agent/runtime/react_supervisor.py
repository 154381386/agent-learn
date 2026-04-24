from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4
from typing import Any, Dict

from ..execution.tool_middleware import ToolExecutionMiddleware
from ..graph.nodes import OrchestratorGraphNodes
from ..orchestration.ranker import Ranker
from ..graph.react_state import ReactTicketGraphState
from ..llm_client import OpenAICompatToolLLM
from ..runtime.contracts import TaskEnvelope
from ..settings import Settings
from ..state.models import EvidenceItem, Hypothesis, RAGContextBundle, SimilarIncidentCase, VerificationResult, VerificationStep
from ..tools.runtime import LocalToolRuntime


logger = logging.getLogger(__name__)


DOMAIN_TOOL_GROUPS: dict[str, list[str]] = {
    "cicd": [
        "check_recent_deployments",
        "check_pipeline_status",
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

    async def run_loop(self, state: ReactTicketGraphState) -> Dict[str, Any]:
        next_state: dict[str, Any] = dict(state)
        context_updates = await self.legacy_nodes.context_collector(next_state)
        next_state.update(context_updates)
        next_state.setdefault("transition_notes", []).append("react supervisor completed context collection")

        if not self.llm.enabled:
            next_state.setdefault("transition_notes", []).append("llm disabled, using rule-based react fallback")
            return await self._run_rule_based_loop(next_state)

        incident_state = next_state["incident_state"]
        context_snapshot = next_state.get("context_snapshot") or incident_state.context_snapshot
        request = next_state["request"]
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
        incident_state.final_summary = "react supervisor stopped by iteration or tool budget guardrail"
        if evidence_evaluation.get("enough_for_output"):
            incident_state.final_message = (
                "已达到当前轮次或工具预算上限，但当前证据已相对充分，可先基于以下事实做判断：\n\n"
                + (working_memory_summary or "；".join(self._flatten_evidence(observations)[:4]))
            )
        else:
            incident_state.final_message = "已达到当前轮次或工具预算上限，请根据已收集证据决定是否继续。"
        react_runtime = self._build_react_runtime(next_state=next_state, observations=observations)
        incident_state.metadata["react_runtime"] = react_runtime
        next_state["response"] = {
            "ticket_id": request.ticket_id,
            "status": "completed",
            "message": incident_state.final_message,
            "diagnosis": {
                "summary": incident_state.final_summary,
                "conclusion": incident_state.final_message,
                "route": "react_tool_first",
                "sources": list(incident_state.rag_context.citations if incident_state.rag_context is not None else []),
                "context_snapshot": context_snapshot.model_dump() if context_snapshot is not None else None,
                "observations": observations,
                "evidence": self._flatten_evidence(observations),
                "working_memory_summary": working_memory_summary,
                "pinned_findings": pinned_findings,
                "tool_calls_used": tool_calls_used,
                "confidence": next_state.get("confidence", 0.0),
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
        payload = {
            "request": request.model_dump(),
            "rag_context": context_snapshot.rag_context.model_dump() if context_snapshot and context_snapshot.rag_context is not None else {},
            "similar_cases": [item.model_dump() for item in list(context_snapshot.similar_cases or [])[:3]] if context_snapshot else [],
            "case_recall": dict(getattr(context_snapshot, "case_recall", {}) or {}) if context_snapshot else {},
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
            payload["pinned_findings"] = pinned_findings[:6]
            content = json.dumps(payload, ensure_ascii=False)
        if len(content) > self.max_context_tokens:
            payload["recent_observations"] = recent_observations[-1:]
            payload["working_memory_summary"] = working_memory_summary[: max(300, self.max_context_tokens // 3)]
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
                    "similar_cases 只是一层历史背景提示，不等于现场证据。"
                    "只有在 service/环境已明确，且你已经拿到更具体的 symptom、failure mode 或根因方向时，才调用 search_similar_incidents。"
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
        incident_state.final_summary = "react supervisor completed tool-first reasoning loop"
        incident_state.final_message = final_message
        next_state["confidence"] = confidence
        if confidence >= self.confidence_threshold:
            next_state["stop_reason"] = "model_answered"
        elif evidence_evaluation.get("enough_for_output"):
            next_state["stop_reason"] = "evidence_sufficient_low_model_confidence"
            final_message = f"模型置信度仅为 {confidence:.2f}，但当前证据已相对充分，先给出阶段性结论。\n\n{final_message}"
            incident_state.final_message = final_message
        else:
            next_state["stop_reason"] = "low_confidence"
            final_message = f"当前结论置信度仅为 {confidence:.2f}，建议继续补充线索或人工确认。\n\n{final_message}"
            incident_state.final_message = final_message
        react_runtime = self._build_react_runtime(next_state=next_state, observations=observations)
        incident_state.metadata["react_runtime"] = react_runtime
        next_state["response"] = {
            "ticket_id": request.ticket_id,
            "status": "completed",
            "message": final_message,
            "diagnosis": {
                "summary": incident_state.final_summary,
                "conclusion": final_message,
                "route": "react_tool_first",
                "sources": list(incident_state.rag_context.citations if incident_state.rag_context is not None else []),
                "context_snapshot": context_snapshot.model_dump() if context_snapshot is not None else None,
                "observations": observations,
                "evidence": self._flatten_evidence(observations),
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
    def _recent_observations_for_prompt(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for item in observations[-3:]:
            result = dict(item.get("result") or {})
            compact.append(
                {
                    "tool_name": item.get("tool_name"),
                    "arguments": item.get("arguments") or {},
                    "status": result.get("status"),
                    "summary": result.get("summary"),
                    "evidence": list(result.get("evidence") or [])[:3],
                }
            )
        return compact

    def _summarize_observations(self, observations: list[dict[str, Any]]) -> str:
        if len(observations) <= self.summary_after_n_steps:
            return ""
        summary_lines: list[str] = []
        for item in observations[:-2]:
            result = dict(item.get("result") or {})
            tool_name = str(item.get("tool_name") or "")
            summary = str(result.get("summary") or "")
            evidence = ", ".join(str(entry) for entry in list(result.get("evidence") or [])[:2])
            line = f"{tool_name}: {summary}".strip()
            if evidence:
                line = f"{line} | evidence={evidence}"
            if line and line not in summary_lines:
                summary_lines.append(line)
        return "\n".join(summary_lines[:8])

    @staticmethod
    def _extract_pinned_findings(observations: list[dict[str, Any]]) -> list[str]:
        pinned: list[str] = []
        for item in observations:
            result = dict(item.get("result") or {})
            for entry in list(result.get("evidence") or []):
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
            result = dict(item.get("result") or {})
            for entry in list(result.get("evidence") or []):
                text = str(entry).strip()
                if text and text not in evidence:
                    evidence.append(text)
                if len(evidence) >= 8:
                    return evidence
        return evidence

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
        if any(token in message_lower for token in ("deploy", "release", "发布", "变更", "pipeline", "回滚")):
            explicit_domains.append("cicd")
        if any(token in message_lower for token in ("oom", "pod", "重启", "container", "oomkilled")):
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
        evidence = [str(entry).lower() for entry in list(result.get("evidence") or [])]
        if any(
            token in entry
            for entry in evidence
            for token in ("degraded", "blocked", "saturated", "oom", "failed", "error", "unhealthy", "timeout")
        ):
            return True
        if payload.get("oom_detected") is True:
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

    async def _run_tool_call(
        self,
        *,
        request,
        call: dict[str, Any],
        tool_cache: dict[str, Any],
        extra_shared_context: dict[str, Any] | None = None,
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
            return {
                "observation": {"tool_name": tool_name, "arguments": arguments, "result": payload, "cached": True},
                "tool_message": {
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or f"{tool_name}-{uuid4().hex[:8]}"),
                    "name": tool_name,
                    "content": json.dumps(payload, ensure_ascii=False),
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
                "mock_world_state": dict(request.mock_world_state or {}),
                **dict(extra_shared_context or {}),
            },
            allowed_actions=["run_tool"],
        )
        result = await self.tool_middleware.run(tool_name, task=task, arguments=arguments)
        payload = result.model_dump()
        tool_cache[cache_key] = payload
        return {
            "observation": {"tool_name": tool_name, "arguments": arguments, "result": payload},
            "tool_message": {
                "role": "tool",
                "tool_call_id": str(call.get("id") or f"{tool_name}-{uuid4().hex[:8]}"),
                "name": tool_name,
                "content": json.dumps(payload, ensure_ascii=False),
            },
        }

    async def _run_rule_based_loop(self, next_state: dict[str, Any]) -> Dict[str, Any]:
        incident_state = next_state["incident_state"]
        request = next_state["request"]
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
                "conclusion": incident_state.final_message or "",
                "route": "react_tool_first",
                "sources": list(incident_state.rag_context.citations if incident_state.rag_context is not None else []),
                "observations": observations,
                "evidence": self._flatten_evidence(observations),
                "working_memory_summary": working_memory_summary,
                "pinned_findings": pinned_findings,
                "tool_calls_used": len(observations),
                "confidence": float(next_state.get("confidence") or 0.0),
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

        if explicit_deploy_signal or "cicd" in matched_domains:
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
                            tool_name="check_recent_deployments",
                            params={"service": service},
                            purpose="确认是否存在近期发布窗口",
                        ),
                        VerificationStep(
                            tool_name="check_pipeline_status",
                            params={"service": service},
                            purpose="确认部署流水线是否失败或抖动",
                        ),
                        VerificationStep(
                            tool_name="get_change_records",
                            params={"service": service},
                            purpose="确认最近变更记录是否指向当前故障窗口",
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

        if explicit_db_signal or ("db" in matched_domains and not explicit_deploy_signal):
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
            summary = str(envelope.get("summary") or "")
            tool_evidence = [str(entry) for entry in list(envelope.get("evidence") or []) if entry]
            evidence.extend(entry for entry in tool_evidence if entry not in evidence)
            if self._matches_expected_signal(envelope):
                strong_signals += 1
                checks_passed.append(summary or item.skill)
            elif status == "completed":
                checks_failed.append(summary or item.skill)

        evidence_strength = min(1.0, max(len(evidence), strong_signals * 2) / 6)
        confidence = min(0.95, hypothesis.confidence_prior + strong_signals * 0.08)
        status = "passed" if strong_signals > 0 else "inconclusive"
        payload = {
            item.skill: item.result.get("payload") or {}
            for item in evidence_items
        }
        return VerificationResult(
            hypothesis_id=hypothesis.hypothesis_id,
            root_cause=hypothesis.root_cause,
            confidence=round(confidence, 3),
            evidence_strength=round(evidence_strength, 3),
            evidence_items=evidence_items,
            recommended_action=hypothesis.recommended_action,
            action_risk=hypothesis.action_risk,
            action_params=dict(hypothesis.action_params),
            status=status,
            summary=f"已基于 {len(observations)} 个检查项完成规则诊断。",
            checks_passed=checks_passed[:5],
            checks_failed=checks_failed[:5],
            evidence=evidence[:8],
            payload=payload,
            metadata={"verification_mode": "rule_based_react_fallback", "react_rounds": 1},
        )

    @staticmethod
    def _matches_expected_signal(envelope: dict[str, Any]) -> bool:
        payload = dict(envelope.get("payload") or {})
        evidence = [str(entry).lower() for entry in list(envelope.get("evidence") or [])]
        signal_terms = ("degraded", "blocked", "saturated", "mismatch_or_unhealthy", "oom", "failed", "error", "unhealthy")
        if any(any(term in entry for term in signal_terms) for entry in evidence):
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
