from __future__ import annotations

import json
import logging
from uuid import uuid4

from ..agents.descriptors import AgentRegistry, AgentRegistryEntry
from ..context import ContextAssembler, ExecutionContext
from ..llm_client import OpenAICompatToolLLM
from ..observability import get_observability
from ..rag_client import RAGServiceClient
from ..schemas import TicketRequest
from ..settings import Settings
from .contracts import RoutingDecision, TaskEnvelope


logger = logging.getLogger(__name__)

MULTI_AGENT_AMBIGUITY_KEYWORDS = (
    "502",
    "5xx",
    "超时",
    "timeout",
    "重启",
    "restart",
    "告警",
    "error",
    "错误",
)


class RuleBasedSupervisor:
    def __init__(self, settings: Settings, registry: AgentRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self.llm = OpenAICompatToolLLM(settings)
        self.context_assembler = ContextAssembler()
        self.routable_entries = registry.routable_entries()
        if not self.routable_entries:
            raise ValueError("at least one routable agent must be configured in registry")
        self.routable_by_name = {entry.descriptor.agent_name: entry for entry in self.routable_entries}
        self.default_entry = self._resolve_default_entry()

    async def route(self, request: TicketRequest) -> RoutingDecision:
        observability = get_observability()
        with observability.start_span(
            name="supervisor.route",
            as_type="span",
            input={
                "ticket_id": request.ticket_id,
                "message": request.message,
                "service": request.service,
                "candidate_agents": [entry.descriptor.agent_name for entry in self.routable_entries],
            },
            metadata={"routable_agent_count": len(self.routable_entries)},
        ) as span:
            message = request.message.lower()
            fan_out_candidates = self._match_fan_out_candidates(message)
            if fan_out_candidates is not None:
                primary_entry, candidate_entries, hit_keywords = fan_out_candidates
                candidate_names = [entry.descriptor.agent_name for entry in candidate_entries]
                logger.info(
                    "supervisor.route path=rule_fan_out ticket_id=%s primary=%s candidates=%s keywords=%s",
                    request.ticket_id,
                    primary_entry.descriptor.agent_name,
                    ",".join(candidate_names),
                    ",".join(hit_keywords[:4]),
                )
                decision = RoutingDecision(
                    agent_name=primary_entry.descriptor.agent_name,
                    mode="fan_out",
                    route_source="rule_fan_out",
                    reason=f"命中多个领域线索，进入并行分析：{', '.join(candidate_names)}",
                    confidence=0.91,
                    candidate_agents=candidate_names,
                )
                span.update(output=decision.model_dump())
                return decision

            keyword_hits = self._match_keywords(message)
            if keyword_hits is not None:
                entry, hit_keywords = keyword_hits
                logger.info(
                    "supervisor.route path=rule ticket_id=%s agent=%s keywords=%s",
                    request.ticket_id,
                    entry.descriptor.agent_name,
                    ",".join(hit_keywords[:3]),
                )
                decision = RoutingDecision(
                    agent_name=entry.descriptor.agent_name,
                    mode="router",
                    route_source="rule",
                    reason=f"命中 {entry.descriptor.display_name} 路由关键词：{', '.join(hit_keywords[:3])}",
                    confidence=0.88,
                    candidate_agents=[item.descriptor.agent_name for item in self.routable_entries],
                )
                span.update(output=decision.model_dump())
                return decision

            llm_decision = await self._route_with_llm(request)
            if llm_decision is not None:
                logger.info(
                    "supervisor.route path=llm ticket_id=%s agent=%s confidence=%.2f",
                    request.ticket_id,
                    llm_decision.agent_name,
                    llm_decision.confidence,
                )
                span.update(output=llm_decision.model_dump())
                return llm_decision

            logger.info(
                "supervisor.route path=fallback ticket_id=%s agent=%s",
                request.ticket_id,
                self.default_entry.descriptor.agent_name,
            )
            decision = RoutingDecision(
                agent_name=self.default_entry.descriptor.agent_name,
                mode="router",
                route_source="fallback",
                reason=f"未命中明确领域关键词，回退到 {self.default_entry.descriptor.display_name}",
                confidence=0.42,
                candidate_agents=[self.default_entry.descriptor.agent_name],
            )
            span.update(output=decision.model_dump())
            return decision

    def _resolve_default_entry(self) -> AgentRegistryEntry:
        for preferred in self.routable_entries:
            if preferred.descriptor.domain == "general":
                return preferred
        return self.routable_entries[0]

    def _match_keywords(self, message: str) -> tuple[AgentRegistryEntry, list[str]] | None:
        best_entry: AgentRegistryEntry | None = None
        best_hits: list[str] = []
        best_priority: int | None = None
        for entry in self.routable_entries:
            hits = [keyword for keyword in entry.descriptor.routing_keywords if keyword.lower() in message]
            if not hits:
                continue
            priority = entry.routing.priority
            if best_entry is None or len(hits) > len(best_hits) or (len(hits) == len(best_hits) and priority < (best_priority or priority)):
                best_entry = entry
                best_hits = hits
                best_priority = priority
        if best_entry is None:
            return None
        return best_entry, best_hits

    async def _route_with_llm(self, request: TicketRequest) -> RoutingDecision | None:
        if not self.llm.enabled:
            logger.info("supervisor.route llm_disabled ticket_id=%s", request.ticket_id)
            return None

        candidate_descriptors = [entry.descriptor for entry in self.routable_entries]
        candidate_names = [descriptor.agent_name for descriptor in candidate_descriptors]

        messages = [
            {
                "role": "system",
                "content": (
                    "你是企业 IT 工单系统中的 Supervisor Router。"
                    "请在提供的候选 agent 中选一个最合适的。"
                    "优先依据 domain、capabilities、routing_keywords 和描述来路由。"
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
                                "agent_name": descriptor.agent_name,
                                "domain": descriptor.domain,
                                "display_name": descriptor.display_name,
                                "description": descriptor.description,
                                "use_for": descriptor.capabilities,
                                "routing_keywords": descriptor.routing_keywords,
                                "required_fields": [field.name for field in descriptor.required_fields],
                            }
                            for descriptor in candidate_descriptors
                        ],
                        "output_schema": {
                            "agent_name": f"one of {candidate_names}",
                            "mode": "router or fan_out",
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

        if decision.agent_name not in set(candidate_names):
            logger.warning(
                "supervisor.route llm_invalid_agent ticket_id=%s agent=%s",
                request.ticket_id,
                decision.agent_name,
            )
            return None
        decision.candidate_agents = [name for name in list(decision.candidate_agents or []) if name in set(candidate_names)]
        if decision.mode == "fan_out":
            normalized = []
            seen: set[str] = set()
            for name in [decision.agent_name, *decision.candidate_agents]:
                if name in set(candidate_names) and name not in seen:
                    normalized.append(name)
                    seen.add(name)
            if len(normalized) <= 1:
                decision.mode = "router"
                decision.candidate_agents = [decision.agent_name]
            else:
                decision.candidate_agents = normalized[:3]
        if not decision.route_source:
            decision.route_source = "llm"
        return decision

    def _match_fan_out_candidates(
        self,
        message: str,
    ) -> tuple[AgentRegistryEntry, list[AgentRegistryEntry], list[str]] | None:
        entry_hits: list[tuple[AgentRegistryEntry, list[str]]] = []
        for entry in self.routable_entries:
            hits = [keyword for keyword in entry.descriptor.routing_keywords if keyword.lower() in message]
            if hits:
                entry_hits.append((entry, hits))

        non_general_hits = [item for item in entry_hits if item[0].descriptor.domain != "general"]
        if len(non_general_hits) >= 2:
            sorted_hits = sorted(non_general_hits, key=lambda item: (item[0].routing.priority, -len(item[1]), item[0].descriptor.agent_name))
            candidates = [item[0] for item in sorted_hits[:2]]
            general_entry = self.routable_by_name.get(self.default_entry.descriptor.agent_name)
            if general_entry is not None and general_entry.descriptor.agent_name not in {entry.descriptor.agent_name for entry in candidates}:
                candidates.append(general_entry)
            primary = candidates[0]
            hit_keywords = []
            for _entry, hits in sorted_hits[:2]:
                for keyword in hits:
                    if keyword not in hit_keywords:
                        hit_keywords.append(keyword)
            return primary, candidates[:3], hit_keywords

        if len(non_general_hits) == 1 and any(token in message for token in MULTI_AGENT_AMBIGUITY_KEYWORDS):
            primary, hits = non_general_hits[0]
            general_entry = self.routable_by_name.get(self.default_entry.descriptor.agent_name)
            if general_entry is not None and general_entry.descriptor.agent_name != primary.descriptor.agent_name:
                return primary, [primary, general_entry], hits
        return None

    def build_task(
        self,
        request: TicketRequest,
        decision: RoutingDecision,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> TaskEnvelope:
        shared_context = (
            self.context_assembler.to_shared_context(execution_context)
            if execution_context is not None
            else {
                "ticket_id": request.ticket_id,
                "user_id": request.user_id,
                "message": request.message,
                "service": request.service or "",
                "cluster": request.cluster,
                "namespace": request.namespace,
                "channel": request.channel,
            }
        )
        return TaskEnvelope(
            task_id=f"task-{uuid4()}",
            ticket_id=request.ticket_id,
            goal="诊断并给出下一步建议",
            mode=decision.mode,
            shared_context=shared_context,
            upstream_findings=[],
            constraints={"timeout_sec": 15},
            priority="normal",
            allowed_actions=["summarize", "recommend_next_step"],
        )

    @staticmethod
    def knowledge_client(settings: Settings) -> RAGServiceClient:
        return RAGServiceClient(settings)
