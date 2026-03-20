from __future__ import annotations

from typing import List

from ..mcp import MCPClient, MCPConnectionManager
from ..rag_client import RAGServiceClient
from ..runtime.contracts import AgentAction, AgentFinding, AgentResult, TaskEnvelope
from ..tools import (
    CheckPipelineStatusTool,
    CheckRecentDeploymentsTool,
    GetDeploymentStatusTool,
    SearchKnowledgeBaseTool,
)
from ..tools.contracts import ToolExecutionResult
from .base import BaseDomainAgent


class CICDAgent(BaseDomainAgent):
    name = "cicd_agent"
    domain = "cicd"

    def __init__(self, knowledge_client: RAGServiceClient, connection_manager: MCPConnectionManager) -> None:
        self.knowledge_client = knowledge_client
        self.connection_manager = connection_manager
        servers = self.connection_manager.servers_for_agent(self.name)
        self.mcp_client = MCPClient(servers[0]) if servers else None
        self.tools = [
            CheckRecentDeploymentsTool(self.mcp_client),
            CheckPipelineStatusTool(self.mcp_client),
            GetDeploymentStatusTool(self.mcp_client),
            SearchKnowledgeBaseTool(knowledge_client),
        ]

    async def run(self, task: TaskEnvelope) -> AgentResult:
        message = task.shared_context.get("message", "")
        service = task.shared_context.get("service", "unknown-service")
        mcp_servers = self.connection_manager.servers_for_agent(self.name)
        findings: List[AgentFinding] = []
        evidence: List[str] = []
        raw_refs: List[str] = []
        recommended_actions: List[AgentAction] = []

        tool_results = []
        for tool in self.tools:
            try:
                tool_result = await tool.run(task)
            except Exception as exc:
                tool_result = ToolExecutionResult(
                    tool_name=tool.name,
                    status="failed",
                    summary=f"工具调用失败：{tool.name}",
                    payload={"error": str(exc)},
                    evidence=[str(exc)],
                    risk="low",
                )
            tool_results.append(tool_result)
            evidence.extend(tool_result.evidence)

        if any(keyword in message.lower() for keyword in ["deploy", "pipeline", "jenkins", "gitlab"]):
            findings.append(
                AgentFinding(
                    title="检测到 CICD 关键词",
                    detail="工单内容包含发布、流水线或构建相关关键词，优先进入 CICD 诊断链路。",
                    severity="info",
                )
            )
            evidence.append("命中 deploy/pipeline/jenkins/gitlab 关键词")

        if any(keyword in message for keyword in ["发版", "发布", "回滚", "构建", "流水线"]):
            findings.append(
                AgentFinding(
                    title="检测到发布变更线索",
                    detail="工单内容与近期发版或流水线执行有关，建议检查最近变更记录与流水线状态。",
                    severity="warning",
                )
            )
            evidence.append("命中 发版/发布/回滚/构建/流水线 关键词")
            recommended_actions.append(
                AgentAction(
                    action="gitlab.list_merge_requests",
                    risk="low",
                    reason="先确认最近部署和流水线状态，再决定是否需要执行回滚。",
                    params={"service": service},
                )
            )

        knowledge_hits = []
        pipeline_payload = {}
        deployment_payload = {}
        for tool_result in tool_results:
            knowledge_hits.extend(tool_result.payload.get("hits", []))
            if tool_result.tool_name == "check_pipeline_status":
                pipeline_payload = tool_result.payload
            if tool_result.tool_name == "get_deployment_status":
                deployment_payload = tool_result.payload

        if self._should_request_rollback(message, pipeline_payload, deployment_payload):
            previous_revision = deployment_payload.get("previous_revision", "release-previous")
            recommended_actions.append(
                AgentAction(
                    action="cicd.rollback_release",
                    risk="high",
                    reason="发布后流水线失败且部署状态降级，建议审批后执行回滚止血。",
                    params={
                        "service": service,
                        "environment": task.shared_context.get("cluster", "prod-shanghai-1"),
                        "target_revision": previous_revision,
                        "reason": "发布后故障，需要回滚止血",
                    },
                )
            )
            findings.append(
                AgentFinding(
                    title="建议进入回滚审批",
                    detail="已检测到高风险回滚建议，需人工审批后才能执行。",
                    severity="warning",
                )
            )

        for item in knowledge_hits[:2]:
            title = item.get("title") or "未命名文档"
            section = item.get("section") or "摘要"
            raw_refs.append(title)

        if mcp_servers:
            findings.append(
                AgentFinding(
                    title="已发现 CICD MCP 连接",
                    detail=f"当前 CICD Agent 已绑定 MCP Servers：{', '.join(mcp_servers)}",
                    severity="info",
                )
            )
            raw_refs.extend(mcp_servers)
            evidence.append(f"已加载 MCP 连接：{', '.join(mcp_servers)}")
        else:
            findings.append(
                AgentFinding(
                    title="当前使用本地工具骨架",
                    detail="尚未检测到 CICD MCP 连接配置，当前使用本地工具实现和 RAG 检索做最小闭环验证。",
                    severity="info",
                )
            )

        if not findings:
            findings.append(
                AgentFinding(
                    title="CICD 线索较弱",
                    detail="当前工单没有明显命中发版或流水线关键词，建议结合通用 SRE 诊断继续排查。",
                    severity="info",
                )
            )

        summary = f"{service} 工单已进入 CICD 诊断，建议先检查最近部署、流水线状态和相关变更记录。"
        return AgentResult(
            agent_name=self.name,
            domain=self.domain,
            status="completed",
            summary=summary,
            findings=findings,
            evidence=evidence,
            tool_results=[tool_result.model_dump() for tool_result in tool_results],
            recommended_actions=recommended_actions,
            risk_level="low",
            confidence=0.72,
            open_questions=["最近一次部署是否与故障时间重合？"],
            needs_handoff=False,
            raw_refs=raw_refs,
        )

    @staticmethod
    def _should_request_rollback(message: str, pipeline_payload: dict, deployment_payload: dict) -> bool:
        has_release_signal = any(keyword in message for keyword in ["发版", "发布", "回滚"])
        pipeline_failed = pipeline_payload.get("status") == "failed"
        rollout_degraded = deployment_payload.get("rollout_status") == "degraded"
        return has_release_signal and pipeline_failed and rollout_degraded
