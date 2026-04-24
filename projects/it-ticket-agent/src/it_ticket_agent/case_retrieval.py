from __future__ import annotations

import logging
from typing import Any

from .rag_client import RAGServiceClient
from .settings import Settings
from .state.models import SimilarIncidentCase


logger = logging.getLogger(__name__)


def infer_failure_mode(message: str) -> str:
    lowered = str(message or "").lower()
    if any(token in lowered for token in ["oom", "outofmemory", "内存", "heap", "oomkilled"]):
        return "oom"
    if any(token in lowered for token in ["慢查询", "连接池", "db", "database", "mysql", "postgres"]):
        return "db_pool_saturation"
    if any(token in lowered for token in ["发布", "deploy", "pipeline", "回滚", "release"]):
        return "deploy_regression"
    if any(token in lowered for token in ["timeout", "超时", "502", "503", "504", "gateway", "ingress"]):
        return "dependency_timeout"
    return ""


def infer_root_cause_taxonomy(message: str) -> str:
    mapping = {
        "oom": "resource_exhaustion",
        "dependency_timeout": "network_path_instability",
        "db_pool_saturation": "database_degradation",
        "deploy_regression": "release_regression",
    }
    return mapping.get(infer_failure_mode(message), "")


class CaseRetriever:
    def __init__(self, client: RAGServiceClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self.last_recall_metadata: dict[str, Any] = {}

    async def recall(
        self,
        *,
        service: str,
        cluster: str,
        namespace: str,
        message: str,
        session_id: str,
        limit: int = 6,
        failure_mode: str = "",
        root_cause_taxonomy: str = "",
    ) -> list[SimilarIncidentCase]:
        normalized_failure_mode = failure_mode or infer_failure_mode(message)
        normalized_taxonomy = root_cause_taxonomy or infer_root_cause_taxonomy(message)
        self.last_recall_metadata = {
            "status": "started",
            "reason": "case_memory_search_started",
            "query": str(message or ""),
            "service": str(service or ""),
            "cluster": str(cluster or ""),
            "namespace": str(namespace or ""),
            "failure_mode": normalized_failure_mode,
            "root_cause_taxonomy": normalized_taxonomy,
            "top_k": limit,
        }
        if not self.settings.rag_enabled:
            self.last_recall_metadata.update(
                {
                    "status": "skipped",
                    "reason": "case_memory_disabled",
                    "hit_count": 0,
                }
            )
            return []
        try:
            response = await self.client.case_memory_search(
                query=message,
                service=service,
                cluster=cluster,
                namespace=namespace,
                failure_mode=normalized_failure_mode,
                root_cause_taxonomy=normalized_taxonomy,
                exclude_case_ids=[],
                top_k=limit,
            )
        except Exception as exc:
            logger.warning(
                "case_memory.recall_failed query=%s service=%s error=%s",
                message,
                service,
                exc,
            )
            self.last_recall_metadata.update(
                {
                    "status": "error",
                    "reason": "case_memory_search_failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "hit_count": 0,
                }
            )
            return []

        hits = [item for item in list(response.get("hits") or []) if isinstance(item, dict)]
        cases = [self._to_similar_case(item) for item in hits]
        self.last_recall_metadata.update(
            {
                "status": "completed",
                "reason": "case_memory_search_completed",
                "hit_count": len(cases),
                "index_info": dict(response.get("index_info") or {}) if isinstance(response, dict) else {},
            }
        )
        return cases

    @staticmethod
    def _to_similar_case(row: dict) -> SimilarIncidentCase:
        return SimilarIncidentCase(
            case_id=str(row.get("case_id") or ""),
            service=str(row.get("service") or ""),
            failure_mode=str(row.get("failure_mode") or ""),
            root_cause_taxonomy=str(row.get("root_cause_taxonomy") or ""),
            signal_pattern=str(row.get("signal_pattern") or ""),
            action_pattern=str(row.get("action_pattern") or ""),
            symptom=str(row.get("symptom") or ""),
            root_cause=str(row.get("root_cause") or ""),
            final_action=str(row.get("final_action") or ""),
            summary=str(row.get("summary") or row.get("final_conclusion") or ""),
            recall_source=str(row.get("recall_source") or ""),
            recall_score=round(float(row.get("score") or row.get("recall_score") or 0.0), 4),
        )
