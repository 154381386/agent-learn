import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass
class Settings:
    app_env: str = os.getenv("APP_ENV", "dev")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8200"))

    rag_enabled: bool = os.getenv("RAG_ENABLED", "true").lower() == "true"
    rag_vector_backend: str = os.getenv("RAG_VECTOR_BACKEND", "local")
    pgvector_dsn: str = os.getenv("PGVECTOR_DSN", "")
    pgvector_schema: str = os.getenv("PGVECTOR_SCHEMA", "rag")
    pgvector_documents_table: str = os.getenv("PGVECTOR_DOCUMENTS_TABLE", "documents")
    pgvector_chunks_table: str = os.getenv("PGVECTOR_CHUNKS_TABLE", "chunks")
    pgvector_parents_table: str = os.getenv("PGVECTOR_PARENTS_TABLE", "parent_blocks")

    rag_docs_path: str = os.getenv("RAG_DOCS_PATH", "./mock_kb")
    rag_index_dir: str = os.getenv("RAG_INDEX_DIR", "./data/rag")
    rag_auto_reindex_on_boot: bool = os.getenv("RAG_AUTO_REINDEX_ON_BOOT", "true").lower() == "true"
    rag_chunk_size: int = int(os.getenv("RAG_CHUNK_SIZE", "900"))
    rag_chunk_overlap: int = int(os.getenv("RAG_CHUNK_OVERLAP", "160"))
    rag_parent_context_max_chars: int = int(os.getenv("RAG_PARENT_CONTEXT_MAX_CHARS", "2400"))
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "5"))
    rag_direct_answer_min_score: float = float(os.getenv("RAG_DIRECT_ANSWER_MIN_SCORE", "0.58"))
    rag_direct_answer_min_margin: float = float(os.getenv("RAG_DIRECT_ANSWER_MIN_MARGIN", "0.10"))
    rag_sparse_weight: float = float(os.getenv("RAG_SPARSE_WEIGHT", "0.55"))
    rag_dense_weight: float = float(os.getenv("RAG_DENSE_WEIGHT", "0.45"))
    rag_sparse_candidates: int = int(os.getenv("RAG_SPARSE_CANDIDATES", "40"))
    rag_dense_candidates: int = int(os.getenv("RAG_DENSE_CANDIDATES", "40"))
    rag_hybrid_candidate_limit: int = int(os.getenv("RAG_HYBRID_CANDIDATE_LIMIT", "60"))
    rag_rrf_k: int = int(os.getenv("RAG_RRF_K", "60"))
    rag_mmr_lambda: float = float(os.getenv("RAG_MMR_LAMBDA", "0.72"))
    rag_fail_on_embedding_error: bool = os.getenv("RAG_FAIL_ON_EMBEDDING_ERROR", "false").lower() == "true"
    case_memory_schema: str = os.getenv("CASE_MEMORY_SCHEMA", "case_memory")
    case_memory_table: str = os.getenv("CASE_MEMORY_TABLE", "incident_case_embedding")
    case_memory_top_k: int = int(os.getenv("CASE_MEMORY_TOP_K", "6"))

    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "")
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small"))
    embedding_timeout_sec: int = int(os.getenv("EMBEDDING_TIMEOUT_SEC", "30"))
    embedding_batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", os.getenv("RAG_EMBEDDING_BATCH_SIZE", "16")))

    rerank_base_url: str = os.getenv("RERANK_BASE_URL", "https://dashscope.aliyuncs.com")
    rerank_api_key: str = os.getenv("RERANK_API_KEY", os.getenv("EMBEDDING_API_KEY", ""))
    rerank_model: str = os.getenv("RERANK_MODEL", "qwen3-rerank")
    rerank_timeout_sec: int = int(os.getenv("RERANK_TIMEOUT_SEC", "30"))
    rerank_top_n: int = int(os.getenv("RERANK_TOP_N", "20"))
    rerank_return_documents: bool = os.getenv("RERANK_RETURN_DOCUMENTS", "true").lower() == "true"
    rerank_instruct: str = os.getenv(
        "RERANK_INSTRUCT",
        "Given a user query, rank the most relevant enterprise knowledge passages that best answer the question.",
    )
    rerank_fail_open: bool = os.getenv("RERANK_FAIL_OPEN", "true").lower() == "true"


def get_settings() -> Settings:
    return Settings()
