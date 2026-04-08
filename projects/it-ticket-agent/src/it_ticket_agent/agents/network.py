from __future__ import annotations

from ..settings import Settings
from ..tools.network import (
    InspectDNSResolutionTool,
    InspectIngressRouteTool,
    InspectLoadBalancerStatusTool,
    InspectVpcConnectivityTool,
)
from .local_tool_agent import LocalToolDomainAgent


class NetworkAgent(LocalToolDomainAgent):
    name = "network_agent"
    domain = "network"
    system_prompt = (
        "你是企业内部的 Network Agent。"
        "你负责排查 DNS、Ingress、VPC 连通性、负载均衡等网络问题。"
        "请优先通过本地工具获取事实，再输出 JSON。"
    )
    fallback_summary = "{service} 工单已进入网络诊断，建议先检查 DNS、入口路由、VPC 连通性和负载均衡状态。"
    local_tool_note = "Network Agent 当前不连接外部系统，统一通过本地 tools 与 JSON mock/fallback 响应提供诊断事实。"
    domain_keywords = ("dns", "域名", "ingress", "vpc", "网络", "route", "lb")

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            settings,
            tools=[
                InspectDNSResolutionTool(),
                InspectIngressRouteTool(),
                InspectVpcConnectivityTool(),
                InspectLoadBalancerStatusTool(),
            ],
        )
