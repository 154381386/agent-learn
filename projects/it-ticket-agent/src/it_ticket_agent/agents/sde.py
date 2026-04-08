from __future__ import annotations

from ..settings import Settings
from ..tools.sde import (
    GetQuotaStatusTool,
    InspectClusterBootstrapTool,
    InspectMachineProvisioningTool,
    InvestigateResourceProvisioningTool,
)
from .local_tool_agent import LocalToolDomainAgent


class SDEAgent(LocalToolDomainAgent):
    name = "sde_agent"
    domain = "sde"
    system_prompt = (
        "你是企业内部的 SDE/平台资源 Agent。"
        "你负责排查资源开通失败、K8s 集群拉起失败、机器开通失败等问题。"
        "请优先通过本地工具获取事实，再输出 JSON。"
    )
    fallback_summary = "{service} 工单已进入 SDE/平台资源诊断，建议先核查资源申请、配额、集群拉起和机器开通状态。"
    local_tool_note = "SDE Agent 当前不连接外部系统，统一通过本地 tools 与 JSON mock/fallback 响应提供诊断事实。"
    domain_keywords = ("资源开通", "provision", "cluster", "集群", "机器开通", "ecs")

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            settings,
            tools=[
                InvestigateResourceProvisioningTool(),
                InspectClusterBootstrapTool(),
                InspectMachineProvisioningTool(),
                GetQuotaStatusTool(),
            ],
        )
