from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ..schemas import TicketRequest
from ..state.models import RAGContextBundle
from .contracts import SmartRouterDecision


class QueryClassifierRules(BaseModel):
    direct_answer_score_threshold: float = 0.8
    action_keywords: list[str] = Field(default_factory=list)
    symptom_keywords: list[str] = Field(default_factory=list)
    question_keywords: list[str] = Field(default_factory=list)


class QueryClassifier:
    def __init__(self, rules: QueryClassifierRules | None = None) -> None:
        self.rules = rules or self._load_rules()

    def classify(
        self,
        request: TicketRequest,
        *,
        rag_context: RAGContextBundle | dict[str, Any] | None = None,
    ) -> SmartRouterDecision:
        bundle = self._normalize_rag_context(rag_context)
        message = str(request.message or "").lower()

        has_action_words = self._matches(message, self.rules.action_keywords)
        has_symptom_words = self._matches(message, self.rules.symptom_keywords)
        has_question_words = self._matches(message, self.rules.question_keywords)
        rag_score = self._top_rag_score(bundle)
        rag_can_answer = bool(bundle.should_respond_directly or bundle.direct_answer or rag_score >= self.rules.direct_answer_score_threshold)

        matched_signals: list[str] = []
        if has_action_words:
            matched_signals.append("action_keywords")
        if has_symptom_words:
            matched_signals.append("symptom_keywords")
        if has_question_words:
            matched_signals.append("question_keywords")
        if rag_can_answer:
            matched_signals.append("rag_high_confidence")

        if not has_action_words and not has_symptom_words and rag_can_answer:
            return SmartRouterDecision(
                intent="direct_answer",
                route_source="rule",
                reason="未命中排查信号，且 RAG 命中足够强，可直接回答。",
                confidence=max(rag_score, 0.8),
                matched_signals=matched_signals,
                rag_score=rag_score,
                should_respond_directly=True,
            )

        return SmartRouterDecision(
            intent="hypothesis_graph",
            route_source="rule",
            reason="命中排查/操作信号，或 RAG 不足以支撑直答，需要进入诊断主链路。",
            confidence=0.72 if (has_action_words or has_symptom_words) else 0.55,
            matched_signals=matched_signals,
            rag_score=rag_score,
            should_respond_directly=False,
        )

    @staticmethod
    def _matches(message: str, keywords: list[str]) -> bool:
        return any(str(keyword).lower() in message for keyword in keywords)

    @staticmethod
    def _normalize_rag_context(
        rag_context: RAGContextBundle | dict[str, Any] | None,
    ) -> RAGContextBundle:
        if isinstance(rag_context, RAGContextBundle):
            return rag_context
        if isinstance(rag_context, dict):
            return RAGContextBundle.model_validate(rag_context)
        return RAGContextBundle()

    @staticmethod
    def _top_rag_score(bundle: RAGContextBundle) -> float:
        hits = list(bundle.context or []) + list(bundle.hits or [])
        if not hits:
            return 0.0
        return max(float(getattr(hit, "score", 0.0) or 0.0) for hit in hits)

    @staticmethod
    def _load_rules() -> QueryClassifierRules:
        rules_path = Path(__file__).with_name("query_classifier_rules.yaml")
        with rules_path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        return QueryClassifierRules.model_validate(payload)
