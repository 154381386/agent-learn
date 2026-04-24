from __future__ import annotations

import logging

from ..rag_client import RAGServiceClient
from ..schemas import TicketRequest
from ..state.models import KnowledgeHit, RAGContextBundle

logger = logging.getLogger(__name__)


class KnowledgeService:
    def __init__(
        self,
        client: RAGServiceClient,
        *,
        default_top_k: int = 4,
    ) -> None:
        self.client = client
        self.default_top_k = default_top_k

    async def retrieve_for_request(
        self,
        request: TicketRequest,
        *,
        top_k: int | None = None,
    ) -> RAGContextBundle:
        return await self.retrieve_query(
            query=str(request.message or "").strip(),
            service=str(request.service or ""),
            top_k=top_k,
        )

    async def retrieve_query(
        self,
        *,
        query: str,
        service: str = "",
        top_k: int | None = None,
    ) -> RAGContextBundle:
        if not query:
            return RAGContextBundle(
                query="",
                query_type="skipped",
                index_info={"ready": False, "skipped": True, "reason": "empty_query"},
            )

        try:
            payload = await self.client.search(
                query=query,
                service=service,
                top_k=top_k or self.default_top_k,
            )
            bundle = RAGContextBundle.model_validate(payload)
        except Exception as exc:
            logger.warning(
                "knowledge.retrieve_failed query=%s service=%s error=%s",
                query,
                service,
                exc,
            )
            return RAGContextBundle(
                query=query,
                query_type="error",
                index_info={"ready": False, "error": str(exc)},
                raw_response={"error": str(exc)},
            )

        if not bundle.citations:
            bundle.citations = [self._format_citation(hit) for hit in list(bundle.context or bundle.hits)[:3] if self._format_citation(hit)]
        return bundle

    @staticmethod
    def _format_citation(hit: KnowledgeHit) -> str:
        parts = [part for part in [hit.title, hit.section, hit.path] if str(part or "").strip()]
        return " / ".join(parts)
