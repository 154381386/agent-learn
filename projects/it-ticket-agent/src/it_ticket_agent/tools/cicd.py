from __future__ import annotations

from typing import List

from ..mcp import MCPClient
from ..rag_client import RAGServiceClient
from ..runtime.contracts import TaskEnvelope
from .contracts import BaseTool, ToolExecutionResult


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
        arguments = arguments or {}
        message = arguments.get("query") or task.shared_context.get("message", "")
        service = arguments.get("service") or task.shared_context.get("service", "")
        try:
            result = await self.knowledge_client.search(query=message, service=service)
        except Exception:
            result = {"context": [], "citations": []}

        hits = result.get("context", [])[:2]
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
