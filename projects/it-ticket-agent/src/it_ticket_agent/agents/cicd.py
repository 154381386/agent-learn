from __future__ import annotations

import json
import logging
from typing import List

from ..llm_client import OpenAICompatToolLLM
from ..mcp import MCPClient, MCPConnectionManager
from ..rag_client import RAGServiceClient
from ..runtime.contracts import AgentAction, AgentFinding, AgentResult, TaskEnvelope
from ..settings import Settings
from ..tools import (
    CheckPipelineStatusTool,
    CheckRecentDeploymentsTool,
    GetDeploymentStatusTool,
    SearchKnowledgeBaseTool,
)
from ..tools.contracts import ToolExecutionResult
from .base import BaseDomainAgent


logger = logging.getLogger(__name__)


class CICDAgent(BaseDomainAgent):
    name = "cicd_agent"
    domain = "cicd"

    @staticmethod
    def _ctx(task: TaskEnvelope) -> dict:
        execution_context = task.shared_context.get("execution_context")
        if isinstance(execution_context, dict):
            request_context = execution_context.get("request_context") or {}
            merged = dict(task.shared_context)
            merged.setdefault("message", request_context.get("message", ""))
            merged.setdefault("service", request_context.get("service", ""))
            merged.setdefault("cluster", request_context.get("cluster", "prod-shanghai-1"))
            merged.setdefault("namespace", request_context.get("namespace", "default"))
            merged.setdefault("channel", request_context.get("channel", "feishu"))
            return merged
        return task.shared_context

    def __init__(
        self,
        settings: Settings,
        knowledge_client: RAGServiceClient,
        connection_manager: MCPConnectionManager,
    ) -> None:
        self.knowledge_client = knowledge_client
        self.connection_manager = connection_manager
        self.llm = OpenAICompatToolLLM(settings)
        servers = self.connection_manager.servers_for_agent(self.name)
        self.mcp_client = MCPClient(servers[0]) if servers else None
        self.tools = [
            CheckRecentDeploymentsTool(self.mcp_client),
            CheckPipelineStatusTool(self.mcp_client),
            GetDeploymentStatusTool(self.mcp_client),
            SearchKnowledgeBaseTool(knowledge_client),
        ]

    async def run(self, task: TaskEnvelope) -> AgentResult:
        if self.llm.enabled:
            try:
                logger.info("cicd.run path=llm_loop ticket_id=%s", task.ticket_id)
                return await self._run_llm_loop(task)
            except Exception as exc:
                logger.warning(
                    "cicd.run llm_loop_failed ticket_id=%s error=%s",
                    task.ticket_id,
                    exc,
                )
                pass

        logger.info("cicd.run path=fallback ticket_id=%s", task.ticket_id)
        return await self._run_fallback(task)

    async def _run_fallback(self, task: TaskEnvelope) -> AgentResult:
        context = self._ctx(task)
        message = context.get("message", "")
        service = context.get("service", "unknown-service")
        mcp_servers = self.connection_manager.servers_for_agent(self.name)
        findings: List[AgentFinding] = []
        evidence: List[str] = []
        raw_refs: List[str] = []
        recommended_actions: List[AgentAction] = []

        tool_results = []
        for tool in self.tools:
            try:
                tool_result = await tool.run(task)
                logger.info(
                    "cicd.fallback tool=%s ticket_id=%s status=%s",
                    tool.name,
                    task.ticket_id,
                    tool_result.status,
                )
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
                        "environment": context.get("cluster", "prod-shanghai-1"),
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
            execution_path="fallback",
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

    async def _run_llm_loop(self, task: TaskEnvelope) -> AgentResult:
        context = self._ctx(task)
        message = context.get("message", "")
        service = context.get("service", "unknown-service")
        cluster = context.get("cluster", "prod-shanghai-1")
        mcp_servers = self.connection_manager.servers_for_agent(self.name)
        tools_by_name = {tool.name: tool for tool in self.tools}
        messages = [
            {
                "role": "system",
                "content": (
                    "你是企业内部的 CICD Agent。"
                    "你要诊断构建、流水线、发版和回滚相关问题。"
                    "请优先通过工具获取事实，再给出结论。"
                    "如果需要高风险动作，只能提出建议，不能直接声称已执行。"
                    "当信息足够时，请输出纯 JSON，字段必须包含："
                    "summary, findings, recommended_actions, risk_level, confidence, open_questions。"
                    "findings 是对象数组，recommended_actions 是对象数组。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "message": message,
                        "service": service,
                        "cluster": cluster,
                        "mcp_servers": mcp_servers,
                        "goal": task.goal,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        tool_results: List[ToolExecutionResult] = []
        for step in range(4):
            assistant_message = await self.llm.chat(
                messages=messages,
                tools=[tool.as_openai_tool() for tool in self.tools],
            )
            tool_calls = assistant_message.get("tool_calls") or []
            content = assistant_message.get("content") or ""
            logger.info(
                "cicd.llm_loop step=%s ticket_id=%s tool_calls=%s has_content=%s",
                step + 1,
                task.ticket_id,
                len(tool_calls),
                bool(content),
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                }
            )

            if tool_calls:
                for tool_call in tool_calls:
                    function = tool_call.get("function", {})
                    tool_name = function.get("name")
                    tool = tools_by_name.get(tool_name)
                    if tool is None:
                        continue
                    raw_arguments = function.get("arguments") or "{}"
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                    try:
                        tool_result = await tool.run(task, arguments=arguments)
                        logger.info(
                            "cicd.llm_loop tool=%s ticket_id=%s status=%s",
                            tool.name,
                            task.ticket_id,
                            tool_result.status,
                        )
                    except Exception as exc:
                        tool_result = ToolExecutionResult(
                            tool_name=tool.name,
                            status="failed",
                            summary=f"工具调用失败：{tool.name}",
                            payload={"error": str(exc)},
                            evidence=[str(exc)],
                        )
                        logger.warning(
                            "cicd.llm_loop tool_failed=%s ticket_id=%s error=%s",
                            tool.name,
                            task.ticket_id,
                            exc,
                        )
                    tool_results.append(tool_result)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "content": json.dumps(tool_result.model_dump(), ensure_ascii=False),
                        }
                    )
                continue

            payload = self.llm.extract_json(content)
            logger.info(
                "cicd.llm_loop completed ticket_id=%s tool_count=%s confidence=%s",
                task.ticket_id,
                len(tool_results),
                payload.get("confidence"),
            )
            return self._build_agent_result_from_llm(task, payload, tool_results, mcp_servers)

        raise ValueError("LLM tool loop exceeded max steps")

    def _build_agent_result_from_llm(
        self,
        task: TaskEnvelope,
        payload: dict,
        tool_results: List[ToolExecutionResult],
        mcp_servers: List[str],
    ) -> AgentResult:
        service = task.shared_context.get("service", "unknown-service")
        findings = [
            AgentFinding(
                title=item.get("title", "未命名发现"),
                detail=item.get("detail", ""),
                severity=item.get("severity", "info"),
            )
            for item in payload.get("findings", [])
            if isinstance(item, dict)
        ]
        actions = [
            AgentAction(
                action=item.get("action", "unknown_action"),
                risk=item.get("risk", "low"),
                reason=item.get("reason", ""),
                params=item.get("params", {}),
            )
            for item in payload.get("recommended_actions", [])
            if isinstance(item, dict)
        ]
        raw_refs = []
        evidence = []
        for tool_result in tool_results:
            evidence.extend(tool_result.evidence)
            for hit in tool_result.payload.get("hits", []):
                title = hit.get("title")
                if title:
                    raw_refs.append(title)
        raw_refs.extend(mcp_servers)

        if mcp_servers:
            findings.append(
                AgentFinding(
                    title="已发现 CICD MCP 连接",
                    detail=f"当前 CICD Agent 已绑定 MCP Servers：{', '.join(mcp_servers)}",
                    severity="info",
                )
            )

        return AgentResult(
            agent_name=self.name,
            domain=self.domain,
            status="completed",
            summary=payload.get(
                "summary",
                f"{service} 工单已进入 CICD 诊断，建议先检查最近部署、流水线状态和相关变更记录。",
            ),
            execution_path="llm_loop",
            findings=findings,
            evidence=evidence,
            tool_results=[tool_result.model_dump() for tool_result in tool_results],
            recommended_actions=actions,
            risk_level=payload.get("risk_level", "low"),
            confidence=float(payload.get("confidence", 0.65)),
            open_questions=payload.get("open_questions", []),
            needs_handoff=bool(payload.get("needs_handoff", False)),
            raw_refs=raw_refs,
        )

    @staticmethod
    def _should_request_rollback(message: str, pipeline_payload: dict, deployment_payload: dict) -> bool:
        has_release_signal = any(keyword in message for keyword in ["发版", "发布", "回滚"])
        pipeline_failed = pipeline_payload.get("status") == "failed"
        rollout_degraded = deployment_payload.get("rollout_status") == "degraded"
        return has_release_signal and pipeline_failed and rollout_degraded
