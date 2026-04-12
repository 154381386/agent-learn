from __future__ import annotations

import hashlib
import json
from typing import Any

from .memory_store import IncidentCaseStore
from .rag_client import RAGServiceClient
from .settings import Settings


class CaseVectorIndexer:
    def __init__(self, settings: Settings, incident_case_store: IncidentCaseStore, client: RAGServiceClient) -> None:
        self.settings = settings
        self.incident_case_store = incident_case_store
        self.client = client

    @property
    def enabled(self) -> bool:
        return bool(self.settings.rag_enabled)

    async def index_case(self, case: dict[str, Any]) -> None:
        if not self.enabled:
            return
        await self.client.case_memory_sync(cases=[self._to_sync_item(case)])

    async def sync_all_cases(self, *, limit: int = 200) -> int:
        if not self.enabled:
            return 0
        cases = self.incident_case_store.list_cases(limit=limit)
        if not cases:
            return 0
        result = await self.client.case_memory_sync(cases=[self._to_sync_item(case) for case in cases])
        return int(result.get("indexed_cases") or 0)

    @staticmethod
    def _to_sync_item(case: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "case_id": str(case.get("case_id") or ""),
            "service": str(case.get("service") or ""),
            "cluster": str(case.get("cluster") or ""),
            "namespace": str(case.get("namespace") or ""),
            "failure_mode": str(case.get("failure_mode") or ""),
            "root_cause_taxonomy": str(case.get("root_cause_taxonomy") or ""),
            "signal_pattern": str(case.get("signal_pattern") or ""),
            "action_pattern": str(case.get("action_pattern") or ""),
            "symptom": str(case.get("symptom") or ""),
            "root_cause": str(case.get("root_cause") or ""),
            "key_evidence": list(case.get("key_evidence") or []),
            "final_action": str(case.get("final_action") or ""),
            "final_conclusion": str(case.get("final_conclusion") or ""),
            "human_verified": bool(case.get("human_verified")),
        }
        checksum_source = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        payload["content_checksum"] = hashlib.sha256(checksum_source.encode("utf-8")).hexdigest()
        payload["source_version"] = str(case.get("updated_at") or case.get("created_at") or "")
        return payload
