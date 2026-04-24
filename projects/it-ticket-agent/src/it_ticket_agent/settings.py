import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


LLM_PROVIDER_PRESETS = {
    "richado": {
        "base_url": "http://richado.qzz.io:8091",
        "model": "gpt-5.5",
        "wire_api": "responses",
        "api_key_env": "LLM_RICHADO_API_KEY",
    },
    "yuangege": {
        "base_url": "https://api.yuangege.cloud/v1",
        "model": "gpt-5.5",
        "wire_api": "chat",
        "api_key_env": "LLM_YUANGEGE_API_KEY",
    },
    "none": {
        "base_url": "",
        "model": "",
        "wire_api": "chat",
        "api_key_env": "",
    },
}
LLM_PROVIDER_ALIASES = {
    "current": "richado",
    "default": "richado",
    "previous": "yuangege",
    "old": "yuangege",
    "disabled": "none",
}


def _env_value(*names: str) -> str:
    for name in names:
        if not name:
            continue
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _llm_provider_name() -> str:
    raw = _env_value("LLM_PROVIDER").lower() or "richado"
    return LLM_PROVIDER_ALIASES.get(raw, raw)


def _llm_provider_preset() -> dict[str, str]:
    return dict(LLM_PROVIDER_PRESETS.get(_llm_provider_name(), {}))


def _llm_provider_value(field: str, env_name: str) -> str:
    explicit = _env_value(env_name)
    if explicit:
        return explicit
    return str(_llm_provider_preset().get(field, ""))


def _llm_api_key_value() -> str:
    preset = _llm_provider_preset()
    return _env_value("LLM_API_KEY", str(preset.get("api_key_env") or ""), "OPENAI_API_KEY")


@dataclass
class Settings:
    app_env: str = os.getenv("APP_ENV", "dev")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    storage_backend: str = os.getenv("STORAGE_BACKEND", "postgres")
    postgres_dsn: str = os.getenv("POSTGRES_DSN", "")
    mcp_connections_path: str = os.getenv("MCP_CONNECTIONS_PATH", "./mcp_connections.yaml")
    approval_db_path: str = os.getenv("APPROVAL_DB_PATH", "./data/approvals.db")
    langfuse_public_key: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    langfuse_base_url: str = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    langfuse_environment: str = os.getenv("LANGFUSE_ENVIRONMENT", os.getenv("APP_ENV", "dev"))
    langfuse_release: str = os.getenv("LANGFUSE_RELEASE", "it-ticket-agent")

    llm_provider: str = field(default_factory=_llm_provider_name)
    llm_base_url: str = field(default_factory=lambda: _llm_provider_value("base_url", "LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=_llm_api_key_value)
    llm_model: str = field(default_factory=lambda: _llm_provider_value("model", "LLM_MODEL"))
    llm_wire_api: str = field(default_factory=lambda: _llm_provider_value("wire_api", "LLM_WIRE_API").lower() or "chat")
    llm_timeout_sec: int = int(os.getenv("LLM_TIMEOUT_SEC", "30"))
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "")
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_timeout_sec: int = int(os.getenv("EMBEDDING_TIMEOUT_SEC", "30"))

    rag_enabled: bool = os.getenv("RAG_ENABLED", "true").lower() == "true"
    rag_service_base_url: str = os.getenv("RAG_SERVICE_BASE_URL", "http://localhost:8200")
    rag_service_timeout_sec: int = int(os.getenv("RAG_SERVICE_TIMEOUT_SEC", "30"))
    orchestration_mode: str = os.getenv("ORCHESTRATION_MODE", "react_tool_first")
    react_max_iterations: int = int(os.getenv("REACT_MAX_ITERATIONS", "4"))
    react_max_tool_calls: int = int(os.getenv("REACT_MAX_TOOL_CALLS", "20"))
    react_confidence_threshold: float = float(os.getenv("REACT_CONFIDENCE_THRESHOLD", "0.65"))
    react_max_parallel_branches: int = int(os.getenv("REACT_MAX_PARALLEL_BRANCHES", "4"))
    react_summary_after_n_steps: int = int(os.getenv("REACT_SUMMARY_AFTER_N_STEPS", "3"))
    react_max_context_tokens: int = int(os.getenv("REACT_MAX_CONTEXT_TOKENS", "6000"))


def get_settings() -> Settings:
    return Settings()
