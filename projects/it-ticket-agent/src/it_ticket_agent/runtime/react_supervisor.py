from __future__ import annotations

import asyncio
import json
import logging
import time
from uuid import uuid4
from typing import Any, Dict

from ..execution.tool_middleware import ToolExecutionMiddleware
from ..graph.nodes import OrchestratorGraphNodes
from ..orchestration.ranker import Ranker
from ..graph.react_state import ReactTicketGraphState
from ..llm_client import OpenAICompatToolLLM
from ..runtime.contracts import TaskEnvelope
from ..settings import Settings
from ..state.models import EvidenceItem, Hypothesis, VerificationResult, VerificationStep
from ..tools.runtime import LocalToolRuntime


logger = logging.getLogger(__name__)


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
        self.tool_runtime = LocalToolRuntime()
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

        for iteration in range(1, self.max_iterations + 1):
            next_state["iterations"] = iteration
            messages = self._build_iteration_messages(
                state=next_state,
                observations=observations,
                working_memory_summary=working_memory_summary,
                pinned_findings=pinned_findings,
            )
            response = await self.llm.chat(messages, tools=[tool.as_openai_tool() for tool in self.tools.values()])
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
            for call in batch_calls:
                function = call.get("function") or {}
                tool_name = str(function.get("name") or "")
                if tool_name in self.tools:
                    valid_calls.append(call)
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
            results = await asyncio.gather(*[self._run_tool_call(request=request, call=call, tool_cache=tool_cache) for call in valid_calls])
            tool_calls_used += len(valid_calls)
            next_state["tool_calls_used"] = tool_calls_used
            next_state["tool_cache"] = tool_cache
            observations.extend(result["observation"] for result in results)
            observations = observations[-20:]
            pinned_findings = self._extract_pinned_findings(observations)
            working_memory_summary = self._summarize_observations(observations)
            evidence_evaluation = self._evaluate_evidence(observations)
            next_state["observation_ledger"] = observations
            next_state["working_memory_summary"] = working_memory_summary
            next_state["pinned_findings"] = pinned_findings
            next_state["evidence_evaluation"] = evidence_evaluation
            incident_state.metadata["react_observations"] = observations
            incident_state.metadata["working_memory_summary"] = working_memory_summary
            incident_state.metadata["pinned_findings"] = pinned_findings
            incident_state.metadata["evidence_evaluation"] = evidence_evaluation

            if tool_calls_used >= self.max_tool_calls:
                next_state.setdefault("transition_notes", []).append("tool budget reached inside react supervisor")
                next_state["stop_reason"] = "tool_budget_reached"
                break

        evidence_evaluation = self._evaluate_evidence(observations)
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
        react_runtime = {
            "iterations": next_state.get("iterations", 0),
            "tool_calls_used": tool_calls_used,
            "stop_reason": next_state.get("stop_reason"),
            "working_memory_summary": working_memory_summary,
            "pinned_findings": pinned_findings,
            "evidence_evaluation": evidence_evaluation,
        }
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
        payload = {
            "request": request.model_dump(),
            "rag_context": context_snapshot.rag_context.model_dump() if context_snapshot and context_snapshot.rag_context is not None else {},
            "similar_cases": [item.model_dump() for item in list(context_snapshot.similar_cases or [])[:3]] if context_snapshot else [],
            "pinned_findings": pinned_findings,
            "working_memory_summary": working_memory_summary,
            "recent_observations": recent_observations,
            "available_tools": [name for name in self.tools.keys()],
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
                    "如果多个只读检查互不依赖，可以一次返回多个 tool calls。"
                    "当证据足够时，不要继续调 tool，直接输出 JSON：{\"final_answer\": string, \"confidence\": number}。"
                    "不要编造不存在的观测结果。"
                ),
            },
            {"role": "user", "content": content},
        ]

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
        react_runtime = {
            "iterations": next_state.get("iterations", 0),
            "tool_calls_used": next_state.get("tool_calls_used", 0),
            "stop_reason": next_state.get("stop_reason"),
            "working_memory_summary": str(next_state.get("working_memory_summary") or ""),
            "pinned_findings": list(next_state.get("pinned_findings") or []),
            "evidence_evaluation": self._evaluate_evidence(observations),
        }
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
        evidence_strength = min(1.0, unique_evidence_count / 6)
        enough_for_output = bool(unique_evidence_count >= 3)
        return {
            "observation_count": observation_count,
            "unique_evidence_count": unique_evidence_count,
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

    async def _run_tool_call(self, *, request, call: dict[str, Any], tool_cache: dict[str, Any]) -> dict[str, Any]:
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
        react_runtime = {
            "iterations": 1,
            "tool_calls_used": len(observations),
            "stop_reason": "rule_based_no_llm",
            "working_memory_summary": working_memory_summary,
            "pinned_findings": pinned_findings,
            "evidence_evaluation": evidence_evaluation,
        }
        incident_state.metadata["working_memory_summary"] = working_memory_summary
        incident_state.metadata["pinned_findings"] = pinned_findings
        incident_state.metadata["evidence_evaluation"] = evidence_evaluation
        incident_state.metadata["react_runtime"] = react_runtime
        next_state["observation_ledger"] = observations
        next_state["working_memory_summary"] = working_memory_summary
        next_state["pinned_findings"] = pinned_findings
        next_state["evidence_evaluation"] = evidence_evaluation
        next_state["tool_calls_used"] = len(observations)
        next_state["confidence"] = float(ranked_result.primary.confidence if ranked_result.primary is not None else 0.0)
        next_state["stop_reason"] = "rule_based_no_llm"

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
    ) -> dict[str, Any]:
        call = {
            "id": f"rule-{tool_name}-{uuid4().hex[:8]}",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        }
        return await self._run_tool_call(request=request, call=call, tool_cache=tool_cache)

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
