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
    port: int = int(os.getenv("PORT", "8000"))
    storage_backend: str = os.getenv("STORAGE_BACKEND", "sqlite")
    postgres_dsn: str = os.getenv("POSTGRES_DSN", "")
    mcp_connections_path: str = os.getenv("MCP_CONNECTIONS_PATH", "./mcp_connections.yaml")
    approval_db_path: str = os.getenv("APPROVAL_DB_PATH", "./data/approvals.db")
    langfuse_public_key: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    langfuse_base_url: str = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    langfuse_environment: str = os.getenv("LANGFUSE_ENVIRONMENT", os.getenv("APP_ENV", "dev"))
    langfuse_release: str = os.getenv("LANGFUSE_RELEASE", "it-ticket-agent")

    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_timeout_sec: int = int(os.getenv("LLM_TIMEOUT_SEC", "30"))
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "")
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_timeout_sec: int = int(os.getenv("EMBEDDING_TIMEOUT_SEC", "30"))

    rag_enabled: bool = os.getenv("RAG_ENABLED", "true").lower() == "true"
    rag_service_base_url: str = os.getenv("RAG_SERVICE_BASE_URL", "http://localhost:8200")
    rag_service_timeout_sec: int = int(os.getenv("RAG_SERVICE_TIMEOUT_SEC", "30"))
    orchestration_mode: str = os.getenv("ORCHESTRATION_MODE", "legacy")
    react_max_iterations: int = int(os.getenv("REACT_MAX_ITERATIONS", "4"))
    react_max_tool_calls: int = int(os.getenv("REACT_MAX_TOOL_CALLS", "20"))
    react_confidence_threshold: float = float(os.getenv("REACT_CONFIDENCE_THRESHOLD", "0.65"))


def get_settings() -> Settings:
    return Settings()
