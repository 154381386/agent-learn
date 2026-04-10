from __future__ import annotations

from typing import Any, Iterable
from uuid import uuid4

from ..runtime.contracts import TaskEnvelope
from ..state.incident_state import IncidentState
from ..state.models import ContextSnapshot, SkillResult
from ..tools.cicd import (
    CheckPipelineStatusTool,
    CheckPodStatusTool,
    CheckRecentAlertsTool,
    CheckRecentDeploymentsTool,
    CheckServiceHealthTool,
    GetChangeRecordsTool,
    GetDeploymentStatusTool,
    GetRollbackHistoryTool,
    InspectBuildFailureLogsTool,
)
from ..tools.contracts import BaseTool, ToolExecutionResult
from ..tools.db import (
    InspectConnectionPoolTool,
    InspectDBInstanceHealthTool,
    InspectReplicationStatusTool,
    InspectSlowQueriesTool,
)
from ..tools.network import (
    InspectDNSResolutionTool,
    InspectIngressRouteTool,
    InspectLoadBalancerStatusTool,
    InspectVpcConnectivityTool,
)
from ..tools.sde import GetQuotaStatusTool


class LocalSkillExecutor:
    def __init__(self) -> None:
        self.tools: dict[str, BaseTool] = {
            "check_recent_deployments": CheckRecentDeploymentsTool(),
            "check_pipeline_status": CheckPipelineStatusTool(),
            "get_deployment_status": GetDeploymentStatusTool(),
            "check_service_health": CheckServiceHealthTool(),
            "check_recent_alerts": CheckRecentAlertsTool(),
            "inspect_build_failure_logs": InspectBuildFailureLogsTool(),
            "get_rollback_history": GetRollbackHistoryTool(),
            "get_change_records": GetChangeRecordsTool(),
            "check_pod_status": CheckPodStatusTool(),
            "inspect_dns_resolution": InspectDNSResolutionTool(),
            "inspect_ingress_route": InspectIngressRouteTool(),
            "inspect_vpc_connectivity": InspectVpcConnectivityTool(),
            "inspect_load_balancer_status": InspectLoadBalancerStatusTool(),
            "inspect_db_instance_health": InspectDBInstanceHealthTool(),
            "inspect_replication_status": InspectReplicationStatusTool(),
            "inspect_slow_queries": InspectSlowQueriesTool(),
            "inspect_connection_pool": InspectConnectionPoolTool(),
            "get_quota_status": GetQuotaStatusTool(),
        }

    async def execute_skill(
        self,
        skill_name: str,
        *,
        params: dict[str, Any],
        context_snapshot: ContextSnapshot,
    ) -> SkillResult:
        task = self._build_task(context_snapshot, params)

        if skill_name == "check_recent_deploys":
            results = await self._run_many(
                task,
                [
                    ("check_recent_deployments", {"service": params.get("service")}),
                    ("get_change_records", {"service": params.get("service"), "limit": 3}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("signals", "changes"))

        if skill_name == "check_pipeline_status":
            results = await self._run_many(
                task,
                [
                    ("check_pipeline_status", {"service": params.get("project") or params.get("service")}),
                    ("inspect_build_failure_logs", {"service": params.get("project") or params.get("service")}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("pipeline_status", "failed_stage"))

        if skill_name == "check_pod_health":
            results = await self._run_many(
                task,
                [
                    ("check_pod_status", {"service": params.get("service"), "namespace": params.get("namespace")}),
                    ("check_service_health", {"service": params.get("service"), "environment": params.get("cluster")}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("pods", "health_status", "replica_status"))

        if skill_name == "check_memory_trend":
            results = await self._run_many(
                task,
                [
                    ("check_service_health", {"service": params.get("service"), "environment": params.get("cluster")}),
                    ("check_pod_status", {"service": params.get("service"), "namespace": params.get("namespace")}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("health_status", "pods"))

        if skill_name == "check_resource_limits":
            results = await self._run_many(
                task,
                [("get_quota_status", {"service": params.get("service")})],
            )
            return self._aggregate(skill_name, results, positive_keys=("quota_state",))

        if skill_name == "check_network_latency":
            results = await self._run_many(
                task,
                [
                    ("inspect_vpc_connectivity", {"service": params.get("service")}),
                    ("inspect_load_balancer_status", {"service": params.get("service")}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("connectivity_status", "lb_status"))

        if skill_name == "check_dns_resolution":
            results = await self._run_many(task, [("inspect_dns_resolution", {"service": params.get("domain") or params.get("service")})])
            return self._aggregate(skill_name, results, positive_keys=("resolution_status",))

        if skill_name == "check_ingress_rules":
            results = await self._run_many(
                task,
                [
                    ("inspect_ingress_route", {"service": params.get("service")}),
                    ("inspect_load_balancer_status", {"service": params.get("service")}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("route_status", "lb_status"))

        if skill_name == "check_db_health":
            results = await self._run_many(
                task,
                [
                    ("inspect_db_instance_health", {"service": params.get("service")}),
                    ("inspect_connection_pool", {"service": params.get("service")}),
                    ("inspect_slow_queries", {"service": params.get("service")}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("db_health", "pool_state", "slow_query_count"))

        if skill_name == "check_replication_lag":
            results = await self._run_many(task, [("inspect_replication_status", {"service": params.get("instance") or params.get("service")})])
            return self._aggregate(skill_name, results, positive_keys=("lag_seconds",))

        if skill_name == "check_log_errors":
            results = await self._run_many(
                task,
                [
                    ("inspect_build_failure_logs", {"service": params.get("service")}),
                    ("check_recent_alerts", {"service": params.get("service"), "window_minutes": 30}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("failed_stage", "alerts"))

        if skill_name == "check_alert_history":
            results = await self._run_many(task, [("check_recent_alerts", {"service": params.get("service"), "window_minutes": 30})])
            return self._aggregate(skill_name, results, positive_keys=("alerts", "highest_severity"))

        if skill_name == "check_slo_status":
            results = await self._run_many(
                task,
                [
                    ("check_service_health", {"service": params.get("service"), "environment": params.get("cluster")}),
                    ("check_recent_alerts", {"service": params.get("service"), "window_minutes": 30}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("health_status", "alert_count"))

        return SkillResult(
            skill_name=skill_name,
            status="error",
            summary=f"未实现的 skill: {skill_name}",
            evidence=[],
            payload={"params": dict(params)},
        )

    async def execute_action(
        self,
        action: str,
        *,
        params: dict[str, Any],
        incident_state: IncidentState | None = None,
    ) -> dict[str, Any]:
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

    async def _run_many(
        self,
        task: TaskEnvelope,
        calls: Iterable[tuple[str, dict[str, Any]]],
    ) -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        for tool_name, arguments in calls:
            tool = self.tools[tool_name]
            results.append(await tool.run(task, arguments))
        return results

    @staticmethod
    def _build_task(context_snapshot: ContextSnapshot, params: dict[str, Any]) -> TaskEnvelope:
        request = dict(context_snapshot.request or {})
        service = str(params.get("service") or request.get("service") or "")
        return TaskEnvelope(
            task_id=f"skill-{uuid4()}",
            ticket_id=str(request.get("ticket_id") or "skill-ticket"),
            goal="执行 skill",
            mode="pipeline",
            shared_context={
                "message": str(request.get("message") or ""),
                "service": service,
                "cluster": str(request.get("cluster") or "prod-shanghai-1"),
                "namespace": str(request.get("namespace") or "default"),
                "rag_context": context_snapshot.rag_context.model_dump() if context_snapshot.rag_context is not None else {},
            },
            upstream_findings=[],
            constraints={},
            priority="normal",
            allowed_actions=["run_skill"],
        )

    @staticmethod
    def _aggregate(
        skill_name: str,
        tool_results: list[ToolExecutionResult],
        *,
        positive_keys: tuple[str, ...],
    ) -> SkillResult:
        payload = {item.tool_name: item.payload for item in tool_results}
        evidence = [entry for item in tool_results for entry in item.evidence][:8]
        summary = "；".join(item.summary for item in tool_results[:2] if item.summary)
        status = "matched" if LocalSkillExecutor._has_positive_signal(tool_results, positive_keys) else "not_matched"
        return SkillResult(
            skill_name=skill_name,
            status=status,
            summary=summary or f"{skill_name} 已执行。",
            evidence=evidence,
            payload=payload,
        )

    @staticmethod
    def _has_positive_signal(tool_results: list[ToolExecutionResult], positive_keys: tuple[str, ...]) -> bool:
        for item in tool_results:
            for key in positive_keys:
                value = item.payload.get(key)
                if value in (None, "", 0, False, "healthy", "stable", "all_ready", "not_in_progress", "sufficient"):
                    continue
                return True
        return False
