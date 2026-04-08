from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import Any

from ..settings import Settings

logger = logging.getLogger(__name__)

try:
    from langfuse import Langfuse  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Langfuse = None  # type: ignore


def _safe_payload(payload: Any) -> Any:
    if payload is None:
        return None
    if isinstance(payload, (str, int, float, bool)):
        return payload
    if isinstance(payload, list):
        return [_safe_payload(item) for item in payload[:20]]
    if isinstance(payload, dict):
        safe: dict[str, Any] = {}
        for key, value in list(payload.items())[:50]:
            lowered = str(key).lower()
            if any(token in lowered for token in ("secret", "password", "token", "authorization", "api_key")):
                safe[str(key)] = "***"
            else:
                safe[str(key)] = _safe_payload(value)
        return safe
    return str(payload)


class _NoopObservation:
    def update(self, **_: Any) -> None:
        return None


class LangfuseObservability:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = bool(
            settings.langfuse_public_key
            and settings.langfuse_secret_key
            and settings.langfuse_base_url
            and Langfuse is not None
        )
        self._client = None
        if not self.enabled:
            return
        try:
            self._client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_base_url,
                environment=settings.langfuse_environment,
                release=settings.langfuse_release,
            )
        except Exception as exc:  # pragma: no cover - network/sdk init safety
            logger.warning("langfuse init failed: %s", exc)
            self.enabled = False
            self._client = None

    def start_span(
        self,
        *,
        name: str,
        as_type: str = "span",
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
    ):
        if not self.enabled or self._client is None:
            return nullcontext(_NoopObservation())
        try:
            kwargs: dict[str, Any] = {
                "name": name,
                "as_type": as_type,
                "input": _safe_payload(input),
                "metadata": _safe_payload(metadata or {}),
            }
            if model:
                kwargs["model"] = model
            if model_parameters:
                kwargs["model_parameters"] = _safe_payload(model_parameters)
            return self._client.start_as_current_observation(**kwargs)
        except Exception as exc:  # pragma: no cover
            logger.warning("langfuse start_span failed: %s", exc)
            return nullcontext(_NoopObservation())

    def update_current_trace(self, **kwargs: Any) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            input_payload = _safe_payload(kwargs.get("input")) if "input" in kwargs else None
            output_payload = _safe_payload(kwargs.get("output")) if "output" in kwargs else None
            if hasattr(self._client, "update_current_trace"):
                safe_kwargs = {}
                for key, value in kwargs.items():
                    safe_kwargs[key] = _safe_payload(value) if key in {"input", "output", "metadata"} else value
                self._client.update_current_trace(**safe_kwargs)
                return
            if (input_payload is not None or output_payload is not None) and hasattr(self._client, "set_current_trace_io"):
                self._client.set_current_trace_io(input=input_payload, output=output_payload)
                return
        except Exception as exc:  # pragma: no cover
            logger.warning("langfuse update_current_trace failed: %s", exc)

    def current_trace_context(self) -> dict[str, Any]:
        if not self.enabled or self._client is None:
            return {}
        try:
            trace_id = self._client.get_current_trace_id()
            trace_url = self._client.get_trace_url(trace_id=trace_id) if trace_id else None
            observation_id = self._client.get_current_observation_id()
            return {
                "enabled": True,
                "trace_id": trace_id,
                "trace_url": trace_url,
                "observation_id": observation_id,
            }
        except Exception as exc:  # pragma: no cover
            logger.warning("langfuse current_trace_context failed: %s", exc)
            return {}

    def flush(self) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            self._client.flush()
        except Exception:  # pragma: no cover
            return

    def shutdown(self) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            self._client.shutdown()
        except Exception:  # pragma: no cover
            return


_OBSERVABILITY: LangfuseObservability | None = None


def configure_observability(settings: Settings) -> LangfuseObservability:
    global _OBSERVABILITY
    _OBSERVABILITY = LangfuseObservability(settings)
    return _OBSERVABILITY


def get_observability() -> LangfuseObservability:
    global _OBSERVABILITY
    if _OBSERVABILITY is None:
        _OBSERVABILITY = LangfuseObservability(Settings())
    return _OBSERVABILITY
