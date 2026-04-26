from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from .adapters.rag_adapter import disabled_rag_search_payload, normalize_rag_search_payload
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
            return disabled_rag_search_payload(query)

        payload: Dict[str, Any] = {"query": query, "service": service or ""}
        if top_k is not None:
            payload["top_k"] = top_k
        response = await self._request("POST", "/api/v1/rag/search", json=payload)
        return normalize_rag_search_payload(response, query=query)

    async def status(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/rag/status")

    async def sync(self) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/rag/sync")

    async def reindex(self) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/rag/reindex")

    async def case_memory_search(
        self,
        *,
        query: str,
        service: str = "",
        cluster: str = "",
        namespace: str = "",
        failure_mode: str = "",
        root_cause_taxonomy: str = "",
        exclude_case_ids: list[str] | None = None,
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "query": query,
            "service": service,
            "cluster": cluster,
            "namespace": namespace,
            "failure_mode": failure_mode,
            "root_cause_taxonomy": root_cause_taxonomy,
            "exclude_case_ids": list(exclude_case_ids or []),
        }
        if top_k is not None:
            payload["top_k"] = top_k
        return await self._request("POST", "/api/v1/case-memory/search", json=payload)

    async def case_memory_sync(self, *, cases: list[Dict[str, Any]]) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/case-memory/sync", json={"cases": cases})

    async def case_memory_status(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/case-memory/status")

    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.settings.rag_service_timeout_sec, trust_env=False) as client:
            response = await client.request(method, f"{self.base_url}{path}", json=json)
            response.raise_for_status()
            return response.json()
