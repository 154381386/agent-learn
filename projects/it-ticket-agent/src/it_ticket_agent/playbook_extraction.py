from __future__ import annotations

import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .playbook_retrieval import infer_service_type


def build_playbook_candidate_from_cases(cases: list[dict[str, Any]], *, min_cases: int = 3) -> dict[str, Any] | None:
    verified_cases = [case for case in cases if _is_verified_case(case)]
    if len(verified_cases) < min_cases:
        return None
    failure_mode = str(verified_cases[0].get("failure_mode") or "").strip()
    signal_pattern = str(verified_cases[0].get("signal_pattern") or "").strip()
    if not failure_mode:
        return None
    source_case_ids = [str(case.get("case_id") or "") for case in verified_cases if case.get("case_id")]
    services = sorted({str(case.get("service") or "").strip() for case in verified_cases if case.get("service")})
    service_type = infer_service_type(service=services[0] if services else "", message=failure_mode)
    playbook_id = _stable_playbook_id(service_type=service_type, failure_mode=failure_mode, signal_pattern=signal_pattern)
    environments = sorted(
        {
            value
            for case in verified_cases
            for value in (str(case.get("cluster") or "").strip(), str(case.get("namespace") or "").strip())
            if value
        }
    )
    success_count = sum(1 for case in verified_cases if case.get("verification_passed") is True)
    failure_count = sum(1 for case in verified_cases if case.get("verification_passed") is False)
    title = _title_for_failure_mode(failure_mode, signal_pattern)
    return {
        "playbook_id": playbook_id,
        "version": 1,
        "title": title,
        "status": "pending_review",
        "human_verified": False,
        "service_type": service_type or "generic",
        "failure_modes": [failure_mode],
        "environments": environments[:8],
        "trigger_conditions": _trigger_conditions_for_failure_mode(failure_mode),
        "signal_patterns": [signal_pattern] if signal_pattern else [],
        "negative_conditions": [],
        "required_entities": ["service", "cluster", "namespace"],
        "diagnostic_goal": _diagnostic_goal_for_failure_mode(failure_mode),
        "diagnostic_steps": default_diagnostic_steps(failure_mode),
        "evidence_requirements": _evidence_requirements_for_failure_mode(failure_mode),
        "guardrails": _guardrails_for_failure_mode(failure_mode),
        "common_false_positives": _false_positives_for_failure_mode(failure_mode),
        "source_case_ids": source_case_ids[:20],
        "success_count": success_count,
        "failure_count": failure_count,
        "last_eval_passed": success_count >= max(1, failure_count),
        "review_note": f"由 {len(verified_cases)} 个人工确认案例聚合生成，需值班人审核后才进入在线召回。",
    }


def default_diagnostic_steps(failure_mode: str) -> list[dict[str, Any]]:
    if failure_mode == "deploy_regression":
        return [
            {"tool_name": "check_service_health", "purpose": "确认错误率、延迟和影响窗口", "params": {}},
            {"tool_name": "check_recent_deployments", "purpose": "确认故障窗口内是否有发布或回滚", "params": {}},
            {"tool_name": "get_change_records", "purpose": "对齐变更记录和异常开始时间", "params": {}},
            {"tool_name": "check_pod_status", "purpose": "检查发布后 Pod 健康、重启和 readiness", "params": {}},
            {"tool_name": "inspect_pod_logs", "purpose": "提取发布后新增异常日志", "params": {}},
        ]
    if failure_mode == "dependency_timeout":
        return [
            {"tool_name": "check_service_health", "purpose": "确认 5xx、timeout 和延迟影响范围", "params": {}},
            {"tool_name": "inspect_upstream_dependency", "purpose": "确认上游依赖错误率和 timeout 比例", "params": {}},
            {"tool_name": "inspect_ingress_route", "purpose": "检查 gateway / ingress 路由异常", "params": {}},
            {"tool_name": "inspect_vpc_connectivity", "purpose": "排除网络链路阻塞", "params": {}},
            {"tool_name": "check_recent_alerts", "purpose": "确认依赖或网络告警是否同窗出现", "params": {}},
        ]
    if failure_mode == "db_pool_saturation":
        return [
            {"tool_name": "check_service_health", "purpose": "确认请求失败和延迟是否集中在数据库调用", "params": {}},
            {"tool_name": "inspect_connection_pool", "purpose": "确认连接池活跃数、等待数和耗尽情况", "params": {}},
            {"tool_name": "inspect_slow_queries", "purpose": "定位慢查询或锁等待", "params": {}},
            {"tool_name": "inspect_db_instance_health", "purpose": "检查数据库实例 CPU、IO 和连接数", "params": {}},
        ]
    if failure_mode == "oom":
        return [
            {"tool_name": "check_pod_status", "purpose": "确认 Pod 重启、OOMKilled 和副本健康", "params": {}},
            {"tool_name": "inspect_jvm_memory", "purpose": "确认 heap、GC 和内存压力", "params": {}},
            {"tool_name": "inspect_pod_events", "purpose": "检查 OOMKilled、Evicted 等事件", "params": {}},
            {"tool_name": "inspect_pod_logs", "purpose": "提取 OOM 前后的异常日志", "params": {}},
        ]
    return [
        {"tool_name": "check_service_health", "purpose": "确认服务健康和影响范围", "params": {}},
        {"tool_name": "check_recent_alerts", "purpose": "确认近期告警", "params": {}},
    ]


def _is_verified_case(case: dict[str, Any]) -> bool:
    return bool(case.get("human_verified")) and str(case.get("case_status") or "") == "verified"


def _stable_playbook_id(*, service_type: str, failure_mode: str, signal_pattern: str) -> str:
    base = f"{service_type or 'generic'}:{failure_mode}:{signal_pattern or 'generic'}"
    suffix = uuid5(NAMESPACE_URL, base).hex[:8]
    slug = _slug("-".join(part for part in [service_type or "generic", failure_mode, signal_pattern] if part))[:48]
    return f"pb-{slug}-{suffix}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug or "generic"


def _title_for_failure_mode(failure_mode: str, signal_pattern: str) -> str:
    mapping = {
        "deploy_regression": "发布后服务异常排查",
        "dependency_timeout": "上游依赖超时 / 5xx 排查",
        "db_pool_saturation": "数据库连接池或慢查询退化排查",
        "oom": "Pod OOM / 重启排查",
    }
    suffix = f"（{signal_pattern}）" if signal_pattern else ""
    return mapping.get(failure_mode, f"{failure_mode} 排查") + suffix


def _trigger_conditions_for_failure_mode(failure_mode: str) -> list[str]:
    mapping = {
        "deploy_regression": ["发布", "deploy", "release", "pipeline", "回滚", "5xx"],
        "dependency_timeout": ["timeout", "超时", "502", "503", "504", "gateway", "上游依赖"],
        "db_pool_saturation": ["慢查询", "连接池", "database", "mysql", "postgres", "deadlock"],
        "oom": ["oom", "oomkilled", "pod 重启", "内存", "heap"],
    }
    return mapping.get(failure_mode, [failure_mode])


def _diagnostic_goal_for_failure_mode(failure_mode: str) -> str:
    mapping = {
        "deploy_regression": "先验证异常窗口是否与发布/变更重合，再用 Pod 状态和日志确认发布影响。",
        "dependency_timeout": "先确认影响面，再验证上游依赖、入口网关和网络链路是否退化。",
        "db_pool_saturation": "先确认数据库调用是否是瓶颈，再验证连接池、慢查询和实例健康。",
        "oom": "先确认 Pod 是否 OOM/重启，再定位内存压力、事件和异常日志。",
    }
    return mapping.get(failure_mode, "按影响面、关键依赖和现场证据顺序完成诊断。")


def _evidence_requirements_for_failure_mode(failure_mode: str) -> list[str]:
    base = ["必须有实时观测证据", "不能只凭历史案例或 Playbook 下结论"]
    mapping = {
        "deploy_regression": ["需要发布窗口证据", "需要错误率/日志/Pod 至少一种现场证据"],
        "dependency_timeout": ["需要依赖 timeout/error ratio 证据", "需要入口或链路证据排除误判"],
        "db_pool_saturation": ["需要连接池或慢查询证据", "需要数据库实例健康指标"],
        "oom": ["需要 Pod 状态或事件证据", "需要 OOM/内存压力证据"],
    }
    return base + mapping.get(failure_mode, [])


def _guardrails_for_failure_mode(failure_mode: str) -> list[str]:
    mapping = {
        "deploy_regression": ["回滚必须有发布窗口相关性和审批", "不要把所有发布后异常都归因于发布"],
        "dependency_timeout": ["不要在未确认依赖异常前重启业务服务", "变更网络配置前必须审批"],
        "db_pool_saturation": ["调整连接池或数据库参数前必须审批", "不要只看应用错误忽略 DB 指标"],
        "oom": ["重启 Pod 属于高风险动作，需审批", "不要在未确认 OOM 前直接扩容"],
    }
    return mapping.get(failure_mode, ["高风险修复动作必须先走审批"])


def _false_positives_for_failure_mode(failure_mode: str) -> list[str]:
    mapping = {
        "deploy_regression": ["发布窗口附近的独立依赖故障", "流量突增导致的非发布退化"],
        "dependency_timeout": ["客户端超时配置过低", "数据库慢查询伪装成上游超时"],
        "db_pool_saturation": ["上游网络抖动导致连接等待", "业务线程池耗尽误判为连接池问题"],
        "oom": ["探针失败导致重启但非内存问题", "节点驱逐导致的 Pod 重建"],
    }
    return mapping.get(failure_mode, [])
