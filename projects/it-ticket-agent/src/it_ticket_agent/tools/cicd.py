from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from ..mcp import MCPClient
from ..rag_client import RAGServiceClient
from ..runtime.contracts import TaskEnvelope
from .contracts import BaseTool, ToolExecutionResult


SERVICE_ALIASES = {
    "车云服务": "车云服务",
    "car-cloud-service": "车云服务",
    "car-cloud": "车云服务",
    "支付服务": "支付服务",
    "payment-service": "支付服务",
    "payment": "支付服务",
}


MOCK_SERVICE_PROFILES: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}

DEFAULT_MOCK_PROFILES_PATH = Path(__file__).resolve().parents[3] / "data" / "mock_tool_profiles.json"


def _context(task: TaskEnvelope, arguments: dict | None = None) -> dict[str, Any]:
    arguments = arguments or {}
    return {
        "message": arguments.get("query") or task.shared_context.get("message", ""),
        "service": arguments.get("service") or task.shared_context.get("service", "order-service"),
        "cluster": arguments.get("environment") or task.shared_context.get("cluster", "prod-shanghai-1"),
        "namespace": arguments.get("namespace") or task.shared_context.get("namespace", "default"),
    }


def _canonical_service_name(service: str) -> str:
    normalized = str(service or "").strip()
    if not normalized:
        return normalized
    return SERVICE_ALIASES.get(normalized.lower(), SERVICE_ALIASES.get(normalized, normalized))


def _load_env_mock_scenarios() -> dict[str, str]:
    raw = os.getenv("IT_TICKET_AGENT_MOCK_SCENARIOS", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        _canonical_service_name(str(service)): str(scenario).strip()
        for service, scenario in payload.items()
        if str(service).strip() and str(scenario).strip()
    }


def _load_mock_service_profiles() -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    raw_path = os.getenv("IT_TICKET_AGENT_MOCK_PROFILES_PATH", "").strip()
    profile_path = Path(raw_path) if raw_path else DEFAULT_MOCK_PROFILES_PATH
    if not profile_path.exists():
        return {}
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _resolve_mock_scenario(task: TaskEnvelope, service: str, arguments: dict | None = None) -> str | None:
    arguments = arguments or {}
    service_name = _canonical_service_name(service)
    inline = str(arguments.get("mock_scenario") or "").strip()
    if inline:
        return inline

    shared_context = task.shared_context if isinstance(task.shared_context, dict) else {}
    shared_service_map = shared_context.get("mock_scenarios")
    if isinstance(shared_service_map, dict):
        scenario = shared_service_map.get(service_name) or shared_service_map.get(service)
        if scenario:
            return str(scenario).strip()

    shared_global = str(shared_context.get("mock_scenario") or "").strip()
    if shared_global:
        return shared_global

    env_service_map = _load_env_mock_scenarios()
    if service_name in env_service_map:
        return env_service_map[service_name]

    env_global = os.getenv("IT_TICKET_AGENT_MOCK_SCENARIO", "").strip()
    return env_global or None


def _resolve_profile_mock(task: TaskEnvelope, tool_name: str, arguments: dict | None = None) -> ToolExecutionResult | None:
    ctx = _context(task, arguments)
    service_name = _canonical_service_name(ctx["service"])
    if not service_name:
        return None
    scenario = _resolve_mock_scenario(task, service_name, arguments)
    if not scenario:
        return None
    service_profiles = _load_mock_service_profiles().get(service_name, {})
    profile = service_profiles.get(str(scenario).strip())
    if not isinstance(profile, dict):
        return None
    payload = profile.get(tool_name)
    if not isinstance(payload, dict):
        return None
    return ToolExecutionResult(
        tool_name=tool_name,
        status=str(payload.get("status") or "completed"),
        summary=str(payload.get("summary") or f"{service_name} 已命中 {scenario} mock：{tool_name}"),
        payload=dict(payload.get("payload") or {}),
        evidence=[str(item) for item in payload.get("evidence", []) if item],
        risk=str(payload.get("risk") or "low"),
    )


def _match_any(message: str, keywords: list[str]) -> bool:
    normalized = message.lower()
    return any(keyword.lower() in normalized for keyword in keywords)


def _resolve_mock_result(task: TaskEnvelope, tool_name: str, arguments: dict | None = None) -> ToolExecutionResult | None:
    arguments = arguments or {}
    inline = arguments.get("mock_response")
    if isinstance(inline, dict):
        payload = inline
    else:
        shared = task.shared_context.get("mock_tool_responses", {})
        payload = shared.get(tool_name) if isinstance(shared, dict) else None

    if not isinstance(payload, dict):
        return _resolve_profile_mock(task, tool_name, arguments)

    return ToolExecutionResult(
        tool_name=tool_name,
        status=str(payload.get("status") or "completed"),
        summary=str(payload.get("summary") or f"已返回 mock 响应：{tool_name}"),
        payload=dict(payload.get("payload") or {}),
        evidence=[str(item) for item in payload.get("evidence", []) if item],
        risk=str(payload.get("risk") or "low"),
    )


class SearchKnowledgeBaseTool(BaseTool):
    name = "search_knowledge_base"
    summary = "Search deployment and incident knowledge context"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Question or symptom to search"},
            "service": {"type": "string", "description": "Target service name"},
        },
    }

    def __init__(self, knowledge_client: RAGServiceClient) -> None:
        self.knowledge_client = knowledge_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        arguments = arguments or {}
        message = arguments.get("query") or task.shared_context.get("message", "")
        service = arguments.get("service") or task.shared_context.get("service", "")
        shared_rag = task.shared_context.get("rag_context")
        if isinstance(shared_rag, dict) and (
            not arguments.get("query")
            or str(arguments.get("query") or "").strip() == str(shared_rag.get("query") or "").strip()
            or str(arguments.get("query") or "").strip() == str(task.shared_context.get("message") or "").strip()
        ):
            result = dict(shared_rag)
        else:
            try:
                result = await self.knowledge_client.search(query=message, service=service)
            except Exception:
                result = {"context": [], "citations": []}

        hits = list(result.get("context") or result.get("hits") or [])[:2]
        evidence = [
            f"知识库命中：{item.get('title', '未命名文档')} / {item.get('section', '摘要')}"
            for item in hits
        ]
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已检索部署与故障相关知识。",
            payload={"hits": hits, "citations": result.get("citations", [])},
            evidence=evidence,
        )


class CheckRecentDeploymentsTool(BaseTool):
    name = "check_recent_deployments"
    summary = "Check recent deployment and rollback signals"
    input_schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "state": {"type": "string", "description": "MR state", "default": "merged"},
        },
    }

    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        self.mcp_client = mcp_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        arguments = arguments or {}
        message = task.shared_context.get("message", "")
        service = arguments.get("service") or task.shared_context.get("service", "unknown-service")
        state = arguments.get("state", "merged")
        if self.mcp_client is not None:
            result = await self.mcp_client.call_tool(
                "gitlab.list_merge_requests",
                {"project": service or "order-service", "state": state},
            )
            payload = result.get("structuredContent", {})
            items = payload.get("items", [])
            evidence = [
                f"MR !{item.get('iid')}: {item.get('title', 'unknown')}"
                for item in items[:2]
            ]
            return ToolExecutionResult(
                tool_name=self.name,
                status="completed",
                summary=result.get("content", [{}])[0].get("text", "已查询最近 MR。"),
                payload=payload,
                evidence=evidence,
            )

        deployment_signals: List[str] = []

        if any(keyword in message for keyword in ["发版", "发布", "回滚"]):
            deployment_signals.append("工单内容指向近期发布或回滚事件")
        if service:
            deployment_signals.append(f"建议检查 {service} 最近一次部署记录与变更单")

        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已生成最近部署检查建议。",
            payload={"service": service, "signals": deployment_signals},
            evidence=deployment_signals,
        )


class CheckPipelineStatusTool(BaseTool):
    name = "check_pipeline_status"
    summary = "Check pipeline failure and build status signals"
    input_schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service or project name"},
            "pipeline_id": {"type": "integer", "description": "Pipeline id if known"},
        },
    }

    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        self.mcp_client = mcp_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        arguments = arguments or {}
        message = task.shared_context.get("message", "")
        service = arguments.get("service") or task.shared_context.get("service", "order-service")
        pipeline_id = arguments.get("pipeline_id", 582341)
        if self.mcp_client is not None:
            result = await self.mcp_client.call_tool(
                "gitlab.get_pipeline",
                {"project": service or "order-service", "pipeline_id": pipeline_id},
            )
            payload = result.get("structuredContent", {})
            evidence = [
                f"Pipeline 状态：{payload.get('status', 'unknown')}",
                f"失败阶段：{payload.get('failed_stage', 'unknown')}",
            ]
            return ToolExecutionResult(
                tool_name=self.name,
                status="completed",
                summary=result.get("content", [{}])[0].get("text", "已查询流水线状态。"),
                payload=payload,
                evidence=evidence,
            )

        evidence: List[str] = []
        status = "healthy"

        if any(keyword in message.lower() for keyword in ["pipeline", "jenkins", "gitlab"]):
            evidence.append("工单内容包含流水线平台关键词，建议检查最近失败任务和失败阶段")
            status = "needs_check"
        if any(keyword in message for keyword in ["构建", "流水线"]):
            evidence.append("工单内容包含构建或流水线线索，优先核查构建日志")
            status = "needs_check"

        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已生成流水线状态检查建议。",
            payload={"pipeline_status": status},
            evidence=evidence,
        )


class GetDeploymentStatusTool(BaseTool):
    name = "get_deployment_status"
    summary = "Check deployment rollout and active alerts"
    input_schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "environment": {"type": "string", "description": "Cluster or environment name"},
        },
    }

    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        self.mcp_client = mcp_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        arguments = arguments or {}
        service = arguments.get("service") or task.shared_context.get("service", "order-service")
        cluster = arguments.get("environment") or task.shared_context.get("cluster", "prod-shanghai-1")
        if self.mcp_client is not None:
            result = await self.mcp_client.call_tool(
                "cicd.get_deployment_status",
                {"service": service or "order-service", "environment": cluster},
            )
            payload = result.get("structuredContent", {})
            evidence = [
                f"Rollout 状态：{payload.get('rollout_status', 'unknown')}",
                f"活跃告警：{', '.join(payload.get('active_alerts', [])) or 'none'}",
            ]
            return ToolExecutionResult(
                tool_name=self.name,
                status="completed",
                summary=result.get("content", [{}])[0].get("text", "已查询发布状态。"),
                payload=payload,
                evidence=evidence,
            )

        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="当前未配置 MCP，无法查询真实发布状态。",
            payload={"service": service, "environment": cluster},
            evidence=["未配置 deployment status MCP source"],
        )


class CheckServiceHealthTool(BaseTool):
    name = "check_service_health"
    summary = "Check service health, replica readiness, and traffic symptom summary"
    input_schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "environment": {"type": "string", "description": "Cluster or environment name"},
        },
    }

    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        self.mcp_client = mcp_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        ctx = _context(task, arguments)
        message = ctx["message"]
        service = ctx["service"]
        cluster = ctx["cluster"]
        health_status = "healthy"
        replica_status = "all_ready"
        error_rate = 0.2
        p99_ms = 180
        impacted_endpoints: list[str] = []
        evidence = [f"{service} 当前使用 mock 健康检查路径"]

        if _match_any(message, ["重启", "5xx", "500", "超时", "timeout", "失败", "不可用"]):
            health_status = "degraded"
            replica_status = "partial_ready"
            error_rate = 4.8
            p99_ms = 1600
            impacted_endpoints = ["/api/orders", "/health/ready"]
            evidence.append("消息中包含故障症状，推断服务健康已降级")
        if _match_any(message, ["完全不可用", "全部失败", "severe", "崩了"]):
            health_status = "unhealthy"
            replica_status = "insufficient_replicas"
            error_rate = 18.5
            p99_ms = 4200
            impacted_endpoints = ["/api/orders", "/checkout", "/health/live"]
            evidence.append("消息中包含严重故障信号，推断服务处于不健康状态")

        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已生成服务健康检查摘要。",
            payload={
                "service": service,
                "environment": cluster,
                "health_status": health_status,
                "replica_status": replica_status,
                "error_rate_percent": error_rate,
                "p99_latency_ms": p99_ms,
                "impacted_endpoints": impacted_endpoints,
            },
            evidence=evidence,
        )


class CheckRecentAlertsTool(BaseTool):
    name = "check_recent_alerts"
    summary = "Check recent alert signals and summarize alert severity"
    input_schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "window_minutes": {"type": "integer", "description": "Lookback window in minutes", "default": 30},
        },
    }

    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        self.mcp_client = mcp_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        arguments = arguments or {}
        ctx = _context(task, arguments)
        message = ctx["message"]
        service = ctx["service"]
        window_minutes = int(arguments.get("window_minutes", 30) or 30)
        alerts: list[dict[str, Any]] = []

        if _match_any(message, ["告警", "报警", "error rate", "错误率", "超时", "latency", "延迟"]):
            alerts.append(
                {
                    "name": f"{service} high error rate",
                    "severity": "critical" if _match_any(message, ["5xx", "500", "错误率"]) else "high",
                    "status": "firing",
                }
            )
            alerts.append(
                {
                    "name": f"{service} p99 latency elevated",
                    "severity": "high",
                    "status": "firing",
                }
            )
        else:
            alerts.append(
                {
                    "name": f"{service} baseline availability",
                    "severity": "info",
                    "status": "stable",
                }
            )

        highest = alerts[0]["severity"] if alerts else "info"
        evidence = [f"最近 {window_minutes} 分钟 mock 告警条数：{len(alerts)}"]
        evidence.extend(f"{item['name']}={item['status']}" for item in alerts[:3])
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总最近告警信号。",
            payload={
                "service": service,
                "window_minutes": window_minutes,
                "alerts": alerts,
                "alert_count": len(alerts),
                "highest_severity": highest,
            },
            evidence=evidence,
        )


class CheckCanaryStatusTool(BaseTool):
    name = "check_canary_status"
    summary = "Check canary rollout status, traffic weight, and failing gates"
    input_schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "environment": {"type": "string", "description": "Cluster or environment name"},
        },
    }

    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        self.mcp_client = mcp_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        ctx = _context(task, arguments)
        message = ctx["message"]
        service = ctx["service"]
        cluster = ctx["cluster"]
        canary_status = "not_in_progress"
        traffic_weight = 0
        failing_checks: list[str] = []

        if _match_any(message, ["canary", "灰度", "分批发布", "金丝雀"]):
            canary_status = "running"
            traffic_weight = 20
            failing_checks = ["error_rate_guardrail"] if _match_any(message, ["失败", "异常", "error"]) else []
        if _match_any(message, ["回滚", "rollback"]):
            canary_status = "rollback_pending"
            traffic_weight = 10
            failing_checks = ["latency_guardrail", "manual_approval_gate"]

        evidence = [
            f"{service} canary 状态：{canary_status}",
            f"当前流量权重：{traffic_weight}%",
        ]
        if failing_checks:
            evidence.append(f"失败检查项：{', '.join(failing_checks)}")

        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已生成灰度发布状态摘要。",
            payload={
                "service": service,
                "environment": cluster,
                "canary_status": canary_status,
                "traffic_weight_percent": traffic_weight,
                "failing_checks": failing_checks,
            },
            evidence=evidence,
        )


class InspectBuildFailureLogsTool(BaseTool):
    name = "inspect_build_failure_logs"
    summary = "Inspect build or pipeline failure logs and summarize the likely failing stage"
    input_schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service or project name"},
            "pipeline_id": {"type": "integer", "description": "Pipeline id if known"},
        },
    }

    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        self.mcp_client = mcp_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        ctx = _context(task, arguments)
        message = ctx["message"]
        service = ctx["service"]
        failed_stage = "unknown"
        suspected_error = "需要查看真实流水线日志"
        snippets = ["mock log: no live pipeline log source configured"]

        if _match_any(message, ["构建", "build", "编译"]):
            failed_stage = "build"
            suspected_error = "依赖解析或编译失败"
            snippets = ["ERROR: package lock mismatch", "Build step exited with code 1"]
        elif _match_any(message, ["镜像", "image", "docker"]):
            failed_stage = "image"
            suspected_error = "镜像构建或推送失败"
            snippets = ["denied: requested access to the resource is denied"]
        elif _match_any(message, ["发布", "deploy", "rollout", "流水线"]):
            failed_stage = "deploy"
            suspected_error = "部署阶段健康检查未通过"
            snippets = ["rollout status: degraded", "readiness probe failed"]

        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已提取构建/流水线失败摘要。",
            payload={
                "service": service,
                "failed_stage": failed_stage,
                "suspected_error": suspected_error,
                "log_snippets": snippets,
            },
            evidence=[f"失败阶段：{failed_stage}", f"疑似原因：{suspected_error}"],
        )


class GetRollbackHistoryTool(BaseTool):
    name = "get_rollback_history"
    summary = "Check recent rollback history and last known stable revision"
    input_schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "limit": {"type": "integer", "description": "How many history entries to return", "default": 3},
        },
    }

    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        self.mcp_client = mcp_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        arguments = arguments or {}
        ctx = _context(task, arguments)
        message = ctx["message"]
        service = ctx["service"]
        limit = int(arguments.get("limit", 3) or 3)
        history = [
            {
                "revision": "release-2026.04.06.1",
                "result": "success",
                "reason": "发布后错误率上升",
            },
            {
                "revision": "release-2026.03.28.2",
                "result": "success",
                "reason": "灰度阶段健康检查失败",
            },
        ][:limit]
        recommended_revision = history[0]["revision"] if history else ""
        rollback_recommended = _match_any(message, ["回滚", "rollback", "发布失败", "错误率"])

        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总最近回滚历史。",
            payload={
                "service": service,
                "recent_rollbacks": history,
                "last_known_stable_revision": recommended_revision,
                "rollback_recommended": rollback_recommended,
            },
            evidence=[
                f"最近回滚记录数：{len(history)}",
                f"最近稳定版本：{recommended_revision or 'unknown'}",
            ],
        )


class GetGitCommitHistoryTool(BaseTool):
    name = "get_git_commit_history"
    summary = "Check recent git commit history and suspicious code changes"
    input_schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "limit": {"type": "integer", "description": "How many commits to return", "default": 5},
        },
    }

    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        self.mcp_client = mcp_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        arguments = arguments or {}
        ctx = _context(task, arguments)
        service = ctx["service"]
        limit = int(arguments.get("limit", 5) or 5)
        commits = [
            {"sha": "abc1234", "author": "alice", "message": f"{service} optimize deployment config", "minutes_ago": 45},
            {"sha": "def5678", "author": "bob", "message": f"{service} fix health probe path", "minutes_ago": 90},
        ][:limit]
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总最近 git 提交记录。",
            payload={"service": service, "commits": commits},
            evidence=[f"最近提交 {item['sha']}" for item in commits[:2]],
        )


class GetChangeRecordsTool(BaseTool):
    name = "get_change_records"
    summary = "Check recent deployment or configuration change records"
    input_schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "limit": {"type": "integer", "description": "How many change records to return", "default": 5},
        },
    }

    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        self.mcp_client = mcp_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        arguments = arguments or {}
        ctx = _context(task, arguments)
        service = ctx["service"]
        limit = int(arguments.get("limit", 5) or 5)
        changes = [
            {"change_id": "CHG-LOCAL-01", "type": "deploy", "status": "completed", "owner": "release-bot"},
            {"change_id": "CHG-LOCAL-02", "type": "config", "status": "completed", "owner": "sre"},
        ][:limit]
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总最近变更记录。",
            payload={"service": service, "changes": changes},
            evidence=[f"最近变更 {item['change_id']}" for item in changes[:2]],
        )


class CheckPodStatusTool(BaseTool):
    name = "check_pod_status"
    summary = "Check current pod status, readiness, and restart count"
    input_schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Target service name"},
            "namespace": {"type": "string", "description": "Target namespace"},
        },
    }

    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        self.mcp_client = mcp_client

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = _resolve_mock_result(task, self.name, arguments)
        if mocked is not None:
            return mocked

        ctx = _context(task, arguments)
        message = ctx["message"]
        service = ctx["service"]
        namespace = ctx["namespace"]
        pods = [
            {"name": f"{service}-pod-1", "status": "Running", "ready": True, "restarts": 0},
            {"name": f"{service}-pod-2", "status": "Running", "ready": True, "restarts": 0},
        ]
        if _match_any(message, ["pod", "探针", "重启", "crashloop", "故障", "失败"]):
            pods[1] = {"name": f"{service}-pod-2", "status": "CrashLoopBackOff", "ready": False, "restarts": 4}

        ready_replicas = len([pod for pod in pods if pod["ready"]])
        desired_replicas = len(pods)
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总当前 pod 状态。",
            payload={
                "service": service,
                "namespace": namespace,
                "pods": pods,
                "ready_replicas": ready_replicas,
                "desired_replicas": desired_replicas,
            },
            evidence=[f"ready {ready_replicas}/{desired_replicas}"],
        )
