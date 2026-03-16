from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx

from .pgvector_store import PgVectorStore
from .settings import Settings


logger = logging.getLogger(__name__)
TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9._/-]*")
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*)$")
MARKDOWN_FILES = ("*.md", "*.mdx")


@dataclass
class KnowledgeChunk:
    chunk_id: str
    doc_id: str
    path: str
    title: str
    section: str
    category: str
    text: str
    tokens: List[str]
    token_freq: Dict[str, int]
    header_tokens: List[str]
    length: int
    embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "KnowledgeChunk":
        return cls(**payload)


@dataclass
class RetrievedChunk:
    chunk_id: str
    title: str
    section: str
    path: str
    category: str
    score: float
    snippet: str
    text: str = field(repr=False)
    sparse_rank: Optional[int] = None
    dense_rank: Optional[int] = None
    rerank_rank: Optional[int] = None
    sparse_score: float = 0.0
    dense_score: float = 0.0
    rerank_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "title": self.title,
            "section": self.section,
            "path": self.path,
            "category": self.category,
            "score": round(self.score, 4),
            "snippet": self.snippet,
        }


class OpenAICompatEmbeddingClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.embedding_base_url
            and self.settings.embedding_api_key
            and self.settings.embedding_model
        )

    async def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        if not self.enabled:
            raise RuntimeError("embedding client is not configured")

        url = self.settings.embedding_base_url.rstrip("/") + "/embeddings"
        headers = {
            "Authorization": f"Bearer {self.settings.embedding_api_key}",
            "Content-Type": "application/json",
        }
        vectors: List[List[float]] = []
        batch_size = min(max(1, self.settings.embedding_batch_size), 10)

        async with httpx.AsyncClient(timeout=self.settings.embedding_timeout_sec) as client:
            for offset in range(0, len(texts), batch_size):
                batch = list(texts[offset : offset + batch_size])
                response = await client.post(
                    url,
                    headers=headers,
                    json={
                        "model": self.settings.embedding_model,
                        "input": batch,
                        "encoding_format": "float",
                    },
                )
                response.raise_for_status()
                data = response.json().get("data", [])
                ordered = sorted(data, key=lambda item: item.get("index", 0))
                vectors.extend([self._normalize_vector(item["embedding"]) for item in ordered])
        return vectors

    @staticmethod
    def _normalize_vector(values: Sequence[float]) -> List[float]:
        magnitude = math.sqrt(sum(value * value for value in values))
        if magnitude == 0:
            return [0.0 for _ in values]
        return [value / magnitude for value in values]


class DashScopeRerankClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.rerank_base_url
            and self.settings.rerank_api_key
            and self.settings.rerank_model
        )

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> Dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("rerank client is not configured")
        if not documents:
            return {"results": [], "request_id": None, "usage": {}}

        url = self._build_url()
        headers = {
            "Authorization": f"Bearer {self.settings.rerank_api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_payload(query=query, documents=documents, top_n=top_n)
        async with httpx.AsyncClient(timeout=self.settings.rerank_timeout_sec) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return {
            "results": data.get("output", {}).get("results", []),
            "request_id": data.get("request_id"),
            "usage": data.get("usage", {}),
        }

    def _build_url(self) -> str:
        base = self.settings.rerank_base_url.rstrip("/")
        if "/api/v1/services/rerank/text-rerank/text-rerank" in base:
            return base
        return base + "/api/v1/services/rerank/text-rerank/text-rerank"

    def _build_payload(self, query: str, documents: Sequence[str], top_n: int) -> Dict[str, Any]:
        model = self.settings.rerank_model
        payload: Dict[str, Any] = {
            "model": model,
            "input": {
                "query": query,
                "documents": list(documents),
            },
        }
        if model == "qwen3-rerank":
            payload["top_n"] = top_n
            payload["instruct"] = self.settings.rerank_instruct
            return payload
        payload["parameters"] = {
            "top_n": top_n,
            "return_documents": self.settings.rerank_return_documents,
        }
        return payload


class KnowledgeBase:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.project_root = Path(__file__).resolve().parents[2]
        self.docs_path = self._resolve_path(settings.rag_docs_path)
        self.index_dir = self._resolve_path(settings.rag_index_dir)
        self.index_file = self.index_dir / "index.json"
        self.embedding_client = OpenAICompatEmbeddingClient(settings)
        self.rerank_client = DashScopeRerankClient(settings)
        self.pgvector = PgVectorStore(settings)
        self._lock = asyncio.Lock()
        self._chunks: List[KnowledgeChunk] = []
        self._chunk_map: Dict[str, KnowledgeChunk] = {}
        self._chunk_index_by_id: Dict[str, int] = {}
        self._idf: Dict[str, float] = {}
        self._avgdl: float = 1.0
        self._documents: List[Dict[str, Any]] = []
        self._manifest: Dict[str, Any] = {}
        self._ready = False

    @property
    def use_pgvector(self) -> bool:
        return self.pgvector.enabled

    async def ensure_ready(self, force_reindex: bool = False) -> Dict[str, Any]:
        async with self._lock:
            if self._ready and not force_reindex:
                return self.status()

            if self.use_pgvector:
                if force_reindex or self.settings.rag_auto_reindex_on_boot:
                    result = await self._build_and_persist_index_pgvector(force=force_reindex)
                else:
                    result = await self._load_pgvector_snapshot()
                self._ready = True
                return result

            if not force_reindex and self.index_file.exists():
                self._load_index_local()
                if not self._index_is_stale() or not self.settings.rag_auto_reindex_on_boot:
                    self._ready = True
                    return self.status()

            result = await self._build_and_persist_index_local()
            self._ready = True
            return result

    async def reindex(self, force: bool = False) -> Dict[str, Any]:
        async with self._lock:
            if self.use_pgvector:
                result = await self._build_and_persist_index_pgvector(force=force)
            else:
                result = await self._build_and_persist_index_local(force=force)
            self._ready = True
            return result

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
                "index_info": self.status(),
            }

        await self.ensure_ready()

        query_type = self.classify_query(query)
        query_text = " ".join(part for part in [query, service] if part).strip() or query
        query_tokens = self._tokenize(query_text)
        target_top_k = max(1, top_k or self.settings.rag_top_k)

        dense_vector: Optional[List[float]] = None
        dense_enabled = bool(self._manifest.get("embedding_enabled") and self.embedding_client.enabled)
        dense_error: Optional[str] = None
        if dense_enabled:
            try:
                dense_vector = (await self.embedding_client.embed_texts([query_text]))[0]
            except Exception as exc:
                dense_error = str(exc)
                logger.warning("query embedding failed, fallback to sparse recall only: %s", exc)
                dense_enabled = False

        sparse_scores = [self._bm25_score(query_tokens, chunk) for chunk in self._chunks]
        dense_scores = [0.0 for _ in self._chunks]
        sparse_candidates = self._rank_sparse_candidates(sparse_scores)
        dense_candidates: List[int] = []
        if dense_enabled and dense_vector is not None:
            if self.use_pgvector:
                dense_rows = await self.pgvector.dense_search(
                    self._collection_id(),
                    self.settings.embedding_model,
                    dense_vector,
                    max(self.settings.rag_dense_candidates, self.settings.rag_top_k),
                )
                for row in dense_rows:
                    index = self._chunk_index_by_id.get(row["chunk_id"])
                    if index is None:
                        continue
                    dense_candidates.append(index)
                    dense_scores[index] = max(0.0, float(row.get("similarity", 0.0)))
            else:
                dense_scores = [
                    self._cosine_similarity(dense_vector, chunk.embedding) for chunk in self._chunks
                ]
                dense_candidates = self._rank_dense_candidates(dense_scores)

        prefused_candidates = self._prefuse_candidates(
            query_tokens=query_tokens,
            sparse_scores=sparse_scores,
            dense_scores=dense_scores,
            sparse_candidates=sparse_candidates,
            dense_candidates=dense_candidates,
            dense_enabled=dense_enabled,
        )

        rerank_error: Optional[str] = None
        rerank_request_id: Optional[str] = None
        rerank_used = False
        reranked_candidates = prefused_candidates
        if self.rerank_client.enabled and prefused_candidates:
            try:
                rerank_payload = await self.rerank_client.rerank(
                    query=query_text,
                    documents=[candidate.text for candidate in prefused_candidates],
                    top_n=min(self.settings.rerank_top_n, len(prefused_candidates)),
                )
                rerank_request_id = rerank_payload.get("request_id")
                reranked_candidates = self._apply_rerank(prefused_candidates, rerank_payload.get("results", []))
                rerank_used = True
            except Exception as exc:
                rerank_error = str(exc)
                logger.warning("rerank failed, fallback to prefused ranking: %s", exc)
                if not self.settings.rerank_fail_open:
                    raise

        final_hits = self._mmr_rerank(
            query_tokens=query_tokens,
            candidates=reranked_candidates,
            top_k=target_top_k,
        )

        top_score = final_hits[0].score if final_hits else 0.0
        score_margin = top_score - final_hits[1].score if len(final_hits) > 1 else top_score
        should_respond_directly = (
            bool(final_hits)
            and query_type in {"procedural", "knowledge_lookup", "access_request"}
            and top_score >= self.settings.rag_direct_answer_min_score
            and score_margin >= self.settings.rag_direct_answer_min_margin
        )

        direct_answer = self._build_direct_answer(final_hits) if should_respond_directly else None
        context = [hit.to_dict() for hit in final_hits]
        citations = [
            {
                "title": hit.title,
                "section": hit.section,
                "path": hit.path,
                "score": round(hit.score, 4),
            }
            for hit in final_hits
        ]
        facts = [
            f"知识库命中：《{hit.title}》{(' / ' + hit.section) if hit.section else ''}"
            for hit in final_hits[:2]
        ]
        index_info = self.status()
        index_info.update(
            {
                "retrieval_mode": self._retrieval_mode(dense_enabled, rerank_used),
                "query_dense_enabled": dense_enabled,
                "dense_error": dense_error,
                "rerank_enabled": self.rerank_client.enabled,
                "rerank_used": rerank_used,
                "rerank_error": rerank_error,
                "rerank_request_id": rerank_request_id,
                "sparse_candidate_pool": len(sparse_candidates),
                "dense_candidate_pool": len(dense_candidates),
                "prefused_candidate_pool": len(prefused_candidates),
                "result_count": len(final_hits),
            }
        )
        return {
            "query": query,
            "query_type": query_type,
            "should_respond_directly": should_respond_directly,
            "direct_answer": direct_answer,
            "hits": context,
            "context": context,
            "citations": citations,
            "facts": facts,
            "index_info": index_info,
        }

    def status(self) -> Dict[str, Any]:
        first_embedding = self._chunks[0].embedding if self._chunks and self._chunks[0].embedding else None
        base = {
            "ready": self._ready,
            "docs_path": str(self.docs_path),
            "documents": len(self._documents),
            "chunks": len(self._chunks),
            "embedding_enabled": bool(self._manifest.get("embedding_enabled")),
            "embedding_dimension": self._manifest.get("embedding_dimension") or (len(first_embedding) if first_embedding else 0),
            "embedding_model": self.settings.embedding_model,
            "embedding_base_url": self.settings.embedding_base_url,
            "rerank_configured": self.rerank_client.enabled,
            "rerank_model": self.settings.rerank_model,
            "rerank_base_url": self.settings.rerank_base_url,
            "built_at": self._manifest.get("built_at"),
            "source_signature": self._manifest.get("source_signature", ""),
            "hybrid_strategy": "sparse+dense recall -> rerank -> mmr",
            "embedding_error": self._manifest.get("embedding_error"),
            "vector_backend": "pgvector" if self.use_pgvector else "local-json",
        }
        if self.use_pgvector:
            base["index_path"] = f"pgvector://{self.settings.pgvector_schema}.{self.settings.pgvector_chunks_table}"
            base["collection_id"] = self._collection_id()
        else:
            base["index_path"] = str(self.index_file)
        return base

    @staticmethod
    def classify_query(text: str) -> str:
        normalized = text.lower()
        if KnowledgeBase.is_realtime_incident(normalized):
            return "incident"

        procedural_keywords = [
            "如何",
            "怎么",
            "文档",
            "接入",
            "配置",
            "步骤",
            "流程",
            "指引",
            "手册",
            "sop",
            "guide",
            "readme",
        ]
        access_keywords = ["权限", "开通", "申请", "配额", "账号", "access"]
        if any(keyword in normalized for keyword in access_keywords):
            return "access_request"
        if any(keyword in normalized for keyword in procedural_keywords):
            return "procedural"
        return "knowledge_lookup"

    @staticmethod
    def is_realtime_incident(text: str) -> bool:
        incident_keywords = [
            "重启",
            "异常",
            "超时",
            "挂了",
            "故障",
            "不可用",
            "告警",
            "crashloop",
            "oom",
            "oomkilled",
            "latency",
            "error",
            "5xx",
        ]
        return any(keyword in text for keyword in incident_keywords)

    def _resolve_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return (self.project_root / path).resolve()

    def _collection_id(self) -> str:
        raw = f"{self.docs_path}:{self.settings.pgvector_schema}:{self.settings.embedding_model}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def _chunking_signature(self) -> str:
        return f"size={self.settings.rag_chunk_size};overlap={self.settings.rag_chunk_overlap}"

    def _index_is_stale(self) -> bool:
        current_signature = self._source_signature()
        return current_signature != self._manifest.get("source_signature")

    async def _load_pgvector_snapshot(self) -> Dict[str, Any]:
        rows = await self.pgvector.load_chunks(self._collection_id(), self.settings.embedding_model)
        counts = await self.pgvector.count(self._collection_id(), self.settings.embedding_model)
        self._load_chunks_into_memory(rows)
        self._documents = [{"path": chunk.path, "title": chunk.title, "category": chunk.category} for chunk in self._chunks]
        self._manifest = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "embedding_enabled": counts["chunks"] > 0,
            "embedding_dimension": self._manifest.get("embedding_dimension", 0),
            "embedding_error": None,
            "source_signature": self._source_signature(),
        }
        return {
            "ready": True,
            "documents": counts["documents"],
            "chunks": counts["chunks"],
            "embedding_enabled": counts["chunks"] > 0,
            "index_path": f"pgvector://{self.settings.pgvector_schema}.{self.settings.pgvector_chunks_table}",
            "built_at": self._manifest["built_at"],
            "new_documents": 0,
            "updated_documents": 0,
            "removed_documents": 0,
            "skipped_documents": counts["documents"],
        }

    async def _build_and_persist_index_pgvector(self, force: bool = False) -> Dict[str, Any]:
        if not self.docs_path.exists():
            raise FileNotFoundError(f"knowledge base path not found: {self.docs_path}")
        if not self.embedding_client.enabled:
            raise RuntimeError("pgvector backend requires EMBEDDING_* configuration")

        collection_id = self._collection_id()
        existing_documents = await self.pgvector.fetch_documents(collection_id)
        new_documents = 0
        updated_documents = 0
        skipped_documents = 0
        current_paths: List[str] = []
        source_files = self._source_files()
        chunking_signature = self._chunking_signature()
        vector_dim: Optional[int] = None

        for file_path in source_files:
            relative_path = str(file_path.relative_to(self.project_root))
            current_paths.append(relative_path)
            raw_text = file_path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            existing = existing_documents.get(relative_path)
            if (
                not force
                and existing is not None
                and existing.get("checksum") == checksum
                and existing.get("chunking_signature") == chunking_signature
                and existing.get("embedding_model") == self.settings.embedding_model
            ):
                skipped_documents += 1
                continue

            title, sections = self._parse_markdown(raw_text, relative_path)
            doc_id = hashlib.sha256(f"{collection_id}:{relative_path}".encode("utf-8")).hexdigest()[:20]
            category = file_path.parent.name
            chunk_rows: List[Dict[str, Any]] = []
            texts_to_embed: List[str] = []
            chunk_total = 0
            for section_name, section_text in sections:
                section_chunks = self._chunk_text(section_text)
                for index, chunk_text in enumerate(section_chunks):
                    chunk_id = f"{doc_id}-{chunk_total + index:04d}"
                    chunk_rows.append(
                        {
                            "chunk_id": chunk_id,
                            "doc_id": doc_id,
                            "path": relative_path,
                            "title": title,
                            "section": section_name,
                            "category": category,
                            "text": chunk_text,
                            "token_count": max(1, len(self._tokenize(chunk_text))),
                            "chunk_order": chunk_total + index,
                            "chunk_checksum": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                        }
                    )
                    texts_to_embed.append(chunk_text)
                chunk_total += len(section_chunks)

            embeddings = await self.embedding_client.embed_texts(texts_to_embed)
            if embeddings:
                vector_dim = len(embeddings[0])
            document_row = {
                "doc_id": doc_id,
                "path": relative_path,
                "title": title,
                "category": category,
                "checksum": checksum,
                "chunking_signature": chunking_signature,
            }
            await self.pgvector.upsert_document(
                collection_id=collection_id,
                document=document_row,
                chunks=chunk_rows,
                embeddings=embeddings,
                embedding_model=self.settings.embedding_model,
                vector_dim=vector_dim or 1024,
            )
            if existing is None:
                new_documents += 1
            else:
                updated_documents += 1

        removed_documents = await self.pgvector.delete_documents(
            collection_id,
            [path for path in existing_documents.keys() if path not in set(current_paths)],
        )

        rows = await self.pgvector.load_chunks(collection_id, self.settings.embedding_model)
        counts = await self.pgvector.count(collection_id, self.settings.embedding_model)
        self._load_chunks_into_memory(rows)
        self._documents = [
            {
                "doc_id": chunk.doc_id,
                "path": chunk.path,
                "title": chunk.title,
                "category": chunk.category,
            }
            for chunk in self._chunks
        ]
        self._manifest = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "source_signature": self._source_signature(source_files),
            "embedding_enabled": counts["chunks"] > 0,
            "embedding_dimension": vector_dim or self._manifest.get("embedding_dimension", 0),
            "embedding_error": None,
        }
        return {
            "ready": True,
            "documents": counts["documents"],
            "chunks": counts["chunks"],
            "embedding_enabled": counts["chunks"] > 0,
            "index_path": f"pgvector://{self.settings.pgvector_schema}.{self.settings.pgvector_chunks_table}",
            "built_at": self._manifest["built_at"],
            "new_documents": new_documents,
            "updated_documents": updated_documents,
            "removed_documents": removed_documents,
            "skipped_documents": skipped_documents,
        }

    async def _build_and_persist_index_local(self, force: bool = False) -> Dict[str, Any]:
        if not self.docs_path.exists():
            raise FileNotFoundError(f"knowledge base path not found: {self.docs_path}")

        documents: List[Dict[str, Any]] = []
        chunks: List[KnowledgeChunk] = []
        source_files = self._source_files()
        for file_path in source_files:
            relative_path = str(file_path.relative_to(self.project_root))
            raw_text = file_path.read_text(encoding="utf-8")
            title, sections = self._parse_markdown(raw_text, relative_path)
            doc_id = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]
            category = file_path.parent.name
            chunk_total = 0
            for section_name, section_text in sections:
                section_chunks = self._chunk_text(section_text)
                for index, chunk_text in enumerate(section_chunks):
                    chunk_id = f"{doc_id}-{chunk_total + index:04d}"
                    tokens = self._tokenize(chunk_text)
                    header_tokens = self._tokenize(f"{title} {section_name} {relative_path}")
                    chunks.append(
                        KnowledgeChunk(
                            chunk_id=chunk_id,
                            doc_id=doc_id,
                            path=relative_path,
                            title=title,
                            section=section_name,
                            category=category,
                            text=chunk_text,
                            tokens=tokens,
                            token_freq=dict(Counter(tokens)),
                            header_tokens=header_tokens,
                            length=max(1, len(tokens)),
                        )
                    )
                chunk_total += len(section_chunks)
            documents.append(
                {
                    "doc_id": doc_id,
                    "path": relative_path,
                    "title": title,
                    "category": category,
                    "checksum": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                }
            )

        self._compute_sparse_stats(chunks)
        embedding_enabled = False
        embedding_error: Optional[str] = None
        if chunks and self.embedding_client.enabled:
            try:
                embeddings = await self.embedding_client.embed_texts([chunk.text for chunk in chunks])
                for chunk, embedding in zip(chunks, embeddings):
                    chunk.embedding = embedding
                embedding_enabled = True
            except Exception as exc:
                embedding_error = str(exc)
                logger.warning("chunk embedding failed, fallback to sparse recall only: %s", exc)
                if self.settings.rag_fail_on_embedding_error:
                    raise

        self.index_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "source_signature": self._source_signature(source_files),
            "documents": documents,
            "avgdl": self._avgdl,
            "idf": self._idf,
            "embedding_enabled": embedding_enabled,
            "embedding_dimension": len(chunks[0].embedding) if embedding_enabled and chunks and chunks[0].embedding else 0,
            "embedding_model": self.settings.embedding_model,
            "embedding_base_url": self.settings.embedding_base_url,
            "embedding_error": embedding_error,
            "rerank_model": self.settings.rerank_model,
            "rerank_base_url": self.settings.rerank_base_url,
            "hybrid_strategy": "sparse+dense recall -> rerank -> mmr",
            "chunks": [chunk.to_dict() for chunk in chunks],
        }
        self.index_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        self._documents = documents
        self._chunks = chunks
        self._chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        self._chunk_index_by_id = {chunk.chunk_id: index for index, chunk in enumerate(chunks)}
        self._manifest = payload
        return {
            "ready": True,
            "documents": len(documents),
            "chunks": len(chunks),
            "embedding_enabled": embedding_enabled,
            "index_path": str(self.index_file),
            "built_at": payload["built_at"],
            "embedding_error": embedding_error,
            "new_documents": len(documents),
            "updated_documents": 0,
            "removed_documents": 0,
            "skipped_documents": 0,
        }

    def _load_index_local(self) -> None:
        payload = json.loads(self.index_file.read_text(encoding="utf-8"))
        self._documents = payload.get("documents", [])
        self._chunks = [KnowledgeChunk.from_dict(item) for item in payload.get("chunks", [])]
        self._chunk_map = {chunk.chunk_id: chunk for chunk in self._chunks}
        self._chunk_index_by_id = {chunk.chunk_id: index for index, chunk in enumerate(self._chunks)}
        self._idf = {key: float(value) for key, value in payload.get("idf", {}).items()}
        self._avgdl = float(payload.get("avgdl", 1.0))
        self._manifest = payload

    def _load_chunks_into_memory(self, rows: Sequence[Dict[str, Any]]) -> None:
        chunks: List[KnowledgeChunk] = []
        documents: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            tokens = self._tokenize(row["content"])
            header_tokens = self._tokenize(f"{row['title']} {row['section']} {row['path']}")
            chunk = KnowledgeChunk(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                path=row["path"],
                title=row["title"],
                section=row["section"],
                category=row["category"],
                text=row["content"],
                tokens=tokens,
                token_freq=dict(Counter(tokens)),
                header_tokens=header_tokens,
                length=max(1, len(tokens)),
                embedding=None,
            )
            chunks.append(chunk)
            documents[row["path"]] = {
                "doc_id": row["doc_id"],
                "path": row["path"],
                "title": row["title"],
                "category": row["category"],
            }
        self._chunks = chunks
        self._chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        self._chunk_index_by_id = {chunk.chunk_id: index for index, chunk in enumerate(chunks)}
        self._documents = list(documents.values())
        self._compute_sparse_stats(chunks)

    def _retrieval_mode(self, dense_enabled: bool, rerank_used: bool) -> str:
        if dense_enabled and rerank_used:
            return "hybrid_recall_rerank"
        if dense_enabled:
            return "hybrid_recall"
        if rerank_used:
            return "sparse_rerank"
        return "sparse_only"

    def _rank_sparse_candidates(self, sparse_scores: Sequence[float]) -> List[int]:
        ranked = sorted(range(len(self._chunks)), key=lambda index: sparse_scores[index], reverse=True)
        limit = max(self.settings.rag_sparse_candidates, self.settings.rag_top_k)
        return [index for index in ranked[:limit] if sparse_scores[index] > 0]

    def _rank_dense_candidates(self, dense_scores: Sequence[float]) -> List[int]:
        ranked = sorted(range(len(self._chunks)), key=lambda index: dense_scores[index], reverse=True)
        limit = max(self.settings.rag_dense_candidates, self.settings.rag_top_k)
        return [index for index in ranked[:limit] if dense_scores[index] > 0]

    def _prefuse_candidates(
        self,
        query_tokens: Sequence[str],
        sparse_scores: Sequence[float],
        dense_scores: Sequence[float],
        sparse_candidates: Sequence[int],
        dense_candidates: Sequence[int],
        dense_enabled: bool,
    ) -> List[RetrievedChunk]:
        sparse_ranks = {index: rank + 1 for rank, index in enumerate(sparse_candidates)}
        dense_ranks = {index: rank + 1 for rank, index in enumerate(dense_candidates)}
        candidate_ids = list(dict.fromkeys([*sparse_candidates, *dense_candidates]))
        if not candidate_ids:
            return []

        query_token_set = set(query_tokens)
        candidate_hits: List[RetrievedChunk] = []
        for index in candidate_ids:
            chunk = self._chunks[index]
            sparse_rank = sparse_ranks.get(index)
            dense_rank = dense_ranks.get(index)
            fused_score = 0.0
            if sparse_rank is not None:
                fused_score += self.settings.rag_sparse_weight / (self.settings.rag_rrf_k + sparse_rank)
            if dense_enabled and dense_rank is not None:
                fused_score += self.settings.rag_dense_weight / (self.settings.rag_rrf_k + dense_rank)

            lexical_coverage = 0.0
            if query_token_set:
                lexical_coverage = len(query_token_set.intersection(chunk.tokens)) / len(query_token_set)
            header_overlap = len(query_token_set.intersection(chunk.header_tokens))
            fused_score += min(header_overlap * 0.015, 0.06)
            fused_score += min(lexical_coverage * 0.08, 0.08)
            candidate_hits.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    title=chunk.title,
                    section=chunk.section,
                    path=chunk.path,
                    category=chunk.category,
                    score=min(fused_score, 1.0),
                    snippet=self._snippet(chunk.text),
                    text=chunk.text,
                    sparse_rank=sparse_rank,
                    dense_rank=dense_rank,
                    sparse_score=sparse_scores[index],
                    dense_score=dense_scores[index] if dense_enabled else 0.0,
                )
            )

        candidate_hits.sort(key=lambda item: item.score, reverse=True)
        return candidate_hits[: self.settings.rag_hybrid_candidate_limit]

    def _apply_rerank(
        self,
        candidates: Sequence[RetrievedChunk],
        rerank_results: Sequence[Dict[str, Any]],
    ) -> List[RetrievedChunk]:
        if not rerank_results:
            return list(candidates)

        updated: List[RetrievedChunk] = []
        covered_indices = set()
        for rank, item in enumerate(rerank_results, start=1):
            index = item.get("index")
            if index is None or index >= len(candidates):
                continue
            covered_indices.add(index)
            candidate = candidates[index]
            rerank_score = float(item.get("relevance_score", 0.0))
            final_score = min(candidate.score * 0.25 + rerank_score * 0.75, 1.0)
            updated.append(
                RetrievedChunk(
                    chunk_id=candidate.chunk_id,
                    title=candidate.title,
                    section=candidate.section,
                    path=candidate.path,
                    category=candidate.category,
                    score=final_score,
                    snippet=candidate.snippet,
                    text=candidate.text,
                    sparse_rank=candidate.sparse_rank,
                    dense_rank=candidate.dense_rank,
                    rerank_rank=rank,
                    sparse_score=candidate.sparse_score,
                    dense_score=candidate.dense_score,
                    rerank_score=rerank_score,
                )
            )

        for index, candidate in enumerate(candidates):
            if index in covered_indices:
                continue
            updated.append(candidate)

        updated.sort(key=lambda item: item.score, reverse=True)
        return updated

    def _mmr_rerank(
        self,
        query_tokens: Sequence[str],
        candidates: Sequence[RetrievedChunk],
        top_k: int,
    ) -> List[RetrievedChunk]:
        if len(candidates) <= top_k:
            return list(candidates)

        remaining = list(candidates)
        selected: List[RetrievedChunk] = []
        while remaining and len(selected) < top_k:
            best_hit: Optional[RetrievedChunk] = None
            best_value = float("-inf")
            for hit in remaining:
                relevance = hit.score
                novelty_penalty = 0.0
                if selected:
                    novelty_penalty = max(self._hit_similarity(hit, existing, query_tokens) for existing in selected)
                mmr_value = self.settings.rag_mmr_lambda * relevance - (1 - self.settings.rag_mmr_lambda) * novelty_penalty
                if mmr_value > best_value:
                    best_value = mmr_value
                    best_hit = hit
            if best_hit is None:
                break
            selected.append(best_hit)
            remaining = [hit for hit in remaining if hit.chunk_id != best_hit.chunk_id]
        return selected

    def _hit_similarity(
        self,
        left: RetrievedChunk,
        right: RetrievedChunk,
        query_tokens: Sequence[str],
    ) -> float:
        left_chunk = self._chunk_map.get(left.chunk_id)
        right_chunk = self._chunk_map.get(right.chunk_id)
        if left_chunk and right_chunk and left_chunk.embedding and right_chunk.embedding:
            return max(0.0, self._cosine_similarity(left_chunk.embedding, right_chunk.embedding))

        left_tokens = set(self._tokenize(left.text))
        right_tokens = set(self._tokenize(right.text))
        if not left_tokens or not right_tokens:
            return 0.0
        jaccard = len(left_tokens.intersection(right_tokens)) / len(left_tokens.union(right_tokens))
        query_token_set = set(query_tokens)
        query_overlap = 0.0
        if query_token_set:
            query_overlap = len(query_token_set.intersection(left_tokens, right_tokens)) / len(query_token_set)
        return min(jaccard + query_overlap * 0.2, 1.0)

    def _source_files(self) -> List[Path]:
        files: List[Path] = []
        for pattern in MARKDOWN_FILES:
            files.extend(sorted(self.docs_path.rglob(pattern)))
        return sorted(set(files))

    def _source_signature(self, source_files: Optional[Sequence[Path]] = None) -> str:
        hasher = hashlib.sha256()
        for file_path in source_files or self._source_files():
            relative_path = str(file_path.relative_to(self.project_root))
            content = file_path.read_bytes()
            hasher.update(relative_path.encode("utf-8"))
            hasher.update(hashlib.sha256(content).hexdigest().encode("utf-8"))
        return hasher.hexdigest()

    def _compute_sparse_stats(self, chunks: Sequence[KnowledgeChunk]) -> None:
        if not chunks:
            self._idf = {}
            self._avgdl = 1.0
            return

        document_frequency: Counter[str] = Counter()
        total_length = 0
        for chunk in chunks:
            total_length += chunk.length
            document_frequency.update(set(chunk.tokens))

        total_documents = len(chunks)
        self._avgdl = max(total_length / total_documents, 1.0)
        self._idf = {
            token: math.log(1 + (total_documents - freq + 0.5) / (freq + 0.5))
            for token, freq in document_frequency.items()
        }

    def _bm25_score(self, query_tokens: Sequence[str], chunk: KnowledgeChunk) -> float:
        if not query_tokens:
            return 0.0
        score = 0.0
        k1 = 1.5
        b = 0.75
        for token in set(query_tokens):
            term_frequency = chunk.token_freq.get(token)
            if not term_frequency:
                continue
            idf = self._idf.get(token, 0.0)
            denominator = term_frequency + k1 * (1 - b + b * chunk.length / self._avgdl)
            score += idf * ((term_frequency * (k1 + 1)) / denominator)
        return score

    @staticmethod
    def _cosine_similarity(left: Optional[Sequence[float]], right: Optional[Sequence[float]]) -> float:
        if not left or not right:
            return 0.0
        return sum(a * b for a, b in zip(left, right))

    def _parse_markdown(self, raw_text: str, relative_path: str) -> tuple[str, List[tuple[str, str]]]:
        lines = raw_text.splitlines()
        title = Path(relative_path).stem.replace("-", " ").title()
        sections: List[tuple[str, str]] = []
        heading_stack: List[str] = []
        current_lines: List[str] = []

        def flush() -> None:
            text = "\n".join(current_lines).strip()
            if not text:
                return
            section_name = " > ".join(heading_stack[1:]) if len(heading_stack) > 1 else (heading_stack[0] if heading_stack else "摘要")
            sections.append((section_name or "摘要", text))

        for line in lines:
            heading = HEADING_PATTERN.match(line)
            if heading:
                flush()
                current_lines = []
                level = len(heading.group(1))
                heading_text = heading.group(2).strip()
                if level == 1:
                    title = heading_text
                    heading_stack = [heading_text]
                else:
                    heading_stack = heading_stack[: level - 1]
                    heading_stack.append(heading_text)
                continue
            current_lines.append(line)
        flush()

        if not sections:
            sections.append(("摘要", raw_text.strip()))
        return title, sections

    def _chunk_text(self, text: str) -> List[str]:
        normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not normalized:
            return []

        paragraphs: List[str] = []
        for paragraph in normalized.split("\n\n"):
            cleaned = paragraph.strip()
            if not cleaned:
                continue
            if len(cleaned) <= self.settings.rag_chunk_size:
                paragraphs.append(cleaned)
            else:
                paragraphs.extend(self._split_oversized_block(cleaned))

        chunks: List[str] = []
        current: List[str] = []
        current_length = 0
        for paragraph in paragraphs:
            addition = len(paragraph) + (2 if current else 0)
            if current and current_length + addition > self.settings.rag_chunk_size:
                chunk_text = "\n\n".join(current).strip()
                if chunk_text:
                    chunks.append(chunk_text)

                overlap_tail: List[str] = []
                overlap_length = 0
                for existing in reversed(current):
                    projected = overlap_length + len(existing) + (2 if overlap_tail else 0)
                    if projected > self.settings.rag_chunk_overlap and overlap_tail:
                        break
                    overlap_tail.insert(0, existing)
                    overlap_length = projected
                    if overlap_length >= self.settings.rag_chunk_overlap:
                        break
                current = overlap_tail[:]
                current_length = sum(len(item) for item in current) + max(len(current) - 1, 0) * 2

            if current and current_length + len(paragraph) + 2 > self.settings.rag_chunk_size:
                current = [paragraph]
                current_length = len(paragraph)
            else:
                if current:
                    current_length += 2
                current.append(paragraph)
                current_length += len(paragraph)

        if current:
            chunk_text = "\n\n".join(current).strip()
            if chunk_text:
                chunks.append(chunk_text)
        return chunks

    def _split_oversized_block(self, text: str) -> List[str]:
        if len(text) <= self.settings.rag_chunk_size:
            return [text]

        pieces: List[str] = []
        sentences = [segment.strip() for segment in re.split(r"(?<=[。！？.!?])", text) if segment.strip()]
        buffer = ""
        for sentence in sentences:
            proposed = f"{buffer}{sentence}" if buffer else sentence
            if buffer and len(proposed) > self.settings.rag_chunk_size:
                pieces.append(buffer)
                buffer = sentence
                continue
            if len(sentence) > self.settings.rag_chunk_size:
                if buffer:
                    pieces.append(buffer)
                    buffer = ""
                start = 0
                while start < len(sentence):
                    end = min(start + self.settings.rag_chunk_size, len(sentence))
                    pieces.append(sentence[start:end])
                    if end >= len(sentence):
                        break
                    start = max(end - self.settings.rag_chunk_overlap, start + 1)
                continue
            buffer = proposed

        if buffer:
            pieces.append(buffer)
        return pieces

    def _build_direct_answer(self, hits: Sequence[RetrievedChunk]) -> str:
        if not hits:
            return ""
        top_hits = hits[:2]
        references = "、".join(
            f"《{hit.title}》{(' / ' + hit.section) if hit.section else ''}" for hit in top_hits
        )
        snippets = "；".join(hit.snippet for hit in top_hits)
        return f"根据知识库，建议优先参考 {references}。{snippets}"

    @staticmethod
    def _snippet(text: str, limit: int = 180) -> str:
        compact = re.sub(r"\s+", " ", text).strip()
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1].rstrip() + "…"

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        lowered = text.lower()
        tokens = TOKEN_PATTERN.findall(lowered)
        for sequence in CJK_PATTERN.findall(lowered):
            if len(sequence) <= 2:
                tokens.append(sequence)
                continue
            if len(sequence) <= 8:
                tokens.append(sequence)
            tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
        return tokens
