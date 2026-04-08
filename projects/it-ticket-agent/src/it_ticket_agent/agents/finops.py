from __future__ import annotations

from ..settings import Settings
from ..tools.finops import (
    InspectBudgetGuardrailTool,
    InspectCommitmentCoverageTool,
    InspectCostAnomalyTool,
    InspectIdleResourceCandidatesTool,
)
from .local_tool_agent import LocalToolDomainAgent


class FinOpsAgent(LocalToolDomainAgent):
    name = "finops_agent"
    domain = "finops"
    system_prompt = (
        "你是企业内部的 FinOps Agent。"
        "你负责排查成本异常、预算护栏、闲置资源和承诺覆盖率问题。"
        "请优先通过本地工具获取事实，再输出 JSON。"
    )
    fallback_summary = "{service} 工单已进入 FinOps 诊断，建议先检查成本异常、预算护栏和闲置资源情况。"
    local_tool_note = "FinOps Agent 当前不连接外部系统，统一通过本地 tools 与 JSON mock/fallback 响应提供诊断事实。"
    domain_keywords = ("成本", "费用", "budget", "cost", "闲置", "coverage")

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            settings,
            tools=[
                InspectCostAnomalyTool(),
                InspectBudgetGuardrailTool(),
                InspectIdleResourceCandidatesTool(),
                InspectCommitmentCoverageTool(),
            ],
        )
