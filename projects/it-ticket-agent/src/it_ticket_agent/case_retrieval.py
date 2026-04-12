from __future__ import annotations

from .rag_client import RAGServiceClient
from .settings import Settings
from .state.models import SimilarIncidentCase


def infer_failure_mode(message: str) -> str:
    lowered = str(message or "").lower()
    if any(token in lowered for token in ["oom", "outofmemory", "内存", "heap", "oomkilled"]):
        return "oom"
    if any(token in lowered for token in ["timeout", "超时", "502", "503", "504", "gateway", "ingress"]):
        return "dependency_timeout"
    if any(token in lowered for token in ["慢查询", "连接池", "db", "database", "mysql", "postgres"]):
        return "db_pool_saturation"
    if any(token in lowered for token in ["发布", "deploy", "pipeline", "回滚", "release"]):
        return "deploy_regression"
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

    async def recall(
        self,
        *,
        service: str,
        cluster: str,
        namespace: str,
        message: str,
        session_id: str,
        limit: int = 6,
    ) -> list[SimilarIncidentCase]:
        if not self.settings.rag_enabled:
            return []
        response = await self.client.case_memory_search(
            query=message,
            service=service,
            cluster=cluster,
            namespace=namespace,
            failure_mode=infer_failure_mode(message),
            root_cause_taxonomy=infer_root_cause_taxonomy(message),
            exclude_case_ids=[],
            top_k=limit,
        )
        hits = list(response.get("hits") or [])
        return [self._to_similar_case(item) for item in hits]

    @staticmethod
    def _to_similar_case(row: dict) -> SimilarIncidentCase:
        return SimilarIncidentCase(
            case_id=str(row.get("case_id") or ""),
            service=str(row.get("service") or ""),
            symptom=str(row.get("symptom") or ""),
            root_cause=str(row.get("root_cause") or ""),
            resolution_summary=str(row.get("summary") or ""),
            success_rate=0.9 if bool(row.get("human_verified")) else 0.6,
            human_verified=bool(row.get("human_verified")),
            root_cause_confirmed=bool(row.get("human_verified")),
            failure_mode=str(row.get("failure_mode") or ""),
            root_cause_taxonomy=str(row.get("root_cause_taxonomy") or ""),
            signal_pattern=str(row.get("signal_pattern") or ""),
            action_pattern=str(row.get("action_pattern") or ""),
            recall_source=str(row.get("recall_source") or ""),
            recall_score=round(float(row.get("score") or 0.0), 4),
        )
