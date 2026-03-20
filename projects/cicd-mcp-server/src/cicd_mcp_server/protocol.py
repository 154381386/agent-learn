from __future__ import annotations

from http import HTTPStatus
from typing import Any


def jsonrpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def http_status_for_error(code: int) -> int:
    mapping = {
        -32700: HTTPStatus.BAD_REQUEST,
        -32600: HTTPStatus.BAD_REQUEST,
        -32601: HTTPStatus.NOT_FOUND,
        -32602: HTTPStatus.UNPROCESSABLE_ENTITY,
        -32603: HTTPStatus.INTERNAL_SERVER_ERROR,
    }
    return int(mapping.get(code, HTTPStatus.BAD_REQUEST))
