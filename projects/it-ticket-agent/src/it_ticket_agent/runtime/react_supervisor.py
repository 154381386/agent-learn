from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4
from typing import Any, Dict

from ..execution.tool_middleware import ToolExecutionMiddleware
from ..graph.nodes import OrchestratorGraphNodes
from ..graph.react_state import ReactTicketGraphState
from ..llm_client import OpenAICompatToolLLM
from ..runtime.contracts import TaskEnvelope
from ..settings import Settings
from ..skills.local_executor import LocalSkillExecutor


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
    ) -> None:
        self.legacy_nodes = legacy_nodes
        self.settings = settings
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.confidence_threshold = confidence_threshold
        self.llm = OpenAICompatToolLLM(settings)
        self.local_executor = LocalSkillExecutor(settings=settings)
        self.tools = self.local_executor.tools
        self.tool_middleware = ToolExecutionMiddleware(self.tools)

    async def run_loop(self, state: ReactTicketGraphState) -> Dict[str, Any]:
        next_state: dict[str, Any] = dict(state)
        context_updates = await self.legacy_nodes.context_collector(next_state)
        next_state.update(context_updates)
        next_state.setdefault("transition_notes", []).append("react supervisor completed context collection")

        if not self.llm.enabled:
            for step in (
                self.legacy_nodes.hypothesis_generator,
                self.legacy_nodes.parallel_verification,
                self.legacy_nodes.ranker,
            ):
                updates = await step(next_state)
                next_state.update(updates)
            next_state["pending_node"] = "approval_gate"
            next_state.setdefault("transition_notes", []).append(
                "llm is disabled, fallback to legacy hypothesis pipeline inside supervisor_loop"
            )
            return next_state

        incident_state = next_state["incident_state"]
        context_snapshot = next_state.get("context_snapshot") or incident_state.context_snapshot
        request = next_state["request"]
        messages = self._build_initial_messages(next_state)
        observations: list[dict[str, Any]] = []
        tool_calls_used = int(next_state.get("tool_calls_used") or 0)
        tool_cache: dict[str, Any] = dict(next_state.get("tool_cache") or {})

        for iteration in range(1, self.max_iterations + 1):
            next_state["iterations"] = iteration
            response = await self.llm.chat(messages, tools=[tool.as_openai_tool() for tool in self.tools.values()])
            tool_calls = response.get("tool_calls") if isinstance(response, dict) else None
            content = str(response.get("content") or "") if isinstance(response, dict) else ""
            next_state.setdefault("transition_notes", []).append(f"react iteration {iteration} completed")

            if not isinstance(tool_calls, list) or not tool_calls:
                parsed_answer = self._parse_final_answer(content)
                final_message = parsed_answer["final_answer"] or content.strip() or "已完成诊断，但模型未返回明确结论。"
                confidence = parsed_answer["confidence"]
                incident_state.status = "completed"
                incident_state.final_summary = "react supervisor completed tool-first reasoning loop"
                incident_state.final_message = final_message
                next_state["confidence"] = confidence
                next_state["stop_reason"] = "model_answered" if confidence >= self.confidence_threshold else "low_confidence"
                if confidence < self.confidence_threshold:
                    final_message = f"当前结论置信度仅为 {confidence:.2f}，建议继续补充线索或人工确认。\n\n{final_message}"
                    incident_state.final_message = final_message
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
                        "tool_calls_used": tool_calls_used,
                        "confidence": confidence,
                        "stop_reason": next_state.get("stop_reason"),
                        "incident_state": incident_state.model_dump(),
                        "graph": {"transition_notes": list(next_state.get("transition_notes") or [])},
                    },
                }
                next_state["pending_node"] = "finalize"
                return next_state

            batch_calls = tool_calls[: max(0, self.max_tool_calls - tool_calls_used)]
            valid_calls = []
            for call in batch_calls:
                function = call.get("function") or {}
                tool_name = str(function.get("name") or "")
                if tool_name in self.tools:
                    valid_calls.append(call)
            if not valid_calls:
                parsed_answer = self._parse_final_answer(content)
                final_message = parsed_answer["final_answer"] or content.strip() or "已完成诊断，但模型未返回明确结论。"
                confidence = parsed_answer["confidence"]
                incident_state.status = "completed"
                incident_state.final_summary = "react supervisor completed tool-first reasoning loop"
                incident_state.final_message = final_message
                next_state["confidence"] = confidence
                next_state["stop_reason"] = "model_answered" if confidence >= self.confidence_threshold else "low_confidence"
                if confidence < self.confidence_threshold:
                    final_message = f"当前结论置信度仅为 {confidence:.2f}，建议继续补充线索或人工确认。\n\n{final_message}"
                    incident_state.final_message = final_message
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
                        "tool_calls_used": tool_calls_used,
                        "confidence": confidence,
                        "stop_reason": next_state.get("stop_reason"),
                        "incident_state": incident_state.model_dump(),
                        "graph": {"transition_notes": list(next_state.get("transition_notes") or [])},
                    },
                }
                next_state["pending_node"] = "finalize"
                return next_state
            assistant_message = {"role": "assistant", "content": content or "", "tool_calls": valid_calls}
            messages.append(assistant_message)
            results = await asyncio.gather(*[self._run_tool_call(request=request, call=call, tool_cache=tool_cache) for call in valid_calls])
            tool_calls_used += len(batch_calls)
            next_state["tool_calls_used"] = tool_calls_used
            next_state["tool_cache"] = tool_cache
            observations.extend(result["observation"] for result in results)
            incident_state.metadata["react_observations"] = observations[-12:]
            for result in results:
                messages.append(result["tool_message"])
            if tool_calls_used >= self.max_tool_calls:
                next_state.setdefault("transition_notes", []).append("tool budget reached inside react supervisor")
                next_state["stop_reason"] = "tool_budget_reached"
                break

        next_state["stop_reason"] = next_state.get("stop_reason") or "iteration_guardrail_reached"
        incident_state.status = "completed"
        incident_state.final_summary = "react supervisor stopped by iteration or tool budget guardrail"
        incident_state.final_message = "已达到当前轮次或工具预算上限，请根据已收集证据决定是否继续。"
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
                "tool_calls_used": tool_calls_used,
                "confidence": next_state.get("confidence", 0.0),
                "stop_reason": next_state.get("stop_reason"),
                "incident_state": incident_state.model_dump(),
                "graph": {"transition_notes": list(next_state.get("transition_notes") or [])},
            },
        }
        next_state["pending_node"] = "finalize"
        return next_state

    def _build_initial_messages(self, state: ReactTicketGraphState) -> list[dict[str, Any]]:
        request = state["request"]
        incident_state = state["incident_state"]
        context_snapshot = state.get("context_snapshot") or incident_state.context_snapshot
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
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "request": request.model_dump(),
                        "rag_context": context_snapshot.rag_context.model_dump() if context_snapshot and context_snapshot.rag_context is not None else {},
                        "similar_cases": [item.model_dump() for item in list(context_snapshot.similar_cases or [])[:3]] if context_snapshot else [],
                        "available_tools": [name for name in self.tools.keys()],
                    },
                    ensure_ascii=False,
                ),
            },
        ]


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
        tool = self.tools[tool_name]
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
