from __future__ import annotations

from pathlib import Path

from ..runtime.contracts import TaskEnvelope
from .contracts import BaseTool, ToolExecutionResult
from .mock_helpers import build_context, match_any, resolve_profile_mock


DEFAULT_MOCK_PROFILES_PATH = Path(__file__).resolve().parents[3] / "data" / "mock_network_profiles.json"
ENV_VAR = "IT_TICKET_AGENT_MOCK_NETWORK_PROFILES_PATH"


class InspectDNSResolutionTool(BaseTool):
    name = "inspect_dns_resolution"
    summary = "Inspect DNS resolution and record consistency"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        resolution_status = "healthy"
        if match_any(ctx["message"], ["dns", "域名", "解析", "host not found"]):
            resolution_status = "degraded"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总 DNS 解析状态。",
            payload={"service": ctx["service"], "resolution_status": resolution_status},
            evidence=[f"dns={resolution_status}"],
        )


class InspectIngressRouteTool(BaseTool):
    name = "inspect_ingress_route"
    summary = "Inspect ingress, gateway, and route matching status"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        route_status = "healthy"
        if match_any(ctx["message"], ["ingress", "gateway", "路由", "404", "502"]):
            route_status = "mismatch_or_unhealthy"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总入口路由状态。",
            payload={"service": ctx["service"], "route_status": route_status},
            evidence=[f"route={route_status}"],
        )


class InspectVpcConnectivityTool(BaseTool):
    name = "inspect_vpc_connectivity"
    summary = "Inspect VPC, subnet, and east-west connectivity"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        connectivity_status = "healthy"
        if match_any(ctx["message"], ["vpc", "子网", "连通性", "network unreachable", "超时"]):
            connectivity_status = "blocked"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总 VPC 连通性状态。",
            payload={"service": ctx["service"], "connectivity_status": connectivity_status},
            evidence=[f"connectivity={connectivity_status}"],
        )


class InspectLoadBalancerStatusTool(BaseTool):
    name = "inspect_load_balancer_status"
    summary = "Inspect load balancer backend health and listener status"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        lb_status = "healthy"
        if match_any(ctx["message"], ["slb", "lb", "负载均衡", "backend", "listener"]):
            lb_status = "degraded"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总负载均衡状态。",
            payload={"service": ctx["service"], "lb_status": lb_status},
            evidence=[f"lb={lb_status}"],
        )


class InspectUpstreamDependencyTool(BaseTool):
    name = "inspect_upstream_dependency"
    summary = "Inspect upstream dependency health and timeout ratio"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        dependency_status = "healthy"
        timeout_ratio = 0.0
        if match_any(ctx["message"], ["upstream", "依赖", "timeout", "超时", "下游"]):
            dependency_status = "degraded"
            timeout_ratio = 0.23
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总上游依赖状态。",
            payload={"service": ctx["service"], "dependency_status": dependency_status, "timeout_ratio": timeout_ratio},
            evidence=[f"dependency={dependency_status}", f"timeout_ratio={timeout_ratio}"],
        )


class InspectEgressPolicyTool(BaseTool):
    name = "inspect_egress_policy"
    summary = "Inspect egress policy and outbound access restrictions"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        policy_status = "healthy"
        if match_any(ctx["message"], ["egress", "出口", "networkpolicy", "访问受限", "连不出去"]):
            policy_status = "blocked"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总出口网络策略状态。",
            payload={"service": ctx["service"], "policy_status": policy_status},
            evidence=[f"egress_policy={policy_status}"],
        )
