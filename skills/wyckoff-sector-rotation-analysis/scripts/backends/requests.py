import json as _json
import urllib.request
import urllib.error
from typing import Any, Dict, Optional


class HTTPError(Exception):
    pass


class Response:
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body
        self.text = body.decode('utf-8', errors='replace')

    def raise_for_status(self) -> None:
        if 400 <= self.status_code:
            raise HTTPError(f"HTTP {self.status_code}: {self.text[:500]}")

    def json(self) -> Any:
        return _json.loads(self.text)


def post(url: str, headers: Optional[Dict[str, str]] = None, json: Any = None, timeout: int = 30) -> Response:
    body = None
    request_headers = dict(headers or {})
    if json is not None:
        body = _json.dumps(json, ensure_ascii=False).encode('utf-8')
        request_headers.setdefault('Content-Type', 'application/json')
    req = urllib.request.Request(url, data=body, headers=request_headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.getcode(), resp.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.read())
