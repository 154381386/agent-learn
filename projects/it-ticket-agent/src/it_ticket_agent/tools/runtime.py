from __future__ import annotations

from uuid import uuid4

from ..state.incident_state import IncidentState
from .cicd import (
    CheckCanaryStatusTool,
    CheckPipelineStatusTool,
    CheckPodStatusTool,
    CheckRecentAlertsTool,
    CheckRecentDeploymentsTool,
    CheckServiceHealthTool,
    GetChangeRecordsTool,
    GetDeploymentStatusTool,
    GetRollbackHistoryTool,
    InspectBuildFailureLogsTool,
    InspectCpuSaturationTool,
    InspectErrorBudgetBurnTool,
    InspectJvmMemoryTool,
    InspectPodEventsTool,
    InspectPodLogsTool,
    InspectThreadPoolStatusTool,
)
from .contracts import BaseTool
from .db import (
    InspectConnectionPoolTool,
    InspectDBInstanceHealthTool,
    InspectDeadlockSignalsTool,
    InspectReplicationStatusTool,
    InspectSlowQueriesTool,
    InspectTransactionRollbackRateTool,
)
from .network import (
    InspectDNSResolutionTool,
    InspectEgressPolicyTool,
    InspectIngressRouteTool,
    InspectLoadBalancerStatusTool,
    InspectUpstreamDependencyTool,
    InspectVpcConnectivityTool,
)
from .sde import GetQuotaStatusTool


def build_default_tools() -> dict[str, BaseTool]:
    return {
        "check_recent_deployments": CheckRecentDeploymentsTool(),
        "check_pipeline_status": CheckPipelineStatusTool(),
        "get_deployment_status": GetDeploymentStatusTool(),
        "check_service_health": CheckServiceHealthTool(),
        "check_recent_alerts": CheckRecentAlertsTool(),
        "inspect_build_failure_logs": InspectBuildFailureLogsTool(),
        "get_rollback_history": GetRollbackHistoryTool(),
        "get_change_records": GetChangeRecordsTool(),
        "check_pod_status": CheckPodStatusTool(),
        "inspect_pod_logs": InspectPodLogsTool(),
        "inspect_pod_events": InspectPodEventsTool(),
        "inspect_jvm_memory": InspectJvmMemoryTool(),
        "inspect_cpu_saturation": InspectCpuSaturationTool(),
        "inspect_thread_pool_status": InspectThreadPoolStatusTool(),
        "inspect_error_budget_burn": InspectErrorBudgetBurnTool(),
        "check_canary_status": CheckCanaryStatusTool(),
        "inspect_dns_resolution": InspectDNSResolutionTool(),
        "inspect_ingress_route": InspectIngressRouteTool(),
        "inspect_vpc_connectivity": InspectVpcConnectivityTool(),
        "inspect_load_balancer_status": InspectLoadBalancerStatusTool(),
        "inspect_upstream_dependency": InspectUpstreamDependencyTool(),
        "inspect_egress_policy": InspectEgressPolicyTool(),
        "inspect_db_instance_health": InspectDBInstanceHealthTool(),
        "inspect_replication_status": InspectReplicationStatusTool(),
        "inspect_slow_queries": InspectSlowQueriesTool(),
        "inspect_connection_pool": InspectConnectionPoolTool(),
        "inspect_deadlock_signals": InspectDeadlockSignalsTool(),
        "inspect_transaction_rollback_rate": InspectTransactionRollbackRateTool(),
        "get_quota_status": GetQuotaStatusTool(),
    }


class LocalToolRuntime:
    def __init__(self) -> None:
        self.tools = build_default_tools()

    async def execute_action(
        self,
        action: str,
        *,
        params: dict[str, object],
        incident_state: IncidentState | None = None,
    ) -> dict[str, object]:
        service = str(params.get("service") or params.get("target") or (incident_state.service if incident_state else "") or "service")
        if action in {"rollback_deploy", "cicd.rollback_release", "cicd.rollback_service"}:
            return {
                "ticket_id": incident_state.ticket_id if incident_state is not None else "",
                "status": "completed",
                "message": f"审批已通过；已为 {service} 执行回滚动作。",
                "diagnosis": {
                    "execution": {
                        "status": "completed",
                        "action": action,
                        "service": service,
                        "target_revision": params.get("version") or "last-known-stable",
                    }
                },
                "structuredContent": {"status": "completed", "action": action, "service": service, "job_id": f"local-{uuid4().hex[:8]}"},
                "content": [{"text": f"{service} 的回滚动作已完成。"}],
            }
        if action == "restart_pods":
            return {
                "ticket_id": incident_state.ticket_id if incident_state is not None else "",
                "status": "completed",
                "message": f"审批已通过；已为 {service} 执行 Pod 重启。",
                "diagnosis": {"execution": {"status": "completed", "action": action, "service": service}},
                "structuredContent": {"status": "completed", "action": action, "service": service},
                "content": [{"text": f"{service} Pod 重启动作已完成。"}],
            }
        if action == "scale_replicas":
            count = params.get("count", 2)
            return {
                "ticket_id": incident_state.ticket_id if incident_state is not None else "",
                "status": "completed",
                "message": f"审批已通过；已将 {service} 副本调整到 {count}。",
                "diagnosis": {"execution": {"status": "completed", "action": action, "service": service, "count": count}},
                "structuredContent": {"status": "completed", "action": action, "service": service, "count": count},
                "content": [{"text": f"{service} 扩缩容已完成。"}],
            }
        if action == "observe_service":
            return {
                "ticket_id": incident_state.ticket_id if incident_state is not None else "",
                "status": "completed",
                "message": f"审批已通过；已记录对 {service} 的低风险观测动作。",
                "diagnosis": {"execution": {"status": "completed", "action": action, "service": service}},
                "structuredContent": {"status": "completed", "action": action, "service": service},
                "content": [{"text": f"{service} 的观测动作已完成。"}],
            }
        raise RuntimeError(f"unsupported local action skill: {action}")
