from __future__ import annotations

from pathlib import Path

from ..runtime.contracts import TaskEnvelope
from .contracts import BaseTool, ToolExecutionResult
from .mock_helpers import build_context, match_any, resolve_profile_mock


DEFAULT_MOCK_PROFILES_PATH = Path(__file__).resolve().parents[3] / "data" / "mock_sde_profiles.json"
ENV_VAR = "IT_TICKET_AGENT_MOCK_SDE_PROFILES_PATH"


class InvestigateResourceProvisioningTool(BaseTool):
    name = "investigate_resource_provisioning"
    summary = "Investigate why a resource provisioning request failed"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        message = ctx["message"]
        service = ctx["service"]
        status = "completed"
        failure_stage = "none"
        if match_any(message, ["资源开通", "provision", "申请失败", "quota"]):
            failure_stage = "approval_or_quota"
        return ToolExecutionResult(
            tool_name=self.name,
            status=status,
            summary="已生成资源开通调查摘要。",
            payload={"service": service, "failure_stage": failure_stage, "request_status": "needs_check"},
            evidence=[f"{service} 资源开通检查已完成"],
        )


class InspectClusterBootstrapTool(BaseTool):
    name = "inspect_cluster_bootstrap"
    summary = "Inspect Kubernetes cluster bootstrap failure signals"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        message = ctx["message"]
        bootstrap_status = "healthy"
        suspected_blocker = "none"
        if match_any(message, ["集群拉起", "bootstrap", "k8s", "control plane"]):
            bootstrap_status = "failed"
            suspected_blocker = "control_plane_or_cni"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总集群拉起状态。",
            payload={"service": ctx["service"], "bootstrap_status": bootstrap_status, "suspected_blocker": suspected_blocker},
            evidence=[f"cluster bootstrap={bootstrap_status}"],
        )


class InspectMachineProvisioningTool(BaseTool):
    name = "inspect_machine_provisioning"
    summary = "Inspect machine provisioning failure reason"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        reason = "none"
        if match_any(ctx["message"], ["机器开通", "主机开通", "instance", "ecs"]):
            reason = "image_or_inventory"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总机器开通结果。",
            payload={"service": ctx["service"], "provisioning_status": "needs_check", "failure_reason": reason},
            evidence=[f"machine provisioning reason={reason}"],
        )


class GetQuotaStatusTool(BaseTool):
    name = "get_quota_status"
    summary = "Check quota and capacity status for provisioning related resources"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        quota_state = "sufficient"
        if match_any(ctx["message"], ["quota", "配额", "资源不足", "容量不足"]):
            quota_state = "tight"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总配额与容量状态。",
            payload={"service": ctx["service"], "quota_state": quota_state},
            evidence=[f"quota={quota_state}"],
        )
