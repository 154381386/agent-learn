from __future__ import annotations

from uuid import uuid4

from ..rag_client import RAGServiceClient
from ..schemas import TicketRequest
from ..settings import Settings
from .contracts import RoutingDecision, TaskEnvelope


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

    def route(self, request: TicketRequest) -> RoutingDecision:
        message = request.message.lower()
        hit_keywords = [keyword for keyword in self.cicd_keywords if keyword in message]
        if hit_keywords:
            return RoutingDecision(
                agent_name="cicd_agent",
                mode="router",
                reason=f"命中 CICD 关键词：{', '.join(hit_keywords[:3])}",
                confidence=0.88,
                candidate_agents=["cicd_agent", "general_sre_agent"],
            )

        return RoutingDecision(
            agent_name="general_sre_agent",
            mode="router",
            reason="未命中明确领域关键词，回退到通用 SRE Agent",
            confidence=0.42,
            candidate_agents=["general_sre_agent"],
        )

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
