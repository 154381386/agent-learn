from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from .memory_store import IncidentCaseStore
from .rag_client import RAGServiceClient
from .settings import Settings


logger = logging.getLogger(__name__)


class CaseVectorIndexer:
    def __init__(self, settings: Settings, incident_case_store: IncidentCaseStore, client: RAGServiceClient) -> None:
        self.settings = settings
        self.incident_case_store = incident_case_store
        self.client = client
        self.last_sync_metadata: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.rag_enabled)

    async def index_case(self, case: dict[str, Any]) -> None:
        if not self.enabled:
            self.last_sync_metadata = {"status": "skipped", "reason": "case_memory_disabled", "indexed_cases": 0}
            return
        not_ready_reason = self._not_ready_for_index_reason(case)
        if not_ready_reason:
            self.last_sync_metadata = {
                "status": "skipped",
                "reason": not_ready_reason,
                "case_id": str(case.get("case_id") or ""),
                "case_status": str(case.get("case_status") or ""),
                "human_verified": bool(case.get("human_verified")),
                "indexed_cases": 0,
            }
            return
        try:
            result = await self.client.case_memory_sync(cases=[self._to_sync_item(case)])
        except Exception as exc:
            logger.warning(
                "case_memory.index_case_failed case_id=%s error=%s",
                case.get("case_id"),
                exc,
            )
            self.last_sync_metadata = {
                "status": "error",
                "reason": "case_memory_sync_failed",
                "case_id": str(case.get("case_id") or ""),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "indexed_cases": 0,
            }
            return
        self.last_sync_metadata = {
            "status": "completed",
            "reason": "case_memory_sync_completed",
            "case_id": str(case.get("case_id") or ""),
            "indexed_cases": int(result.get("indexed_cases") or 0),
        }

    async def sync_all_cases(self, *, limit: int = 200) -> int:
        if not self.enabled:
            self.last_sync_metadata = {"status": "skipped", "reason": "case_memory_disabled", "indexed_cases": 0}
            return 0
        cases = self.incident_case_store.list_cases(case_status="verified", human_verified=True, limit=limit)
        cases = [case for case in cases if not self._not_ready_for_index_reason(case)]
        if not cases:
            self.last_sync_metadata = {"status": "skipped", "reason": "no_verified_cases", "indexed_cases": 0}
            return 0
        try:
            result = await self.client.case_memory_sync(cases=[self._to_sync_item(case) for case in cases])
        except Exception as exc:
            logger.warning("case_memory.sync_all_failed limit=%s error=%s", limit, exc)
            self.last_sync_metadata = {
                "status": "error",
                "reason": "case_memory_sync_failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "indexed_cases": 0,
                "case_count": len(cases),
            }
            return 0
        indexed_cases = int(result.get("indexed_cases") or 0)
        self.last_sync_metadata = {
            "status": "completed",
            "reason": "case_memory_sync_completed",
            "indexed_cases": indexed_cases,
            "case_count": len(cases),
        }
        return indexed_cases

    @staticmethod
    def _not_ready_for_index_reason(case: dict[str, Any]) -> str:
        if str(case.get("case_status") or "") != "verified":
            return "case_not_verified"
        if not bool(case.get("human_verified")):
            return "case_not_verified"
        return ""

    @staticmethod
    def _to_sync_item(case: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "case_id": str(case.get("case_id") or ""),
            "service": str(case.get("service") or ""),
            "cluster": str(case.get("cluster") or ""),
            "namespace": str(case.get("namespace") or ""),
            "case_status": str(case.get("case_status") or ""),
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
            "reviewed_by": str(case.get("reviewed_by") or ""),
            "reviewed_at": str(case.get("reviewed_at") or ""),
        }
        checksum_source = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        payload["content_checksum"] = hashlib.sha256(checksum_source.encode("utf-8")).hexdigest()
        payload["source_version"] = str(case.get("updated_at") or case.get("created_at") or "")
        return payload
