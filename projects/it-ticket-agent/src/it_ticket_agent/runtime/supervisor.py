from __future__ import annotations

import json
import logging
from uuid import uuid4

from ..llm_client import OpenAICompatToolLLM
from ..rag_client import RAGServiceClient
from ..schemas import TicketRequest
from ..settings import Settings
from .contracts import RoutingDecision, TaskEnvelope


logger = logging.getLogger(__name__)


class RuleBasedSupervisor:
    cicd_keywords = {
        "发版",
        "发布",
        "回滚",
        "构建",
        "流水线",
        "build",
        "deploy",
        "pipeline",
        "jenkins",
        "gitlab",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.llm = OpenAICompatToolLLM(settings)

    async def route(self, request: TicketRequest) -> RoutingDecision:
        message = request.message.lower()
        hit_keywords = [keyword for keyword in self.cicd_keywords if keyword in message]
        if hit_keywords:
            logger.info(
                "supervisor.route path=rule ticket_id=%s agent=%s keywords=%s",
                request.ticket_id,
                "cicd_agent",
                ",".join(hit_keywords[:3]),
            )
            return RoutingDecision(
                agent_name="cicd_agent",
                mode="router",
                route_source="rule",
                reason=f"命中 CICD 关键词：{', '.join(hit_keywords[:3])}",
                confidence=0.88,
                candidate_agents=["cicd_agent", "general_sre_agent"],
            )

        llm_decision = await self._route_with_llm(request)
        if llm_decision is not None:
            logger.info(
                "supervisor.route path=llm ticket_id=%s agent=%s confidence=%.2f",
                request.ticket_id,
                llm_decision.agent_name,
                llm_decision.confidence,
            )
            return llm_decision

        logger.info(
            "supervisor.route path=fallback ticket_id=%s agent=%s",
            request.ticket_id,
            "general_sre_agent",
        )
        return RoutingDecision(
            agent_name="general_sre_agent",
            mode="router",
            route_source="fallback",
            reason="未命中明确领域关键词，回退到通用 SRE Agent",
            confidence=0.42,
            candidate_agents=["general_sre_agent"],
        )

    async def _route_with_llm(self, request: TicketRequest) -> RoutingDecision | None:
        if not self.llm.enabled:
            logger.info("supervisor.route llm_disabled ticket_id=%s", request.ticket_id)
            return None

        messages = [
            {
                "role": "system",
                "content": (
                    "你是企业 IT 工单系统中的 Supervisor Router。"
                    "请在 cicd_agent 和 general_sre_agent 两个候选里选一个最合适的。"
                    "如果问题涉及构建、流水线、发布、回滚、Jenkins、GitLab，优先选 cicd_agent。"
                    "输出纯 JSON，不要 markdown，不要解释。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "ticket_id": request.ticket_id,
                        "message": request.message,
                        "service": request.service,
                        "cluster": request.cluster,
                        "namespace": request.namespace,
                        "candidates": [
                            {
                                "agent_name": "cicd_agent",
                                "domain": "cicd",
                                "use_for": ["构建失败", "流水线异常", "发布问题", "回滚建议"],
                            },
                            {
                                "agent_name": "general_sre_agent",
                                "domain": "general",
                                "use_for": ["其他通用 SRE 问题", "上下文不足的问题"],
                            },
                        ],
                        "output_schema": {
                            "agent_name": "cicd_agent or general_sre_agent",
                            "mode": "router",
                            "reason": "string",
                            "confidence": "0~1 float",
                            "candidate_agents": ["agent-name"],
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            response = await self.llm.chat(messages)
            payload = self.llm.extract_json(response.get("content") or "")
            decision = RoutingDecision(**payload)
        except Exception as exc:
            logger.warning(
                "supervisor.route llm_error ticket_id=%s error=%s",
                request.ticket_id,
                exc,
            )
            return None

        if decision.agent_name not in {"cicd_agent", "general_sre_agent"}:
            logger.warning(
                "supervisor.route llm_invalid_agent ticket_id=%s agent=%s",
                request.ticket_id,
                decision.agent_name,
            )
            return None
        if not decision.route_source:
            decision.route_source = "llm"
        return decision

    def build_task(self, request: TicketRequest, decision: RoutingDecision) -> TaskEnvelope:
        return TaskEnvelope(
            task_id=f"task-{uuid4()}",
            ticket_id=request.ticket_id,
            goal="诊断并给出下一步建议",
            mode=decision.mode,
            shared_context={
                "ticket_id": request.ticket_id,
                "user_id": request.user_id,
                "message": request.message,
                "service": request.service or "",
                "cluster": request.cluster,
                "namespace": request.namespace,
                "channel": request.channel,
            },
            upstream_findings=[],
            constraints={"timeout_sec": 15},
            priority="normal",
            allowed_actions=["summarize", "recommend_next_step"],
        )

    @staticmethod
    def knowledge_client(settings: Settings) -> RAGServiceClient:
        return RAGServiceClient(settings)
