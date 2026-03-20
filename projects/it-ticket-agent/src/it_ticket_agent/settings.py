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
    orchestration_mode: str = os.getenv("ORCHESTRATION_MODE", "legacy")
    mcp_connections_path: str = os.getenv("MCP_CONNECTIONS_PATH", "./mcp_connections.yaml")
    approval_db_path: str = os.getenv("APPROVAL_DB_PATH", "./data/approvals.db")
    langgraph_checkpoint_db: str = os.getenv("LANGGRAPH_CHECKPOINT_DB", "./data/langgraph.db")

    agent_transport: str = os.getenv("AGENT_TRANSPORT", "local")
    pod_agent_url: str = os.getenv(
        "POD_AGENT_URL",
        "http://localhost:8101/api/v1/agents/pod-analysis/run",
    )
    rca_agent_url: str = os.getenv(
        "RCA_AGENT_URL",
        "http://localhost:8101/api/v1/agents/root-cause/run",
    )
    network_agent_url: str = os.getenv(
        "NETWORK_AGENT_URL",
        "http://localhost:8101/api/v1/agents/network-diagnosis/run",
    )

    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_timeout_sec: int = int(os.getenv("LLM_TIMEOUT_SEC", "30"))
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))

    rag_enabled: bool = os.getenv("RAG_ENABLED", "true").lower() == "true"
    rag_service_base_url: str = os.getenv("RAG_SERVICE_BASE_URL", "http://localhost:8200")
    rag_service_timeout_sec: int = int(os.getenv("RAG_SERVICE_TIMEOUT_SEC", "30"))


def get_settings() -> Settings:
    return Settings()
