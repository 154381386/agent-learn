from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None

from .settings import Settings


IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class PgVectorStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.schema = self._safe_identifier(settings.pgvector_schema)
        self.documents_table = self._safe_identifier(settings.pgvector_documents_table)
        self.chunks_table = self._safe_identifier(settings.pgvector_chunks_table)

    @property
    def enabled(self) -> bool:
        return bool(psycopg and self.settings.rag_vector_backend == "pgvector" and self.settings.pgvector_dsn)

    async def ensure_base_schema(self) -> None:
        if not self.enabled:
            raise RuntimeError("pgvector backend is not configured")

        async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
                await cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.schema}.{self.documents_table} (
                        doc_id TEXT PRIMARY KEY,
                        collection_id TEXT NOT NULL,
                        path TEXT NOT NULL,
                        title TEXT NOT NULL,
                        category TEXT NOT NULL,
                        checksum TEXT NOT NULL,
                        chunking_signature TEXT NOT NULL,
                        embedding_model TEXT NOT NULL,
                        source_type TEXT NOT NULL DEFAULT 'markdown',
                        indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (collection_id, path)
                    )
                    """
                )
                await cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.documents_table}_collection_path_idx "
                    f"ON {self.schema}.{self.documents_table} (collection_id, path)"
                )
            await conn.commit()

    async def ensure_schema(self, vector_dim: int) -> None:
        if not self.enabled:
            raise RuntimeError("pgvector backend is not configured")

        await self.ensure_base_schema()
        async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.schema}.{self.chunks_table} (
                        chunk_id TEXT PRIMARY KEY,
                        doc_id TEXT NOT NULL REFERENCES {self.schema}.{self.documents_table}(doc_id) ON DELETE CASCADE,
                        collection_id TEXT NOT NULL,
                        path TEXT NOT NULL,
                        title TEXT NOT NULL,
                        section TEXT NOT NULL,
                        category TEXT NOT NULL,
                        content TEXT NOT NULL,
                        token_count INTEGER NOT NULL,
                        chunk_order INTEGER NOT NULL,
                        chunk_checksum TEXT NOT NULL,
                        embedding_model TEXT NOT NULL,
                        embedding VECTOR({vector_dim}) NOT NULL,
                        indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.chunks_table}_collection_doc_idx "
                    f"ON {self.schema}.{self.chunks_table} (collection_id, doc_id, chunk_order)"
                )
                await cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.chunks_table}_embedding_hnsw_idx "
                    f"ON {self.schema}.{self.chunks_table} USING hnsw (embedding vector_cosine_ops)"
                )
            await conn.commit()

    async def fetch_documents(self, collection_id: str) -> Dict[str, Dict[str, Any]]:
        if not self.enabled:
            return {}
        await self.ensure_base_schema()

        async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn, row_factory=dict_row) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT doc_id, path, checksum, chunking_signature, embedding_model
                    FROM {self.schema}.{self.documents_table}
                    WHERE collection_id = %s
                    """,
                    (collection_id,),
                )
                rows = await cur.fetchall()
        return {row["path"]: dict(row) for row in rows}

    async def load_chunks(self, collection_id: str, embedding_model: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []

        sql = (
            f"SELECT chunk_id, doc_id, path, title, section, category, content, token_count, chunk_order "
            f"FROM {self.schema}.{self.chunks_table} WHERE collection_id = %s"
        )
        params: List[Any] = [collection_id]
        if embedding_model:
            sql += " AND embedding_model = %s"
            params.append(embedding_model)
        sql += " ORDER BY path, chunk_order"

        try:
            async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn, row_factory=dict_row) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, tuple(params))
                    rows = await cur.fetchall()
            return [dict(row) for row in rows]
        except psycopg.errors.UndefinedTable:
            return []

    async def dense_search(
        self,
        collection_id: str,
        embedding_model: str,
        query_vector: Sequence[float],
        limit: int,
    ) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []

        vector_literal = self._vector_literal(query_vector)
        try:
            async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn, row_factory=dict_row) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT chunk_id, path, title, section, category, content,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM {self.schema}.{self.chunks_table}
                        WHERE collection_id = %s AND embedding_model = %s
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (vector_literal, collection_id, embedding_model, vector_literal, limit),
                    )
                    rows = await cur.fetchall()
            return [dict(row) for row in rows]
        except psycopg.errors.UndefinedTable:
            return []

    async def upsert_document(
        self,
        *,
        collection_id: str,
        document: Dict[str, Any],
        chunks: Sequence[Dict[str, Any]],
        embeddings: Sequence[Sequence[float]],
        embedding_model: str,
        vector_dim: int,
    ) -> None:
        if not self.enabled:
            raise RuntimeError("pgvector backend is not configured")
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")

        await self.ensure_schema(vector_dim)
        async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    INSERT INTO {self.schema}.{self.documents_table}
                    (doc_id, collection_id, path, title, category, checksum, chunking_signature, embedding_model, indexed_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (doc_id) DO UPDATE SET
                        collection_id = EXCLUDED.collection_id,
                        path = EXCLUDED.path,
                        title = EXCLUDED.title,
                        category = EXCLUDED.category,
                        checksum = EXCLUDED.checksum,
                        chunking_signature = EXCLUDED.chunking_signature,
                        embedding_model = EXCLUDED.embedding_model,
                        updated_at = NOW()
                    """,
                    (
                        document["doc_id"],
                        collection_id,
                        document["path"],
                        document["title"],
                        document["category"],
                        document["checksum"],
                        document["chunking_signature"],
                        embedding_model,
                    ),
                )
                await cur.execute(
                    f"DELETE FROM {self.schema}.{self.chunks_table} WHERE collection_id = %s AND doc_id = %s",
                    (collection_id, document["doc_id"]),
                )
                for chunk, embedding in zip(chunks, embeddings):
                    await cur.execute(
                        f"""
                        INSERT INTO {self.schema}.{self.chunks_table}
                        (chunk_id, doc_id, collection_id, path, title, section, category, content, token_count, chunk_order, chunk_checksum, embedding_model, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                        """,
                        (
                            chunk["chunk_id"],
                            document["doc_id"],
                            collection_id,
                            chunk["path"],
                            chunk["title"],
                            chunk["section"],
                            chunk["category"],
                            chunk["text"],
                            chunk["token_count"],
                            chunk["chunk_order"],
                            chunk["chunk_checksum"],
                            embedding_model,
                            self._vector_literal(embedding),
                        ),
                    )
            await conn.commit()

    async def delete_documents(self, collection_id: str, paths: Sequence[str]) -> int:
        if not self.enabled or not paths:
            return 0
        await self.ensure_base_schema()

        async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"DELETE FROM {self.schema}.{self.documents_table} WHERE collection_id = %s AND path = ANY(%s)",
                    (collection_id, list(paths)),
                )
                deleted = cur.rowcount or 0
            await conn.commit()
        return deleted

    async def count(self, collection_id: str, embedding_model: Optional[str] = None) -> Dict[str, int]:
        if not self.enabled:
            return {"documents": 0, "chunks": 0}
        await self.ensure_base_schema()

        async with await psycopg.AsyncConnection.connect(self.settings.pgvector_dsn, row_factory=dict_row) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT COUNT(*) AS count FROM {self.schema}.{self.documents_table} WHERE collection_id = %s",
                    (collection_id,),
                )
                document_count = int((await cur.fetchone())["count"])
                try:
                    if embedding_model:
                        await cur.execute(
                            f"SELECT COUNT(*) AS count FROM {self.schema}.{self.chunks_table} WHERE collection_id = %s AND embedding_model = %s",
                            (collection_id, embedding_model),
                        )
                    else:
                        await cur.execute(
                            f"SELECT COUNT(*) AS count FROM {self.schema}.{self.chunks_table} WHERE collection_id = %s",
                            (collection_id,),
                        )
                    chunk_count = int((await cur.fetchone())["count"])
                except psycopg.errors.UndefinedTable:
                    chunk_count = 0
        return {"documents": document_count, "chunks": chunk_count}

    @staticmethod
    def _vector_literal(vector: Sequence[float]) -> str:
        return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"

    @staticmethod
    def _safe_identifier(value: str) -> str:
        if not IDENTIFIER_PATTERN.match(value):
            raise ValueError(f"invalid SQL identifier: {value}")
        return value
