from __future__ import annotations

from typing import Any, Callable

from ..service_names import canonical_service_name
from ..tools.contracts import ToolExecutionResult


WorldBuilder = Callable[[dict[str, Any], str, str, str], ToolExecutionResult]


def project_world_state_tool_result(
    tool_name: str,
    world_state: dict[str, Any],
    *,
    service: str,
    cluster: str,
    namespace: str,
) -> ToolExecutionResult | None:
    builders: dict[str, WorldBuilder] = {
        "check_recent_deployments": _build_check_recent_deployments,
        "check_pipeline_status": _build_check_pipeline_status,
        "get_deployment_status": _build_get_deployment_status,
        "check_service_health": _build_check_service_health,
        "check_recent_alerts": _build_check_recent_alerts,
        "inspect_build_failure_logs": _build_inspect_build_failure_logs,
        "get_rollback_history": _build_get_rollback_history,
        "get_change_records": _build_get_change_records,
        "check_pod_status": _build_check_pod_status,
        "inspect_pod_logs": _build_inspect_pod_logs,
        "inspect_pod_events": _build_inspect_pod_events,
        "inspect_jvm_memory": _build_inspect_jvm_memory,
        "inspect_cpu_saturation": _build_inspect_cpu_saturation,
        "inspect_thread_pool_status": _build_inspect_thread_pool_status,
        "inspect_dns_resolution": _build_inspect_dns_resolution,
        "inspect_ingress_route": _build_inspect_ingress_route,
        "inspect_vpc_connectivity": _build_inspect_vpc_connectivity,
        "inspect_load_balancer_status": _build_inspect_load_balancer_status,
        "inspect_upstream_dependency": _build_inspect_upstream_dependency,
        "inspect_egress_policy": _build_inspect_egress_policy,
        "inspect_db_instance_health": _build_inspect_db_instance_health,
        "inspect_replication_status": _build_inspect_replication_status,
        "inspect_slow_queries": _build_inspect_slow_queries,
        "inspect_connection_pool": _build_inspect_connection_pool,
        "inspect_deadlock_signals": _build_inspect_deadlock_signals,
        "inspect_transaction_rollback_rate": _build_inspect_transaction_rollback_rate,
        "investigate_resource_provisioning": _build_investigate_resource_provisioning,
        "inspect_cluster_bootstrap": _build_inspect_cluster_bootstrap,
        "inspect_machine_provisioning": _build_inspect_machine_provisioning,
        "get_quota_status": _build_get_quota_status,
    }
    builder = builders.get(tool_name)
    if builder is None or not isinstance(world_state, dict):
        return None
    scoped = _resolve_scoped_world_state(world_state, service)
    return builder(scoped, _service_name(service, scoped), cluster, namespace)


def _resolve_scoped_world_state(world_state: dict[str, Any], service: str) -> dict[str, Any]:
    services = world_state.get("services")
    if not isinstance(services, dict):
        return dict(world_state)
    service_name = canonical_service_name(service) or service
    service_payload = services.get(service_name) or services.get(service)
    if not isinstance(service_payload, dict):
        return dict(world_state)
    base = {key: value for key, value in world_state.items() if key != "services"}
    return _deep_merge(base, service_payload)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _service_name(service: str, world_state: dict[str, Any]) -> str:
    return canonical_service_name(service) or canonical_service_name(world_state.get("service")) or service or "unknown-service"


def _signal(world_state: dict[str, Any], domain: str) -> dict[str, Any]:
    signals = world_state.get("signals")
    if not isinstance(signals, dict):
        return {}
    payload = signals.get(domain)
    return dict(payload) if isinstance(payload, dict) else {}


def _timeline_notes(world_state: dict[str, Any]) -> list[str]:
    timeline = world_state.get("timeline")
    if not isinstance(timeline, dict):
        return []
    notes: list[str] = []
    if timeline.get("failure_window"):
        notes.append(f"failure_window={timeline.get('failure_window')}")
    if timeline.get("deploy_minutes_ago") is not None:
        notes.append(f"deploy_minutes_ago={timeline.get('deploy_minutes_ago')}")
    if timeline.get("symptom_started_minutes_ago") is not None:
        notes.append(f"symptom_started_minutes_ago={timeline.get('symptom_started_minutes_ago')}")
    return notes[:2]


def _alerts(world_state: dict[str, Any], service: str) -> list[dict[str, Any]]:
    monitor = _signal(world_state, "monitor")
    alerts = monitor.get("alerts")
    if isinstance(alerts, list) and alerts:
        normalized: list[dict[str, Any]] = []
        for item in alerts[:5]:
            if isinstance(item, dict):
                normalized.append(
                    {
                        "name": str(item.get("name") or f"{service} alert"),
                        "severity": str(item.get("severity") or "info"),
                        "status": str(item.get("status") or "stable"),
                    }
                )
        if normalized:
            return normalized
    return [
        {
            "name": f"{service} baseline availability",
            "severity": "info",
            "status": "stable",
        }
    ]


def _summary(prefix: str, world_state: dict[str, Any]) -> str:
    notes = _timeline_notes(world_state)
    if not notes:
        return prefix
    return f"{prefix}（{', '.join(notes)}）"


def _build_check_recent_deployments(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    cicd = _signal(world_state, "cicd")
    has_recent_deploy = bool(cicd.get("has_recent_deploy"))
    latest_revision = str(cicd.get("latest_revision") or "unknown")
    deploy_count = int(cicd.get("deploy_count") or (1 if has_recent_deploy else 0))
    evidence = [f"has_recent_deploy={has_recent_deploy}", f"latest_revision={latest_revision}"]
    evidence.extend(_timeline_notes(world_state))
    return ToolExecutionResult(
        tool_name="check_recent_deployments",
        status="completed",
        summary=_summary("已从共享事故世界投影最近发布记录。", world_state),
        payload={
            "service": service,
            "environment": cluster,
            "namespace": namespace,
            "has_recent_deploy": has_recent_deploy,
            "deploy_count": deploy_count,
            "latest_revision": latest_revision,
        },
        evidence=evidence[:4],
    )


def _build_check_pipeline_status(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    cicd = _signal(world_state, "cicd")
    pipeline_status = str(cicd.get("pipeline_status") or "success")
    failed_stage = str(cicd.get("failed_stage") or "none")
    evidence = [f"pipeline_status={pipeline_status}"]
    if failed_stage not in {"", "none"}:
        evidence.append(f"failed_stage={failed_stage}")
    return ToolExecutionResult(
        tool_name="check_pipeline_status",
        status="completed",
        summary=_summary("已从共享事故世界投影流水线状态。", world_state),
        payload={
            "service": service,
            "pipeline_status": pipeline_status,
            "failed_stage": failed_stage,
        },
        evidence=evidence,
    )


def _build_get_deployment_status(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    cicd = _signal(world_state, "cicd")
    rollout_status = str(cicd.get("rollout_status") or ("degraded" if cicd.get("has_recent_deploy") else "stable"))
    active_alerts = [str(item) for item in list(cicd.get("active_alerts") or [])[:4]]
    return ToolExecutionResult(
        tool_name="get_deployment_status",
        status="completed",
        summary=_summary("已从共享事故世界投影 rollout 状态。", world_state),
        payload={
            "service": service,
            "environment": cluster,
            "rollout_status": rollout_status,
            "active_alerts": active_alerts,
        },
        evidence=[f"rollout_status={rollout_status}", f"active_alerts={','.join(active_alerts) or 'none'}"],
    )


def _build_check_service_health(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    monitor = _signal(world_state, "monitor")
    k8s = _signal(world_state, "k8s")
    health_status = str(monitor.get("health_status") or "healthy")
    replica_status = str(monitor.get("replica_status") or "all_ready")
    error_rate = float(monitor.get("error_rate_percent") or 0.2)
    p99_ms = int(monitor.get("p99_latency_ms") or 180)
    impacted_endpoints = [str(item) for item in list(monitor.get("impacted_endpoints") or [])[:4]]
    if not monitor and k8s:
        ready = int(k8s.get("ready_replicas") or 0)
        desired = int(k8s.get("desired_replicas") or 0)
        if desired and ready < desired:
            health_status = "degraded"
            replica_status = "partial_ready"
            error_rate = max(error_rate, 4.5)
            p99_ms = max(p99_ms, 1200)
    return ToolExecutionResult(
        tool_name="check_service_health",
        status="completed",
        summary=_summary("已从共享事故世界投影服务健康状态。", world_state),
        payload={
            "service": service,
            "environment": cluster,
            "health_status": health_status,
            "replica_status": replica_status,
            "error_rate_percent": error_rate,
            "p99_latency_ms": p99_ms,
            "impacted_endpoints": impacted_endpoints,
        },
        evidence=[f"health_status={health_status}", f"error_rate_percent={error_rate}"],
    )


def _build_check_recent_alerts(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    alerts = _alerts(world_state, service)
    highest = alerts[0]["severity"] if alerts else "info"
    return ToolExecutionResult(
        tool_name="check_recent_alerts",
        status="completed",
        summary=_summary("已从共享事故世界投影最近告警。", world_state),
        payload={
            "service": service,
            "window_minutes": 30,
            "alerts": alerts,
            "alert_count": len(alerts),
            "highest_severity": highest,
        },
        evidence=[f"alert_count={len(alerts)}", f"highest_severity={highest}"],
    )


def _build_inspect_build_failure_logs(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    cicd = _signal(world_state, "cicd")
    failed_stage = str(cicd.get("failed_stage") or "none")
    suspected_error = str(cicd.get("suspected_error") or "none")
    log_snippets = [str(item) for item in list(cicd.get("log_snippets") or [])[:3]]
    return ToolExecutionResult(
        tool_name="inspect_build_failure_logs",
        status="completed",
        summary=_summary("已从共享事故世界投影构建失败日志。", world_state),
        payload={
            "service": service,
            "failed_stage": failed_stage,
            "suspected_error": suspected_error,
            "log_snippets": log_snippets,
        },
        evidence=[f"failed_stage={failed_stage}", f"suspected_error={suspected_error}"],
    )


def _build_get_rollback_history(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    cicd = _signal(world_state, "cicd")
    history = [item for item in list(cicd.get("recent_rollbacks") or []) if isinstance(item, dict)][:3]
    last_known_stable_revision = str(cicd.get("last_known_stable_revision") or "")
    rollback_recommended = bool(cicd.get("rollback_recommended"))
    return ToolExecutionResult(
        tool_name="get_rollback_history",
        status="completed",
        summary=_summary("已从共享事故世界投影回滚历史。", world_state),
        payload={
            "service": service,
            "recent_rollbacks": history,
            "last_known_stable_revision": last_known_stable_revision,
            "rollback_recommended": rollback_recommended,
        },
        evidence=[f"rollback_count={len(history)}", f"last_known_stable_revision={last_known_stable_revision or 'unknown'}"],
    )


def _build_get_change_records(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    cicd = _signal(world_state, "cicd")
    change_count = int(cicd.get("change_count") or 0)
    suspect_component = str(cicd.get("suspect_component") or "none")
    changes = [item for item in list(cicd.get("changes") or []) if isinstance(item, dict)][:5]
    return ToolExecutionResult(
        tool_name="get_change_records",
        status="completed",
        summary=_summary("已从共享事故世界投影变更记录。", world_state),
        payload={
            "service": service,
            "change_count": change_count,
            "suspect_component": suspect_component,
            "changes": changes,
        },
        evidence=[f"change_count={change_count}", f"suspect_component={suspect_component}"],
    )


def _build_check_pod_status(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    k8s = _signal(world_state, "k8s")
    pods = [item for item in list(k8s.get("pods") or []) if isinstance(item, dict)]
    ready_replicas = int(k8s.get("ready_replicas") or len([pod for pod in pods if pod.get("ready")]))
    desired_replicas = int(k8s.get("desired_replicas") or len(pods))
    return ToolExecutionResult(
        tool_name="check_pod_status",
        status="completed",
        summary=_summary("已从共享事故世界投影 Pod 状态。", world_state),
        payload={
            "service": service,
            "namespace": namespace,
            "pods": pods,
            "ready_replicas": ready_replicas,
            "desired_replicas": desired_replicas,
        },
        evidence=[f"ready {ready_replicas}/{desired_replicas}"],
    )


def _build_inspect_pod_logs(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    k8s = _signal(world_state, "k8s")
    error_pattern = str(k8s.get("error_pattern") or "none")
    oom_detected = bool(k8s.get("oom_detected"))
    log_snippets = [str(item) for item in list(k8s.get("log_snippets") or [])[:3]]
    return ToolExecutionResult(
        tool_name="inspect_pod_logs",
        status="completed",
        summary=_summary("已从共享事故世界投影 Pod 日志。", world_state),
        payload={
            "service": service,
            "namespace": namespace,
            "error_pattern": error_pattern,
            "oom_detected": oom_detected,
            "log_snippets": log_snippets,
        },
        evidence=log_snippets[:2] or [f"error_pattern={error_pattern}"],
    )


def _build_inspect_pod_events(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    k8s = _signal(world_state, "k8s")
    events = [item for item in list(k8s.get("events") or []) if isinstance(item, dict)][:4]
    last_termination_reason = str(k8s.get("last_termination_reason") or "none")
    return ToolExecutionResult(
        tool_name="inspect_pod_events",
        status="completed",
        summary=_summary("已从共享事故世界投影 Pod 事件。", world_state),
        payload={
            "service": service,
            "namespace": namespace,
            "event_count": len(events),
            "last_termination_reason": last_termination_reason,
            "events": events,
        },
        evidence=[f"last_termination_reason={last_termination_reason}", f"event_count={len(events)}"],
    )


def _build_inspect_jvm_memory(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    k8s = _signal(world_state, "k8s")
    heap_usage = float(k8s.get("heap_usage_ratio") or 0.42)
    gc_pressure = str(k8s.get("gc_pressure") or "normal")
    return ToolExecutionResult(
        tool_name="inspect_jvm_memory",
        status="completed",
        summary=_summary("已从共享事故世界投影 JVM 内存状态。", world_state),
        payload={"service": service, "heap_usage_ratio": heap_usage, "gc_pressure": gc_pressure},
        evidence=[f"heap_usage={heap_usage}", f"gc_pressure={gc_pressure}"],
    )


def _build_inspect_cpu_saturation(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    k8s = _signal(world_state, "k8s")
    saturation = str(k8s.get("cpu_saturation") or "normal")
    throttling_ratio = float(k8s.get("throttling_ratio") or 0.01)
    return ToolExecutionResult(
        tool_name="inspect_cpu_saturation",
        status="completed",
        summary=_summary("已从共享事故世界投影 CPU 饱和度。", world_state),
        payload={"service": service, "cpu_saturation": saturation, "throttling_ratio": throttling_ratio},
        evidence=[f"cpu_saturation={saturation}", f"throttling_ratio={throttling_ratio}"],
    )


def _build_inspect_thread_pool_status(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    k8s = _signal(world_state, "k8s")
    pool_state = str(k8s.get("thread_pool_state") or "healthy")
    queue_depth = int(k8s.get("queue_depth") or 0)
    return ToolExecutionResult(
        tool_name="inspect_thread_pool_status",
        status="completed",
        summary=_summary("已从共享事故世界投影线程池状态。", world_state),
        payload={"service": service, "pool_state": pool_state, "queue_depth": queue_depth},
        evidence=[f"pool_state={pool_state}", f"queue_depth={queue_depth}"],
    )


def _build_inspect_dns_resolution(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    network = _signal(world_state, "network")
    resolution_status = str(network.get("resolution_status") or "healthy")
    return ToolExecutionResult(
        tool_name="inspect_dns_resolution",
        status="completed",
        summary=_summary("已从共享事故世界投影 DNS 状态。", world_state),
        payload={"service": service, "resolution_status": resolution_status},
        evidence=[f"dns={resolution_status}"],
    )


def _build_inspect_ingress_route(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    network = _signal(world_state, "network")
    route_status = str(network.get("route_status") or "healthy")
    return ToolExecutionResult(
        tool_name="inspect_ingress_route",
        status="completed",
        summary=_summary("已从共享事故世界投影 ingress 路由状态。", world_state),
        payload={"service": service, "route_status": route_status},
        evidence=[f"route_status={route_status}"],
    )


def _build_inspect_vpc_connectivity(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    network = _signal(world_state, "network")
    connectivity_status = str(network.get("connectivity_status") or "healthy")
    return ToolExecutionResult(
        tool_name="inspect_vpc_connectivity",
        status="completed",
        summary=_summary("已从共享事故世界投影 VPC 连通性。", world_state),
        payload={"service": service, "connectivity_status": connectivity_status},
        evidence=[f"connectivity_status={connectivity_status}"],
    )


def _build_inspect_load_balancer_status(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    network = _signal(world_state, "network")
    lb_status = str(network.get("lb_status") or "healthy")
    return ToolExecutionResult(
        tool_name="inspect_load_balancer_status",
        status="completed",
        summary=_summary("已从共享事故世界投影负载均衡状态。", world_state),
        payload={"service": service, "lb_status": lb_status},
        evidence=[f"lb_status={lb_status}"],
    )


def _build_inspect_upstream_dependency(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    network = _signal(world_state, "network")
    dependency_status = str(network.get("dependency_status") or "healthy")
    timeout_ratio = float(network.get("timeout_ratio") or 0.0)
    return ToolExecutionResult(
        tool_name="inspect_upstream_dependency",
        status="completed",
        summary=_summary("已从共享事故世界投影上游依赖状态。", world_state),
        payload={"service": service, "dependency_status": dependency_status, "timeout_ratio": timeout_ratio},
        evidence=[f"dependency_status={dependency_status}", f"timeout_ratio={timeout_ratio}"],
    )


def _build_inspect_egress_policy(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    network = _signal(world_state, "network")
    policy_status = str(network.get("policy_status") or "healthy")
    return ToolExecutionResult(
        tool_name="inspect_egress_policy",
        status="completed",
        summary=_summary("已从共享事故世界投影出口策略状态。", world_state),
        payload={"service": service, "policy_status": policy_status},
        evidence=[f"policy_status={policy_status}"],
    )


def _build_inspect_db_instance_health(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    db = _signal(world_state, "db")
    db_health = str(db.get("db_health") or "healthy")
    return ToolExecutionResult(
        tool_name="inspect_db_instance_health",
        status="completed",
        summary=_summary("已从共享事故世界投影数据库实例健康状态。", world_state),
        payload={"service": service, "db_health": db_health},
        evidence=[f"db_health={db_health}"],
    )


def _build_inspect_replication_status(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    db = _signal(world_state, "db")
    lag_seconds = int(db.get("lag_seconds") or 0)
    return ToolExecutionResult(
        tool_name="inspect_replication_status",
        status="completed",
        summary=_summary("已从共享事故世界投影复制状态。", world_state),
        payload={"service": service, "lag_seconds": lag_seconds},
        evidence=[f"replication_lag={lag_seconds}s"],
    )


def _build_inspect_slow_queries(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    db = _signal(world_state, "db")
    slow_query_count = int(db.get("slow_query_count") or 0)
    max_latency_ms = int(db.get("max_latency_ms") or 0)
    return ToolExecutionResult(
        tool_name="inspect_slow_queries",
        status="completed",
        summary=_summary("已从共享事故世界投影慢查询信号。", world_state),
        payload={"service": service, "slow_query_count": slow_query_count, "max_latency_ms": max_latency_ms},
        evidence=[f"slow_query_count={slow_query_count}", f"max_latency_ms={max_latency_ms}"],
    )


def _build_inspect_connection_pool(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    db = _signal(world_state, "db")
    pool_state = str(db.get("pool_state") or "healthy")
    active_connections = int(db.get("active_connections") or 0)
    max_connections = int(db.get("max_connections") or 0)
    return ToolExecutionResult(
        tool_name="inspect_connection_pool",
        status="completed",
        summary=_summary("已从共享事故世界投影连接池状态。", world_state),
        payload={
            "service": service,
            "pool_state": pool_state,
            "active_connections": active_connections,
            "max_connections": max_connections,
        },
        evidence=[f"pool_state={pool_state}", f"active_connections={active_connections}/{max_connections}"],
    )


def _build_inspect_deadlock_signals(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    db = _signal(world_state, "db")
    deadlock_count = int(db.get("deadlock_count") or 0)
    return ToolExecutionResult(
        tool_name="inspect_deadlock_signals",
        status="completed",
        summary=_summary("已从共享事故世界投影死锁信号。", world_state),
        payload={"service": service, "deadlock_count": deadlock_count},
        evidence=[f"deadlocks={deadlock_count}"],
    )


def _build_inspect_transaction_rollback_rate(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    db = _signal(world_state, "db")
    rollback_rate = float(db.get("rollback_rate") or 0.0)
    return ToolExecutionResult(
        tool_name="inspect_transaction_rollback_rate",
        status="completed",
        summary=_summary("已从共享事故世界投影事务回滚率。", world_state),
        payload={"service": service, "rollback_rate": rollback_rate},
        evidence=[f"rollback_rate={rollback_rate}"],
    )


def _build_investigate_resource_provisioning(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    sde = _signal(world_state, "sde")
    failure_stage = str(sde.get("failure_stage") or "none")
    request_status = str(sde.get("request_status") or "needs_check")
    return ToolExecutionResult(
        tool_name="investigate_resource_provisioning",
        status="completed",
        summary=_summary("已从共享事故世界投影资源开通调查结果。", world_state),
        payload={"service": service, "failure_stage": failure_stage, "request_status": request_status},
        evidence=[f"failure_stage={failure_stage}"],
    )


def _build_inspect_cluster_bootstrap(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    sde = _signal(world_state, "sde")
    bootstrap_status = str(sde.get("bootstrap_status") or "healthy")
    suspected_blocker = str(sde.get("suspected_blocker") or "none")
    return ToolExecutionResult(
        tool_name="inspect_cluster_bootstrap",
        status="completed",
        summary=_summary("已从共享事故世界投影集群拉起状态。", world_state),
        payload={"service": service, "bootstrap_status": bootstrap_status, "suspected_blocker": suspected_blocker},
        evidence=[f"cluster_bootstrap={bootstrap_status}", f"suspected_blocker={suspected_blocker}"],
    )


def _build_inspect_machine_provisioning(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    sde = _signal(world_state, "sde")
    provisioning_status = str(sde.get("provisioning_status") or "needs_check")
    failure_reason = str(sde.get("failure_reason") or "none")
    return ToolExecutionResult(
        tool_name="inspect_machine_provisioning",
        status="completed",
        summary=_summary("已从共享事故世界投影机器开通结果。", world_state),
        payload={"service": service, "provisioning_status": provisioning_status, "failure_reason": failure_reason},
        evidence=[f"machine_provisioning_reason={failure_reason}"],
    )


def _build_get_quota_status(world_state: dict[str, Any], service: str, cluster: str, namespace: str) -> ToolExecutionResult:
    sde = _signal(world_state, "sde")
    quota_state = str(sde.get("quota_state") or "sufficient")
    requested_cpu = int(sde.get("requested_cpu") or 0)
    available_cpu = int(sde.get("available_cpu") or 0)
    return ToolExecutionResult(
        tool_name="get_quota_status",
        status="completed",
        summary=_summary("已从共享事故世界投影配额状态。", world_state),
        payload={
            "service": service,
            "quota_state": quota_state,
            "requested_cpu": requested_cpu,
            "available_cpu": available_cpu,
        },
        evidence=[f"quota_state={quota_state}", f"available_cpu={available_cpu}"],
    )
