from __future__ import annotations

import asyncio
import json
import logging
import time
from uuid import uuid4
from typing import Any, Dict

from ..execution.tool_middleware import ToolExecutionMiddleware
from ..graph.nodes import OrchestratorGraphNodes
from ..graph.react_state import ReactTicketGraphState
from ..llm_client import OpenAICompatToolLLM
from ..runtime.contracts import TaskEnvelope
from ..settings import Settings
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

    async def run_loop(self, state: ReactTicketGraphState) -> Dict[str, Any]:
        next_state: dict[str, Any] = dict(state)
        context_updates = await self.legacy_nodes.context_collector(next_state)
        next_state.update(context_updates)
        next_state.setdefault("transition_notes", []).append("react supervisor completed context collection")

        if not self.llm.enabled:
            incident_state = next_state["incident_state"]
            incident_state.status = "failed"
            incident_state.final_summary = "react runtime requires llm configuration"
            next_state["response"] = {
                "ticket_id": str(next_state["request"].ticket_id),
                "status": "failed",
                "message": "当前已切换到 tool-first react 运行时，未配置 LLM 时不再回退到旧 skill 诊断链路。",
                "diagnosis": {
                    "summary": "react runtime requires llm configuration",
                    "stop_reason": "llm_disabled",
                    "evidence": [],
                },
            }
            next_state["pending_node"] = None
            next_state.setdefault("transition_notes", []).append("llm is disabled, react runtime stopped without legacy fallback")
            return next_state

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
