from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .protocol import http_status_for_error, jsonrpc_error, jsonrpc_result
from .tools import ToolSpec, build_tool_registry


class MCPApplication:
    def __init__(self) -> None:
        self.tools = build_tool_registry()

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "server": "cicd-mcp-server"}

    def index(self) -> dict[str, Any]:
        return {
            "name": "cicd-mcp-server",
            "version": "0.1.0",
            "description": "Standalone mock MCP server for CICD Agent",
            "endpoints": {
                "healthz": "/healthz",
                "mcp": "/mcp",
                "tools": "/api/v1/tools",
            },
            "tool_count": len(self.tools),
        }

    def list_tools(self) -> dict[str, Any]:
        return {"tools": [tool.as_mcp_tool() for tool in self.tools.values()]}

    def tool_detail(self, name: str) -> dict[str, Any] | None:
        tool = self.tools.get(name)
        if tool is None:
            return None
        return tool.as_mcp_tool()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            raise KeyError(name)
        return tool.handler(arguments)

    def handle_mcp(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        request_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}

        if payload.get("jsonrpc") != "2.0" or not method:
            error = jsonrpc_error(request_id, -32600, "Invalid JSON-RPC request")
            return http_status_for_error(-32600), error

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                    "serverInfo": {"name": "cicd-mcp-server", "version": "0.1.0"},
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "prompts": {"listChanged": False},
                        "resources": {"listChanged": False},
                    },
                }
                return HTTPStatus.OK, jsonrpc_result(request_id, result)

            if method == "notifications/initialized":
                return HTTPStatus.ACCEPTED, None

            if method == "ping":
                return HTTPStatus.OK, jsonrpc_result(request_id, {"pong": True})

            if method == "tools/list":
                return HTTPStatus.OK, jsonrpc_result(request_id, self.list_tools())

            if method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not name:
                    error = jsonrpc_error(request_id, -32602, "Missing tool name")
                    return http_status_for_error(-32602), error
                result = self.call_tool(name, arguments)
                return HTTPStatus.OK, jsonrpc_result(request_id, result)

            if method == "resources/list":
                return HTTPStatus.OK, jsonrpc_result(request_id, {"resources": []})

            if method == "prompts/list":
                return HTTPStatus.OK, jsonrpc_result(request_id, {"prompts": []})

            error = jsonrpc_error(request_id, -32601, f"Method not found: {method}")
            return http_status_for_error(-32601), error
        except KeyError as exc:
            error = jsonrpc_error(request_id, -32602, f"Unknown tool: {exc.args[0]}")
            return http_status_for_error(-32602), error
        except Exception as exc:
            error = jsonrpc_error(request_id, -32603, "Internal server error", {"detail": str(exc)})
            return http_status_for_error(-32603), error


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any] | None) -> None:
    handler.send_response(status)
    if body is None:
        handler.end_headers()
        return
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(content_length) if content_length > 0 else b"{}"
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def create_handler(app: MCPApplication) -> type[BaseHTTPRequestHandler]:
    class RequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                _json_response(self, HTTPStatus.OK, app.index())
                return
            if parsed.path == "/healthz":
                _json_response(self, HTTPStatus.OK, app.health())
                return
            if parsed.path == "/api/v1/tools":
                _json_response(self, HTTPStatus.OK, app.list_tools())
                return
            if parsed.path.startswith("/api/v1/tools/"):
                name = parsed.path.removeprefix("/api/v1/tools/")
                detail = app.tool_detail(name)
                if detail is None:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": f"tool not found: {name}"})
                    return
                _json_response(self, HTTPStatus.OK, detail)
                return
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/mcp":
                try:
                    payload = _read_json_body(self)
                except json.JSONDecodeError:
                    error = jsonrpc_error(None, -32700, "Parse error")
                    _json_response(self, http_status_for_error(-32700), error)
                    return
                status, body = app.handle_mcp(payload)
                _json_response(self, int(status), body)
                return

            if parsed.path.startswith("/api/v1/tools/"):
                name = parsed.path.removeprefix("/api/v1/tools/")
                try:
                    arguments = _read_json_body(self)
                    result = app.call_tool(name, arguments)
                except json.JSONDecodeError:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid json body"})
                    return
                except KeyError:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": f"tool not found: {name}"})
                    return
                _json_response(self, HTTPStatus.OK, result)
                return

            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    return RequestHandler


def run_server(host: str, port: int) -> None:
    app = MCPApplication()
    server = ThreadingHTTPServer((host, port), create_handler(app))
    print(f"cicd-mcp-server listening on http://{host}:{port}")
    print("MCP endpoint: POST /mcp")
    print("Health endpoint: GET /healthz")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
