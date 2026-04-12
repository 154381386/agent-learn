from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from ..llm_client import OpenAICompatToolLLM
from ..runtime.contracts import TaskEnvelope
from ..settings import Settings
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
    InspectCpuSaturationTool,
    InspectErrorBudgetBurnTool,
    InspectJvmMemoryTool,
    InspectPodEventsTool,
    InspectPodLogsTool,
    InspectThreadPoolStatusTool,
)
from ..tools.contracts import BaseTool, ToolExecutionResult
from ..tools.db import (
    InspectConnectionPoolTool,
    InspectDBInstanceHealthTool,
    InspectDeadlockSignalsTool,
    InspectReplicationStatusTool,
    InspectTransactionRollbackRateTool,
    InspectSlowQueriesTool,
)
from ..tools.network import (
    InspectDNSResolutionTool,
    InspectEgressPolicyTool,
    InspectIngressRouteTool,
    InspectLoadBalancerStatusTool,
    InspectUpstreamDependencyTool,
    InspectVpcConnectivityTool,
)
from ..tools.sde import GetQuotaStatusTool
from .registry import SkillRegistry


ACTION_SKILLS = {"restart_pods", "scale_replicas", "rollback_deploy"}

DEFAULT_SKILL_SPECS: dict[str, dict[str, object]] = {
    "check_recent_deploys": {
        "tool_names": ["check_recent_deployments", "get_change_records"],
        "fallback_calls": [
            ("check_recent_deployments", {"service": "${service}"}),
            ("get_change_records", {"service": "${service}", "limit": 3}),
        ],
        "positive_keys": ("signals", "changes"),
    },
    "check_pipeline_status": {
        "tool_names": ["check_pipeline_status", "inspect_build_failure_logs"],
        "fallback_calls": [
            ("check_pipeline_status", {"service": "${project_or_service}"}),
            ("inspect_build_failure_logs", {"service": "${project_or_service}"}),
        ],
        "positive_keys": ("pipeline_status", "failed_stage"),
    },
    "diagnose_pod_crash": {
        "tool_names": ["check_pod_status", "inspect_pod_events", "inspect_pod_logs", "inspect_jvm_memory", "inspect_cpu_saturation", "get_quota_status", "check_service_health"],
        "fallback_calls": [
            ("check_pod_status", {"service": "${service}", "namespace": "${namespace}"}),
            ("inspect_pod_events", {"service": "${service}", "namespace": "${namespace}"}),
            ("inspect_pod_logs", {"service": "${service}", "namespace": "${namespace}"}),
            ("inspect_jvm_memory", {"service": "${service}"}),
            ("inspect_cpu_saturation", {"service": "${service}"}),
            ("get_quota_status", {"service": "${service}"}),
            ("check_service_health", {"service": "${service}", "environment": "${cluster}"}),
        ],
        "positive_keys": ("pods", "health_status", "replica_status", "last_termination_reason", "oom_detected", "error_pattern", "heap_usage_ratio", "quota_state"),
    },
    "check_pod_health": {
        "tool_names": ["check_pod_status", "inspect_pod_events", "inspect_pod_logs", "check_service_health"],
        "fallback_calls": [
            ("check_pod_status", {"service": "${service}", "namespace": "${namespace}"}),
            ("inspect_pod_events", {"service": "${service}", "namespace": "${namespace}"}),
            ("inspect_pod_logs", {"service": "${service}", "namespace": "${namespace}"}),
            ("check_service_health", {"service": "${service}", "environment": "${cluster}"}),
        ],
        "positive_keys": ("pods", "health_status", "replica_status", "last_termination_reason", "oom_detected"),
    },
    "check_memory_trend": {
        "tool_names": ["check_service_health", "check_pod_status", "inspect_pod_events", "inspect_pod_logs", "inspect_jvm_memory", "inspect_cpu_saturation"],
        "fallback_calls": [
            ("check_service_health", {"service": "${service}", "environment": "${cluster}"}),
            ("check_pod_status", {"service": "${service}", "namespace": "${namespace}"}),
            ("inspect_pod_events", {"service": "${service}", "namespace": "${namespace}"}),
            ("inspect_pod_logs", {"service": "${service}", "namespace": "${namespace}"}),
            ("inspect_jvm_memory", {"service": "${service}"}),
            ("inspect_cpu_saturation", {"service": "${service}"}),
        ],
        "positive_keys": ("health_status", "pods", "last_termination_reason", "oom_detected", "heap_usage_ratio", "gc_pressure"),
    },
    "check_resource_limits": {
        "tool_names": ["get_quota_status"],
        "fallback_calls": [("get_quota_status", {"service": "${service}"})],
        "positive_keys": ("quota_state",),
    },
    "check_network_latency": {
        "tool_names": ["inspect_vpc_connectivity", "inspect_load_balancer_status", "inspect_upstream_dependency", "inspect_egress_policy"],
        "fallback_calls": [
            ("inspect_vpc_connectivity", {"service": "${service}"}),
            ("inspect_load_balancer_status", {"service": "${service}"}),
            ("inspect_upstream_dependency", {"service": "${service}"}),
            ("inspect_egress_policy", {"service": "${service}"}),
        ],
        "positive_keys": ("connectivity_status", "lb_status", "dependency_status", "policy_status"),
    },
    "check_dns_resolution": {
        "tool_names": ["inspect_dns_resolution"],
        "fallback_calls": [("inspect_dns_resolution", {"service": "${domain_or_service}"})],
        "positive_keys": ("resolution_status",),
    },
    "check_ingress_rules": {
        "tool_names": ["inspect_ingress_route", "inspect_load_balancer_status"],
        "fallback_calls": [
            ("inspect_ingress_route", {"service": "${service}"}),
            ("inspect_load_balancer_status", {"service": "${service}"}),
        ],
        "positive_keys": ("route_status", "lb_status"),
    },
    "check_db_health": {
        "tool_names": ["inspect_db_instance_health", "inspect_connection_pool", "inspect_slow_queries", "inspect_deadlock_signals", "inspect_transaction_rollback_rate"],
        "fallback_calls": [
            ("inspect_db_instance_health", {"service": "${service}"}),
            ("inspect_connection_pool", {"service": "${service}"}),
            ("inspect_slow_queries", {"service": "${service}"}),
            ("inspect_deadlock_signals", {"service": "${service}"}),
            ("inspect_transaction_rollback_rate", {"service": "${service}"}),
        ],
        "positive_keys": ("db_health", "pool_state", "slow_query_count", "deadlock_count", "rollback_rate"),
    },
    "check_replication_lag": {
        "tool_names": ["inspect_replication_status"],
        "fallback_calls": [("inspect_replication_status", {"service": "${instance_or_service}"})],
        "positive_keys": ("lag_seconds",),
    },
    "check_log_errors": {
        "tool_names": ["inspect_pod_logs", "check_recent_alerts", "inspect_thread_pool_status"],
        "fallback_calls": [
            ("inspect_pod_logs", {"service": "${service}", "namespace": "${namespace}"}),
            ("check_recent_alerts", {"service": "${service}", "window_minutes": 30}),
            ("inspect_thread_pool_status", {"service": "${service}"}),
        ],
        "positive_keys": ("error_pattern", "oom_detected", "alerts", "pool_state", "queue_depth"),
    },
    "check_alert_history": {
        "tool_names": ["check_recent_alerts"],
        "fallback_calls": [("check_recent_alerts", {"service": "${service}", "window_minutes": 30})],
        "positive_keys": ("alerts", "highest_severity"),
    },
    "check_slo_status": {
        "tool_names": ["check_service_health", "check_recent_alerts", "inspect_error_budget_burn"],
        "fallback_calls": [
            ("check_service_health", {"service": "${service}", "environment": "${cluster}"}),
            ("check_recent_alerts", {"service": "${service}", "window_minutes": 30}),
            ("inspect_error_budget_burn", {"service": "${service}"}),
        ],
        "positive_keys": ("health_status", "alert_count", "burn_state", "burn_rate"),
    },
}


class LocalSkillExecutor:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        skill_registry: SkillRegistry | None = None,
        llm: OpenAICompatToolLLM | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.skill_registry = skill_registry or SkillRegistry()
        self.llm = llm or OpenAICompatToolLLM(self.settings)
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
            "inspect_pod_logs": InspectPodLogsTool(),
            "inspect_pod_events": InspectPodEventsTool(),
            "inspect_jvm_memory": InspectJvmMemoryTool(),
            "inspect_cpu_saturation": InspectCpuSaturationTool(),
            "inspect_thread_pool_status": InspectThreadPoolStatusTool(),
            "inspect_error_budget_burn": InspectErrorBudgetBurnTool(),
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

    async def execute_skill(
        self,
        skill_name: str,
        *,
        params: dict[str, Any],
        context_snapshot: ContextSnapshot,
    ) -> SkillResult:
        task = self._build_task(context_snapshot, params)

        if skill_name in ACTION_SKILLS:
            return SkillResult(
                skill_name=skill_name,
                status="approval_required",
                summary=f"{skill_name} 为动作型 skill，需走 interrupt / approval gate 后才能执行。",
                evidence=[],
                payload={"params": dict(params), "approval_required": True},
            )

        generic_spec = DEFAULT_SKILL_SPECS.get(skill_name)
        if generic_spec is not None:
            fallback_calls = self._resolve_template_calls(
                generic_spec["fallback_calls"],
                params=params,
                context_snapshot=context_snapshot,
            )
            results = await self._run_skill_with_planning(
                skill_name,
                task=task,
                params=params,
                context_snapshot=context_snapshot,
                fallback_calls=fallback_calls,
                tool_names=list(generic_spec["tool_names"]),
            )
            return self._aggregate(skill_name, results, positive_keys=tuple(generic_spec["positive_keys"]))

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

        if skill_name == "diagnose_pod_crash":
            fallback_calls = [
                ("check_pod_status", {"service": params.get("service"), "namespace": params.get("namespace")}),
                ("inspect_pod_events", {"service": params.get("service"), "namespace": params.get("namespace")}),
                ("inspect_pod_logs", {"service": params.get("service"), "namespace": params.get("namespace")}),
                ("inspect_jvm_memory", {"service": params.get("service")}),
                ("inspect_cpu_saturation", {"service": params.get("service")}),
                ("get_quota_status", {"service": params.get("service")}),
                ("check_service_health", {"service": params.get("service"), "environment": params.get("cluster")}),
            ]
            results = await self._run_skill_with_planning(
                skill_name,
                task=task,
                params=params,
                context_snapshot=context_snapshot,
                fallback_calls=fallback_calls,
            )
            return self._aggregate(
                skill_name,
                results,
                positive_keys=(
                    "pods",
                    "health_status",
                    "replica_status",
                    "last_termination_reason",
                    "oom_detected",
                    "error_pattern",
                    "heap_usage_ratio",
                    "quota_state",
                ),
            )

        if skill_name == "check_pod_health":
            results = await self._run_many(
                task,
                [
                    ("check_pod_status", {"service": params.get("service"), "namespace": params.get("namespace")}),
                    ("inspect_pod_events", {"service": params.get("service"), "namespace": params.get("namespace")}),
                    ("inspect_pod_logs", {"service": params.get("service"), "namespace": params.get("namespace")}),
                    ("check_service_health", {"service": params.get("service"), "environment": params.get("cluster")}),
                ],
            )
            return self._aggregate(
                skill_name,
                results,
                positive_keys=("pods", "health_status", "replica_status", "last_termination_reason", "oom_detected"),
            )

        if skill_name == "check_memory_trend":
            results = await self._run_many(
                task,
                [
                    ("check_service_health", {"service": params.get("service"), "environment": params.get("cluster")}),
                    ("check_pod_status", {"service": params.get("service"), "namespace": params.get("namespace")}),
                    ("inspect_pod_events", {"service": params.get("service"), "namespace": params.get("namespace")}),
                    ("inspect_pod_logs", {"service": params.get("service"), "namespace": params.get("namespace")}),
                    ("inspect_jvm_memory", {"service": params.get("service")}),
                    ("inspect_cpu_saturation", {"service": params.get("service")}),
                ],
            )
            return self._aggregate(
                skill_name,
                results,
                positive_keys=("health_status", "pods", "last_termination_reason", "oom_detected", "heap_usage_ratio", "gc_pressure"),
            )

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
                    ("inspect_upstream_dependency", {"service": params.get("service")}),
                    ("inspect_egress_policy", {"service": params.get("service")}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("connectivity_status", "lb_status", "dependency_status", "policy_status"))

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
                    ("inspect_deadlock_signals", {"service": params.get("service")}),
                    ("inspect_transaction_rollback_rate", {"service": params.get("service")}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("db_health", "pool_state", "slow_query_count", "deadlock_count", "rollback_rate"))

        if skill_name == "check_replication_lag":
            results = await self._run_many(task, [("inspect_replication_status", {"service": params.get("instance") or params.get("service")})])
            return self._aggregate(skill_name, results, positive_keys=("lag_seconds",))

        if skill_name == "check_log_errors":
            results = await self._run_many(
                task,
                [
                    ("inspect_pod_logs", {"service": params.get("service"), "namespace": params.get("namespace")}),
                    ("check_recent_alerts", {"service": params.get("service"), "window_minutes": 30}),
                    ("inspect_thread_pool_status", {"service": params.get("service")}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("error_pattern", "oom_detected", "alerts", "pool_state", "queue_depth"))

        if skill_name == "check_alert_history":
            results = await self._run_many(task, [("check_recent_alerts", {"service": params.get("service"), "window_minutes": 30})])
            return self._aggregate(skill_name, results, positive_keys=("alerts", "highest_severity"))

        if skill_name == "check_slo_status":
            results = await self._run_many(
                task,
                [
                    ("check_service_health", {"service": params.get("service"), "environment": params.get("cluster")}),
                    ("check_recent_alerts", {"service": params.get("service"), "window_minutes": 30}),
                    ("inspect_error_budget_burn", {"service": params.get("service")}),
                ],
            )
            return self._aggregate(skill_name, results, positive_keys=("health_status", "alert_count", "burn_state", "burn_rate"))

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

    async def _run_skill_with_planning(
        self,
        skill_name: str,
        *,
        task: TaskEnvelope,
        params: dict[str, Any],
        context_snapshot: ContextSnapshot,
        fallback_calls: list[tuple[str, dict[str, Any]]],
        tool_names: list[str] | None = None,
    ) -> list[ToolExecutionResult]:
        signature = self.skill_registry.get_signature(skill_name)
        declared_tool_names = list(tool_names or [])
        if signature and signature.tool_names:
            declared_tool_names = list(signature.tool_names)
        should_plan_with_llm = (
            self.llm.enabled
            and bool(declared_tool_names)
            and signature is not None
            and signature.planning_mode == "llm_parallel"
        )
        if should_plan_with_llm:
            planned = await self._run_llm_planned_calls(
                skill_name=skill_name,
                task=task,
                params=params,
                context_snapshot=context_snapshot,
                tool_names=declared_tool_names,
                skill_summary=signature.sop_summary if signature is not None else "",
                when_to_use=signature.when_to_use if signature is not None else "",
                guide_text=self._load_guide_text(signature.guide_path) if signature is not None else "",
            )
            if planned:
                return planned
        return await self._run_many(task, fallback_calls)

    async def _run_llm_planned_calls(
        self,
        *,
        skill_name: str,
        task: TaskEnvelope,
        params: dict[str, Any],
        context_snapshot: ContextSnapshot,
        tool_names: list[str],
        skill_summary: str,
        when_to_use: str,
        guide_text: str,
    ) -> list[ToolExecutionResult]:
        allowed_tools = [self.tools[name] for name in tool_names if name in self.tools]
        if not allowed_tools:
            return []
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 skill 内部的工具编排器。\n"
                    "目标是根据当前 skill 的 SOP 与上下文，决定本轮要调用哪些工具。\n"
                    "如果多个工具彼此独立，请一次返回多个 tool calls，runtime 会并行执行。\n"
                    "只允许调用提供给你的 tools；不要输出解释文字。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "skill_name": skill_name,
                        "params": params,
                        "when_to_use": when_to_use,
                        "sop_summary": skill_summary,
                        "guide": guide_text[:4000],
                        "request": dict(context_snapshot.request or {}),
                        "rag_context": context_snapshot.rag_context.model_dump() if context_snapshot.rag_context is not None else {},
                        "similar_cases": [item.model_dump() for item in context_snapshot.similar_cases[:3]],
                        "available_tools": [tool.name for tool in allowed_tools],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = await self.llm.chat(messages, tools=[tool.as_openai_tool() for tool in allowed_tools])
        tool_calls = response.get("tool_calls") if isinstance(response, dict) else None
        if not isinstance(tool_calls, list) or not tool_calls:
            return []

        async_calls: list[tuple[str, Any]] = []
        for call in tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            if name not in tool_names or name not in self.tools:
                continue
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments or {})
            except Exception:
                arguments = {}
            async_calls.append((name, self.tools[name].run(task, arguments)))
        if not async_calls:
            return []
        results = await asyncio.gather(*[item[1] for item in async_calls], return_exceptions=True)
        normalized: list[ToolExecutionResult] = []
        for (tool_name, _), item in zip(async_calls, results):
            if isinstance(item, ToolExecutionResult):
                normalized.append(item)
                continue
            normalized.append(
                ToolExecutionResult(
                    tool_name=tool_name,
                    status="error",
                    summary="tool 执行失败",
                    payload={},
                    evidence=[str(item)],
                )
            )
        return normalized

    def _load_guide_text(self, guide_path: str) -> str:
        if not guide_path:
            return ""
        path = Path(__file__).resolve().parent / guide_path
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _resolve_template_calls(
        self,
        calls: list[tuple[str, dict[str, Any]]],
        *,
        params: dict[str, Any],
        context_snapshot: ContextSnapshot,
    ) -> list[tuple[str, dict[str, Any]]]:
        request = dict(context_snapshot.request or {})
        substitutions = {
            "${service}": params.get("service") or request.get("service") or "",
            "${namespace}": params.get("namespace") or request.get("namespace") or "default",
            "${cluster}": params.get("cluster") or request.get("cluster") or "prod-shanghai-1",
            "${project_or_service}": params.get("project") or params.get("service") or request.get("service") or "",
            "${domain_or_service}": params.get("domain") or params.get("service") or request.get("service") or "",
            "${instance_or_service}": params.get("instance") or params.get("service") or request.get("service") or "",
        }
        resolved: list[tuple[str, dict[str, Any]]] = []
        for tool_name, arguments in calls:
            payload = {}
            for key, value in arguments.items():
                if isinstance(value, str) and value in substitutions:
                    payload[key] = substitutions[value]
                else:
                    payload[key] = value
            resolved.append((tool_name, payload))
        return resolved

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
                "mock_scenario": str(request.get("mock_scenario") or ""),
                "mock_scenarios": dict(request.get("mock_scenarios") or {}),
                "mock_tool_responses": dict(request.get("mock_tool_responses") or {}),
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
                if not LocalSkillExecutor._is_positive_value(key, value):
                    continue
                return True
        return False

    @staticmethod
    def _is_positive_value(key: str, value: Any) -> bool:
        if value in (None, "", 0, False):
            return False
        if key == "pods":
            if not isinstance(value, list):
                return False
            for pod in value:
                if not isinstance(pod, dict):
                    continue
                if not bool(pod.get("ready", True)):
                    return True
                if str(pod.get("status") or "").lower() not in {"running", "succeeded"}:
                    return True
                if int(pod.get("restarts") or 0) > 0:
                    return True
            return False
        if key in {"health_status", "route_status", "lb_status", "resolution_status", "connectivity_status", "dependency_status", "policy_status", "db_health", "pool_state", "burn_state", "quota_state", "gc_pressure", "cpu_saturation"}:
            return str(value).lower() not in {"ok", "healthy", "normal", "connected", "green", "stable", "within_limit"}
        if key in {"last_termination_reason", "error_pattern", "failed_stage", "highest_severity"}:
            return str(value).strip().lower() not in {"", "none", "ok", "info"}
        if key in {"oom_detected"}:
            return bool(value)
        if key in {"heap_usage_ratio", "burn_rate", "rollback_rate"}:
            try:
                return float(value) >= 0.7
            except Exception:
                return False
        if key in {"lag_seconds", "slow_query_count", "deadlock_count", "alert_count", "queue_depth"}:
            try:
                return float(value) > 0
            except Exception:
                return False
        return bool(value)
