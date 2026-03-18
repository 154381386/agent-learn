from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from .settings import Settings


class RAGServiceClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.rag_service_base_url.rstrip("/")

    async def search(
        self,
        query: str,
        service: str = "",
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not self.settings.rag_enabled:
            return {
                "query": query,
                "query_type": "disabled",
                "should_respond_directly": False,
                "direct_answer": None,
                "hits": [],
                "context": [],
                "citations": [],
                "facts": [],
                "index_info": {"ready": False, "vector_backend": "remote-http", "disabled": True},
            }

        payload: Dict[str, Any] = {"query": query, "service": service or ""}
        if top_k is not None:
            payload["top_k"] = top_k
        return await self._request("POST", "/api/v1/rag/search", json=payload)

    async def status(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/rag/status")

    async def sync(self) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/rag/sync")

    async def reindex(self) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/rag/reindex")

    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.settings.rag_service_timeout_sec) as client:
            response = await client.request(method, f"{self.base_url}{path}", json=json)
            response.raise_for_status()
            return response.json()
