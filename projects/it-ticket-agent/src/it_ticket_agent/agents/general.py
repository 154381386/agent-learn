from __future__ import annotations

from ..runtime.contracts import AgentFinding, AgentResult, TaskEnvelope
from .base import BaseDomainAgent


class GeneralSREAgent(BaseDomainAgent):
    name = "general_sre_agent"
    domain = "general"

    async def run(self, task: TaskEnvelope) -> AgentResult:
        execution_context = task.shared_context.get("execution_context") or {}
        request_context = execution_context.get("request_context") or {}
        message = task.shared_context.get("message") or request_context.get("message", "")
        service = task.shared_context.get("service") or request_context.get("service", "unknown-service")
        return AgentResult(
            agent_name=self.name,
            domain=self.domain,
            status="completed",
            summary=f"{service} 工单进入通用 SRE 诊断路径，当前建议先做基础现象确认与上下文补齐。",
            execution_path="general_direct",
            findings=[
                AgentFinding(
                    title="进入通用兜底诊断",
                    detail=f"当前工单未命中特定领域路由规则，原始问题为：{message}",
                    severity="info",
                )
            ],
            evidence=["未命中特定领域关键词，走 general fallback"],
            tool_results=[],
            recommended_actions=[],
            risk_level="low",
            confidence=0.45,
            open_questions=["是否有明确的最近变更、告警或影响范围信息？"],
            needs_handoff=False,
            raw_refs=[],
        )
