from __future__ import annotations

import json
import logging
from typing import Iterable, List

from ..runtime.contracts import FieldRequirement
from ..llm_client import OpenAICompatToolLLM
from ..runtime.contracts import AgentAction, AgentFinding, AgentResult, TaskEnvelope
from ..settings import Settings
from ..tools.contracts import BaseTool, ToolExecutionResult
from .base import BaseDomainAgent


logger = logging.getLogger(__name__)


class LocalToolDomainAgent(BaseDomainAgent):
    name: str
    domain: str
    system_prompt: str
    fallback_summary: str
    local_tool_note: str
    domain_keywords: tuple[str, ...] = ()
    required_fields = [
        FieldRequirement(name="service", type="string", description="需要排查的服务名", priority="critical"),
    ]

    def __init__(self, settings: Settings, tools: Iterable[BaseTool]) -> None:
        self.llm = OpenAICompatToolLLM(settings)
        self.tools = list(tools)

    async def diagnose(self, task: TaskEnvelope) -> AgentResult:
        if self.llm.enabled:
            try:
                logger.info("%s.run path=llm_loop ticket_id=%s", self.name, task.ticket_id)
                return await self._run_llm_loop(task)
            except Exception as exc:
                logger.warning("%s.run llm_loop_failed ticket_id=%s error=%s", self.name, task.ticket_id, exc)
        logger.info("%s.run path=fallback ticket_id=%s", self.name, task.ticket_id)
        return await self._run_fallback(task)

    async def _run_fallback(self, task: TaskEnvelope) -> AgentResult:
        shared = task.shared_context if isinstance(task.shared_context, dict) else {}
        message = str(shared.get("message") or "")
        service = str(shared.get("service") or "unknown-service")
        findings: List[AgentFinding] = []
        evidence: List[str] = []
        tool_results: list[ToolExecutionResult] = []

        for tool in self.tools:
            try:
                result = await self.run_tool_observed(tool, task)
            except Exception as exc:
                result = ToolExecutionResult(
                    tool_name=tool.name,
                    status="failed",
                    summary=f"工具调用失败：{tool.name}",
                    payload={"error": str(exc)},
                    evidence=[str(exc)],
                    risk="low",
                )
            tool_results.append(result)
            evidence.extend(result.evidence)

        if self.domain_keywords and any(keyword.lower() in message.lower() for keyword in self.domain_keywords):
            findings.append(
                AgentFinding(
                    title=f"检测到 {self.domain} 领域关键词",
                    detail=f"工单内容命中 {self.domain} 领域线索，已进入 {self.name} 诊断链路。",
                    severity="info",
                )
            )

        findings.append(
            AgentFinding(
                title="当前使用本地领域工具",
                detail=self.local_tool_note,
                severity="info",
            )
        )

        if not findings:
            findings.append(
                AgentFinding(
                    title="领域线索较弱",
                    detail=f"当前工单缺少明显的 {self.domain} 领域关键词，建议结合更多上下文继续排查。",
                    severity="info",
                )
            )

        return AgentResult(
            agent_name=self.name,
            domain=self.domain,
            status="completed",
            summary=self.fallback_summary.format(service=service),
            execution_path="fallback",
            findings=findings,
            evidence=evidence,
            tool_results=[item.model_dump() for item in tool_results],
            recommended_actions=[],
            risk_level="low",
            confidence=0.68,
            open_questions=[],
            needs_handoff=False,
            raw_refs=[],
        )

    async def _run_llm_loop(self, task: TaskEnvelope) -> AgentResult:
        shared = task.shared_context if isinstance(task.shared_context, dict) else {}
        message = str(shared.get("message") or "")
        service = str(shared.get("service") or "unknown-service")
        tools_by_name = {tool.name: tool for tool in self.tools}
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "message": message,
                        "service": service,
                        "goal": task.goal,
                        "execution_mode": "local_tools_only",
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        tool_results: list[ToolExecutionResult] = []
        for _ in range(4):
            assistant_message = await self.llm.chat(messages=messages, tools=[tool.as_openai_tool() for tool in self.tools])
            tool_calls = assistant_message.get("tool_calls") or []
            content = assistant_message.get("content") or ""
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            if tool_calls:
                for tool_call in tool_calls:
                    function = tool_call.get("function", {})
                    tool = tools_by_name.get(function.get("name"))
                    if tool is None:
                        continue
                    raw_arguments = function.get("arguments") or "{}"
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                    try:
                        tool_result = await self.run_tool_observed(tool, task, arguments=arguments)
                    except Exception as exc:
                        tool_result = ToolExecutionResult(
                            tool_name=tool.name,
                            status="failed",
                            summary=f"工具调用失败：{tool.name}",
                            payload={"error": str(exc)},
                            evidence=[str(exc)],
                            risk="low",
                        )
                    tool_results.append(tool_result)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "content": json.dumps(tool_result.model_dump(), ensure_ascii=False),
                        }
                    )
                continue

            payload = self.llm.extract_json(content)
            return self._build_agent_result_from_llm(task, payload, tool_results)

        raise ValueError("LLM tool loop exceeded max steps")

    def _build_agent_result_from_llm(
        self,
        task: TaskEnvelope,
        payload: dict,
        tool_results: list[ToolExecutionResult],
    ) -> AgentResult:
        service = str((task.shared_context or {}).get("service") or "unknown-service")
        findings = [
            AgentFinding(
                title=item.get("title", "未命名发现"),
                detail=item.get("detail", ""),
                severity=item.get("severity", "info"),
            )
            for item in payload.get("findings", [])
            if isinstance(item, dict)
        ]
        findings.append(
            AgentFinding(
                title="当前使用本地领域工具",
                detail=self.local_tool_note,
                severity="info",
            )
        )
        actions = [
            AgentAction(
                action=item.get("action", "unknown_action"),
                risk=item.get("risk", "low"),
                reason=item.get("reason", ""),
                params=item.get("params", {}),
            )
            for item in payload.get("recommended_actions", [])
            if isinstance(item, dict)
        ]
        evidence: list[str] = []
        for tool_result in tool_results:
            evidence.extend(tool_result.evidence)

        return AgentResult(
            agent_name=self.name,
            domain=self.domain,
            status="completed",
            summary=payload.get("summary", self.fallback_summary.format(service=service)),
            execution_path="llm_loop",
            findings=findings,
            evidence=evidence,
            tool_results=[item.model_dump() for item in tool_results],
            recommended_actions=actions,
            risk_level=payload.get("risk_level", "low"),
            confidence=float(payload.get("confidence", 0.65)),
            open_questions=payload.get("open_questions", []),
            needs_handoff=bool(payload.get("needs_handoff", False)),
            raw_refs=[],
        )
