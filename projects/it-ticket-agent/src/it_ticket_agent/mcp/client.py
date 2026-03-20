from __future__ import annotations

from typing import Any, Dict, List

import httpx


class MCPClient:
    def __init__(self, base_url: str, timeout_sec: int = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    async def list_tools(self) -> List[Dict[str, Any]]:
        response = await self._request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
        )
        return response.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        response = await self._request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"Unexpected MCP result for tool {name}")
        return result

    async def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            response = await client.post(f"{self.base_url}/mcp", json=payload)
            response.raise_for_status()
            data = response.json()
        if "error" in data:
            message = data["error"].get("message", "Unknown MCP error")
            raise ValueError(message)
        return data
