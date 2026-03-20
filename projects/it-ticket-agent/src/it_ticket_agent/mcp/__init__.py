"""MCP connection scaffolding for domain agents."""

from .client import MCPClient
from .connections import MCPConnectionManager

__all__ = ["MCPClient", "MCPConnectionManager"]
