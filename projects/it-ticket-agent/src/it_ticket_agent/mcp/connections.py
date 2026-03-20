from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from pydantic import BaseModel, Field


class AgentMCPConnections(BaseModel):
    mcp_servers: List[str] = Field(default_factory=list)


class MCPConnectionsConfig(BaseModel):
    agents: Dict[str, AgentMCPConnections] = Field(default_factory=dict)


class MCPConnectionManager:
    def __init__(self, config_path: str) -> None:
        self.config_path = Path(config_path)
        self.config = self._load_config()

    def servers_for_agent(self, agent_name: str) -> List[str]:
        agent_config = self.config.agents.get(agent_name)
        if agent_config is None:
            return []
        return [self._normalize_server_url(server) for server in agent_config.mcp_servers]

    def _load_config(self) -> MCPConnectionsConfig:
        if not self.config_path.exists():
            return MCPConnectionsConfig()

        content = self.config_path.read_text(encoding="utf-8")
        return self._parse_simple_yaml(content)

    @staticmethod
    def _parse_simple_yaml(content: str) -> MCPConnectionsConfig:
        agents: Dict[str, AgentMCPConnections] = {}
        current_agent: str | None = None

        for raw_line in content.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped == "agents:":
                continue

            if line.startswith("  ") and not line.startswith("    ") and stripped.endswith(":"):
                current_agent = stripped[:-1]
                agents[current_agent] = AgentMCPConnections()
                continue

            if current_agent and line.startswith("    mcp_servers:"):
                _, raw_value = stripped.split(":", 1)
                raw_value = raw_value.strip()
                if raw_value.startswith("[") and raw_value.endswith("]"):
                    items = [item.strip() for item in raw_value[1:-1].split(",") if item.strip()]
                    agents[current_agent].mcp_servers = items

        return MCPConnectionsConfig(agents=agents)

    @staticmethod
    def _normalize_server_url(server: str) -> str:
        server = server.strip()
        if not server:
            return server
        if server.startswith("http://") or server.startswith("https://"):
            return server.rstrip("/")
        return f"http://{server.rstrip('/')}"
