from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None

from .knowledge import OpenAICompatEmbeddingClient
from .settings import Settings


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


@dataclass
class CaseCandidate:
    index: int
    score: float
    sparse_rank: int | None = None
    dense_rank: int | None = None
    sparse_score: float = 0.0
    dense_score: float = 0.0


class CaseMemoryStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.schema = settings.case_memory_schema
        self.table = settings.case_memory_table

    @property
    def enabled(self) -> bool:
        return bool(psycopg and dict_row and self.settings.pgvector_dsn)

    async def ensure_schema(self, vector_dim: int) -> None:
        if not self.enabled:
            raise RuntimeError("case memory store is not configured")
        async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
                await cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.schema}.{self.table} (
                        case_id TEXT PRIMARY KEY,
                        service TEXT NOT NULL DEFAULT '',
                        cluster TEXT NOT NULL DEFAULT '',
                        namespace TEXT NOT NULL DEFAULT '',
                        failure_mode TEXT NOT NULL DEFAULT '',
                        root_cause_taxonomy TEXT NOT NULL DEFAULT '',
                        signal_pattern TEXT NOT NULL DEFAULT '',
                        action_pattern TEXT NOT NULL DEFAULT '',
                        symptom TEXT NOT NULL DEFAULT '',
                        root_cause TEXT NOT NULL DEFAULT '',
                        key_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
                        final_action TEXT NOT NULL DEFAULT '',
                        final_conclusion TEXT NOT NULL DEFAULT '',
                        human_verified BOOLEAN NOT NULL DEFAULT FALSE,
                        content_checksum TEXT NOT NULL DEFAULT '',
                        source_version TEXT NOT NULL DEFAULT '',
                        document_text TEXT NOT NULL,
                        embedding_model TEXT NOT NULL,
                        embedding VECTOR({vector_dim}) NOT NULL,
                        indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.table}_embedding_hnsw_idx "
                    f"ON {self.schema}.{self.table} USING hnsw (embedding vector_cosine_ops)"
                )
                await cur.execute(
                    f"ALTER TABLE {self.schema}.{self.table} ADD COLUMN IF NOT EXISTS content_checksum TEXT NOT NULL DEFAULT ''"
                )
                await cur.execute(
                    f"ALTER TABLE {self.schema}.{self.table} ADD COLUMN IF NOT EXISTS source_version TEXT NOT NULL DEFAULT ''"
                )
            await conn.commit()

    async def upsert_case(
        self,
        *,
        case: Dict[str, Any],
        document_text: str,
        embedding_model: str,
        embedding: Sequence[float],
        content_checksum: str,
        source_version: str,
    ) -> None:
        if not self.enabled:
            raise RuntimeError("case memory store is not configured")
        await self.ensure_schema(len(embedding))
        async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    INSERT INTO {self.schema}.{self.table} (
                        case_id, service, cluster, namespace, failure_mode, root_cause_taxonomy,
                        signal_pattern, action_pattern, symptom, root_cause, key_evidence,
                        final_action, final_conclusion, human_verified, content_checksum, source_version, document_text,
                        embedding_model, embedding, indexed_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s::vector, NOW(), NOW()
                    )
                    ON CONFLICT (case_id) DO UPDATE SET
                        service = EXCLUDED.service,
                        cluster = EXCLUDED.cluster,
                        namespace = EXCLUDED.namespace,
                        failure_mode = EXCLUDED.failure_mode,
                        root_cause_taxonomy = EXCLUDED.root_cause_taxonomy,
                        signal_pattern = EXCLUDED.signal_pattern,
                        action_pattern = EXCLUDED.action_pattern,
                        symptom = EXCLUDED.symptom,
                        root_cause = EXCLUDED.root_cause,
                        key_evidence = EXCLUDED.key_evidence,
                        final_action = EXCLUDED.final_action,
                        final_conclusion = EXCLUDED.final_conclusion,
                        human_verified = EXCLUDED.human_verified,
                        content_checksum = EXCLUDED.content_checksum,
                        source_version = EXCLUDED.source_version,
                        document_text = EXCLUDED.document_text,
                        embedding_model = EXCLUDED.embedding_model,
                        embedding = EXCLUDED.embedding,
                        updated_at = NOW()
                    """,
                    (
                        str(case.get("case_id") or ""),
                        str(case.get("service") or ""),
                        str(case.get("cluster") or ""),
                        str(case.get("namespace") or ""),
                        str(case.get("failure_mode") or ""),
                        str(case.get("root_cause_taxonomy") or ""),
                        str(case.get("signal_pattern") or ""),
                        str(case.get("action_pattern") or ""),
                        str(case.get("symptom") or ""),
                        str(case.get("root_cause") or ""),
                        json.dumps(list(case.get("key_evidence") or []), ensure_ascii=False),
                        str(case.get("final_action") or ""),
                        str(case.get("final_conclusion") or ""),
                        bool(case.get("human_verified")),
                        str(content_checksum or ""),
                        str(source_version or ""),
                        document_text,
                        embedding_model,
                        self._vector_literal(embedding),
                    ),
                )
            await conn.commit()

    async def list_cases(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn, row_factory=dict_row) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT case_id, service, cluster, namespace, failure_mode, root_cause_taxonomy,
                               signal_pattern, action_pattern, symptom, root_cause, key_evidence,
                               final_action, final_conclusion, human_verified, content_checksum, source_version, document_text, indexed_at
                        FROM {self.schema}.{self.table}
                        ORDER BY updated_at DESC, indexed_at DESC, case_id DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                    rows = await cur.fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []

    async def semantic_search(
        self,
        *,
        query_embedding: Sequence[float],
        exclude_case_ids: Sequence[str] | None = None,
        limit: int = 6,
    ) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        conditions = ["1 = 1"]
        params: List[Any] = []
        if exclude_case_ids:
            conditions.append("case_id <> ALL(%s)")
            params.append(list(exclude_case_ids))
        vector_literal = self._vector_literal(query_embedding)
        async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn, row_factory=dict_row) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT case_id, service, cluster, namespace, failure_mode, root_cause_taxonomy,
                           signal_pattern, action_pattern, symptom, root_cause, final_action,
                           final_conclusion, human_verified, document_text,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM {self.schema}.{self.table}
                    WHERE {' AND '.join(conditions)}
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    tuple([vector_literal, *params, vector_literal, limit]),
                )
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def count(self) -> int:
        if not self.enabled:
            return 0
        try:
            async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn, row_factory=dict_row) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"SELECT COUNT(*) AS count FROM {self.schema}.{self.table}")
                    row = await cur.fetchone()
            return int(row["count"] if row else 0)
        except Exception:
            return 0

    async def get_case_meta(self, case_id: str) -> Dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn, row_factory=dict_row) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT case_id, content_checksum, source_version, embedding_model
                        FROM {self.schema}.{self.table}
                        WHERE case_id = %s
                        """,
                        (case_id,),
                    )
                    row = await cur.fetchone()
            return None if row is None else dict(row)
        except Exception:
            return None

    @staticmethod
    def _vector_literal(vector: Sequence[float]) -> str:
        return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


class CaseMemoryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = CaseMemoryStore(settings)
        self.embedding_client = OpenAICompatEmbeddingClient(settings)

    @property
    def enabled(self) -> bool:
        return bool(self.store.enabled and self.embedding_client.enabled)

    async def status(self) -> Dict[str, Any]:
        return {
            "ready": self.enabled,
            "schema_name": self.settings.case_memory_schema,
            "table": self.settings.case_memory_table,
            "indexed_cases": await self.store.count(),
            "embedding_enabled": self.embedding_client.enabled,
            "embedding_model": self.settings.embedding_model,
            "vector_backend": "pgvector",
        }

    async def sync_cases(self, cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("case memory service is not configured")
        indexed = 0
        skipped = 0
        for case in cases:
            case_dict = dict(case)
            if not str(case_dict.get("case_id") or "").strip():
                skipped += 1
                continue
            content_checksum = str(case_dict.get("content_checksum") or "")
            source_version = str(case_dict.get("source_version") or "")
            existing = await self.store.get_case_meta(str(case_dict.get("case_id") or ""))
            if (
                existing is not None
                and str(existing.get("content_checksum") or "") == content_checksum
                and str(existing.get("source_version") or "") == source_version
                and str(existing.get("embedding_model") or "") == self.settings.embedding_model
            ):
                skipped += 1
                continue
            document = self.build_case_document(case_dict)
            embeddings = await self.embedding_client.embed_texts([document])
            if not embeddings or not embeddings[0]:
                skipped += 1
                continue
            await self.store.upsert_case(
                case=case_dict,
                document_text=document,
                embedding_model=self.settings.embedding_model,
                embedding=embeddings[0],
                content_checksum=content_checksum,
                source_version=source_version,
            )
            indexed += 1
        return {
            "status": "ok",
            "indexed_cases": indexed,
            "skipped_cases": skipped,
        }

    async def search(
        self,
        *,
        query: str,
        service: str = "",
        cluster: str = "",
        namespace: str = "",
        failure_mode: str = "",
        root_cause_taxonomy: str = "",
        exclude_case_ids: Sequence[str] | None = None,
        top_k: int | None = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("case memory service is not configured")

        target_top_k = max(1, top_k or self.settings.case_memory_top_k)
        inferred_failure_mode = failure_mode or infer_failure_mode(query)
        inferred_taxonomy = root_cause_taxonomy or infer_root_cause_taxonomy(query)

        exact_cases = await self._recall_exact(
            query=query,
            service=service,
            cluster=cluster,
            namespace=namespace,
            failure_mode=inferred_failure_mode,
            root_cause_taxonomy=inferred_taxonomy,
            exclude_case_ids=exclude_case_ids or [],
            limit=min(2, target_top_k),
        )
        base_excluded = list(exclude_case_ids or [])
        pattern_cases = await self._recall_pattern(
            service=service,
            failure_mode=inferred_failure_mode,
            root_cause_taxonomy=inferred_taxonomy,
            exclude_case_ids=[*base_excluded, *(item["case_id"] for item in exact_cases)],
            limit=min(2, target_top_k),
        )
        semantic_cases = await self._recall_semantic_hybrid(
            query=query,
            service=service,
            exclude_case_ids=[*base_excluded, *(item["case_id"] for item in exact_cases)],
            limit=max(target_top_k, 4),
        )
        hits = self._merge_cases(exact_cases, pattern_cases, semantic_cases)
        hits.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return {
            "query": query,
            "hits": hits[:target_top_k],
            "index_info": {
                "retrieval_mode": "exact+pattern+semantic_hybrid",
                "indexed_cases": await self.store.count(),
                "embedding_enabled": self.embedding_client.enabled,
                "embedding_model": self.settings.embedding_model,
                "vector_backend": "pgvector",
            },
        }

    async def _recall_exact(
        self,
        *,
        query: str,
        service: str,
        cluster: str,
        namespace: str,
        failure_mode: str,
        root_cause_taxonomy: str,
        exclude_case_ids: Sequence[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        rows = await self.store.list_cases(limit=80)
        cases: List[Dict[str, Any]] = []
        excluded = set(exclude_case_ids)
        query_tokens = self._tokens(query)
        for row in rows:
            if str(row.get("case_id") or "") in excluded:
                continue
            if service and str(row.get("service") or "") != service:
                continue
            lexical = self._token_overlap(
                query_tokens,
                self._tokens(str(row.get("document_text") or self.build_case_document(row))),
            )
            pattern_score = 0.0
            if failure_mode and str(row.get("failure_mode") or "") == failure_mode:
                pattern_score += 0.35
            if root_cause_taxonomy and str(row.get("root_cause_taxonomy") or "") == root_cause_taxonomy:
                pattern_score += 0.18
            if pattern_score <= 0 and lexical < 0.12:
                continue
            score = 0.22 + pattern_score + min(lexical * 0.28, 0.18)
            if pattern_score > 0 or lexical >= 0.18:
                if cluster and str(row.get("cluster") or "") == cluster:
                    score += 0.04
                if namespace and str(row.get("namespace") or "") == namespace:
                    score += 0.03
            if bool(row.get("human_verified")):
                score += 0.05
            cases.append(self._to_hit(row, recall_source="exact", score=min(score, 0.76)))
        return sorted(cases, key=lambda item: item["score"], reverse=True)[:limit]

    async def _recall_pattern(
        self,
        *,
        service: str,
        failure_mode: str,
        root_cause_taxonomy: str,
        exclude_case_ids: Sequence[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        rows = await self.store.list_cases(limit=100)
        excluded = set(exclude_case_ids)
        cases: List[Dict[str, Any]] = []
        for row in rows:
            if str(row.get("case_id") or "") in excluded:
                continue
            score = 0.0
            if failure_mode and str(row.get("failure_mode") or "") == failure_mode:
                score += 0.55
            if root_cause_taxonomy and str(row.get("root_cause_taxonomy") or "") == root_cause_taxonomy:
                score += 0.25
            if service and str(row.get("service") or "") == service and score > 0:
                score += 0.08
            if bool(row.get("human_verified")):
                score += 0.05
            if score <= 0:
                continue
            cases.append(self._to_hit(row, recall_source="pattern", score=min(score, 0.88)))
        return sorted(cases, key=lambda item: item["score"], reverse=True)[:limit]

    async def _recall_semantic_hybrid(
        self,
        *,
        query: str,
        service: str,
        exclude_case_ids: Sequence[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        rows = await self.store.list_cases(limit=max(self.settings.rag_hybrid_candidate_limit, 80))
        excluded = set(exclude_case_ids)
        candidates = [row for row in rows if str(row.get("case_id") or "") not in excluded]
        if not candidates:
            return []
        query_embedding = (await self.embedding_client.embed_texts([query]))[0]
        dense_rows = await self.store.semantic_search(
            query_embedding=query_embedding,
            exclude_case_ids=exclude_case_ids,
            limit=max(self.settings.rag_dense_candidates, limit),
        )
        dense_by_case_id = {
            str(row.get("case_id") or ""): float(row.get("similarity") or 0.0)
            for row in dense_rows
        }
        sparse_scores = self._score_sparse(query, candidates)
        sparse_candidates = self._rank_sparse_candidates(sparse_scores)
        dense_scores = [max(0.0, dense_by_case_id.get(str(row.get("case_id") or ""), 0.0)) for row in candidates]
        dense_candidates = self._rank_dense_candidates(dense_scores)
        fused = self._prefuse_candidates(
            candidates=candidates,
            service=service,
            sparse_scores=sparse_scores,
            dense_scores=dense_scores,
            sparse_candidates=sparse_candidates,
            dense_candidates=dense_candidates,
        )
        return [
            self._to_hit(candidates[item.index], recall_source="semantic_hybrid", score=min(item.score, 0.82))
            for item in fused[:limit]
        ]

    def _score_sparse(self, query: str, candidates: List[Dict[str, Any]]) -> List[float]:
        query_tokens = self._tokens(query)
        scores: List[float] = []
        for row in candidates:
            case_tokens = self._tokens(str(row.get("document_text") or self.build_case_document(row)))
            scores.append(self._token_overlap(query_tokens, case_tokens))
        return scores

    def _rank_sparse_candidates(self, sparse_scores: Sequence[float]) -> List[int]:
        ranked = sorted(range(len(sparse_scores)), key=lambda index: sparse_scores[index], reverse=True)
        limit = max(self.settings.rag_sparse_candidates, self.settings.case_memory_top_k)
        return [index for index in ranked[:limit] if sparse_scores[index] > 0]

    def _rank_dense_candidates(self, dense_scores: Sequence[float]) -> List[int]:
        ranked = sorted(range(len(dense_scores)), key=lambda index: dense_scores[index], reverse=True)
        limit = max(self.settings.rag_dense_candidates, self.settings.case_memory_top_k)
        return [index for index in ranked[:limit] if dense_scores[index] > 0]

    def _prefuse_candidates(
        self,
        *,
        candidates: Sequence[Dict[str, Any]],
        service: str,
        sparse_scores: Sequence[float],
        dense_scores: Sequence[float],
        sparse_candidates: Sequence[int],
        dense_candidates: Sequence[int],
    ) -> List[CaseCandidate]:
        sparse_ranks = {index: rank + 1 for rank, index in enumerate(sparse_candidates)}
        dense_ranks = {index: rank + 1 for rank, index in enumerate(dense_candidates)}
        candidate_ids = list(dict.fromkeys([*sparse_candidates, *dense_candidates]))
        fused: List[CaseCandidate] = []
        for index in candidate_ids:
            score = 0.0
            sparse_rank = sparse_ranks.get(index)
            dense_rank = dense_ranks.get(index)
            if sparse_rank is not None:
                score += self.settings.rag_sparse_weight / (self.settings.rag_rrf_k + sparse_rank)
            if dense_rank is not None:
                score += self.settings.rag_dense_weight / (self.settings.rag_rrf_k + dense_rank)
            lexical_coverage = min(sparse_scores[index], 1.0)
            score += min(lexical_coverage * 0.20, 0.20)
            score += min(dense_scores[index] * 0.20, 0.20)
            if service and str(candidates[index].get("service") or "") == service and (
                lexical_coverage >= 0.12 or dense_scores[index] >= 0.55
            ):
                score += 0.08
            if bool(candidates[index].get("human_verified")):
                score += 0.04
            fused.append(
                CaseCandidate(
                    index=index,
                    score=score,
                    sparse_rank=sparse_rank,
                    dense_rank=dense_rank,
                    sparse_score=sparse_scores[index],
                    dense_score=dense_scores[index],
                )
            )
        fused.sort(key=lambda item: item.score, reverse=True)
        return fused[: self.settings.rag_hybrid_candidate_limit]

    @staticmethod
    def _merge_cases(*groups: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for group in groups:
            for item in group:
                case_id = str(item.get("case_id") or "")
                current = merged.get(case_id)
                if current is None:
                    merged[case_id] = dict(item)
                    continue
                parts = [part for part in str(current.get("recall_source") or "").split(",") if part]
                if item.get("recall_source") and item["recall_source"] not in parts:
                    parts.append(str(item["recall_source"]))
                current["recall_source"] = ",".join(parts)
                current["score"] = min(float(current.get("score") or 0.0) + float(item.get("score") or 0.0), 1.0)
        return list(merged.values())

    def build_case_document(self, case: Dict[str, Any]) -> str:
        evidence = "\n".join(f"- {item}" for item in list(case.get("key_evidence") or [])[:8])
        return (
            f"service: {case.get('service') or ''}\n"
            f"cluster: {case.get('cluster') or ''}\n"
            f"namespace: {case.get('namespace') or ''}\n"
            f"failure_mode: {case.get('failure_mode') or ''}\n"
            f"root_cause_taxonomy: {case.get('root_cause_taxonomy') or ''}\n"
            f"signal_pattern: {case.get('signal_pattern') or ''}\n"
            f"action_pattern: {case.get('action_pattern') or ''}\n"
            f"symptom: {case.get('symptom') or ''}\n"
            f"root_cause: {case.get('root_cause') or ''}\n"
            f"final_action: {case.get('final_action') or ''}\n"
            f"final_conclusion: {case.get('final_conclusion') or ''}\n"
            f"evidence:\n{evidence}"
        )

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token.strip().lower() for token in str(text or "").replace("\n", " ").split() if token.strip()}

    @staticmethod
    def _token_overlap(left: set[str], right: set[str]) -> float:
        union = len(left | right)
        return (len(left & right) / union) if union > 0 else 0.0

    @staticmethod
    def _to_hit(row: Dict[str, Any], *, recall_source: str, score: float) -> Dict[str, Any]:
        return {
            "case_id": str(row.get("case_id") or ""),
            "service": str(row.get("service") or ""),
            "cluster": str(row.get("cluster") or ""),
            "namespace": str(row.get("namespace") or ""),
            "failure_mode": str(row.get("failure_mode") or ""),
            "root_cause_taxonomy": str(row.get("root_cause_taxonomy") or ""),
            "signal_pattern": str(row.get("signal_pattern") or ""),
            "action_pattern": str(row.get("action_pattern") or ""),
            "symptom": str(row.get("symptom") or ""),
            "root_cause": str(row.get("root_cause") or ""),
            "final_action": str(row.get("final_action") or ""),
            "summary": str(row.get("final_conclusion") or ""),
            "human_verified": bool(row.get("human_verified")),
            "recall_source": recall_source,
            "score": round(float(score), 4),
        }
