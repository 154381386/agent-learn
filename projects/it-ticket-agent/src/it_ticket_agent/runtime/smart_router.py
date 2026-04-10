from __future__ import annotations

import json
from typing import Any

from ..llm_client import OpenAICompatToolLLM
from ..schemas import TicketRequest
from ..settings import Settings
from ..state.models import RAGContextBundle
from .contracts import SmartRouterDecision
from .query_classifier import QueryClassifier


class SmartRouter:
    def __init__(
        self,
        settings: Settings,
        *,
        classifier: QueryClassifier | None = None,
        llm: OpenAICompatToolLLM | None = None,
    ) -> None:
        self.settings = settings
        self.classifier = classifier or QueryClassifier()
        self.llm = llm or OpenAICompatToolLLM(settings)

    def route(
        self,
        request: TicketRequest,
        *,
        rag_context: RAGContextBundle | dict[str, Any] | None = None,
    ) -> SmartRouterDecision:
        return self.classifier.classify(request, rag_context=rag_context)

    async def generate_direct_answer(
        self,
        request: TicketRequest,
        *,
        rag_context: RAGContextBundle | dict[str, Any] | None = None,
    ) -> str:
        bundle = self._normalize_rag_context(rag_context)
        if bundle.direct_answer:
            return str(bundle.direct_answer)
        if self.llm.enabled and list(bundle.context or bundle.hits):
            return await self._generate_with_llm(request, bundle)
        return self._generate_fallback(bundle)

    async def _generate_with_llm(self, request: TicketRequest, bundle: RAGContextBundle) -> str:
        context_items = list(bundle.context or bundle.hits)[:3]
        messages = [
            {
                "role": "system",
                "content": (
                    "你是企业 IT 助手。请只基于提供的知识片段回答用户问题。"
                    "回答简洁、直接，若知识不足请明确说明。不要编造。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": request.message,
                        "service": request.service,
                        "knowledge": [
                            {
                                "title": item.title,
                                "section": item.section,
                                "path": item.path,
                                "snippet": item.snippet,
                            }
                            for item in context_items
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = await self.llm.chat(messages)
        return str(response.get("content") or "").strip() or self._generate_fallback(bundle)

    @staticmethod
    def _generate_fallback(bundle: RAGContextBundle) -> str:
        snippets = [str(item.snippet or "").strip() for item in list(bundle.context or bundle.hits)[:2] if str(item.snippet or "").strip()]
        if snippets:
            return "\n\n".join(snippets)
        return "已识别为知识咨询，但当前知识库没有足够命中，暂时无法直接回答。"

    @staticmethod
    def _normalize_rag_context(
        rag_context: RAGContextBundle | dict[str, Any] | None,
    ) -> RAGContextBundle:
        if isinstance(rag_context, RAGContextBundle):
            return rag_context
        if isinstance(rag_context, dict):
            return RAGContextBundle.model_validate(rag_context)
        return RAGContextBundle()
