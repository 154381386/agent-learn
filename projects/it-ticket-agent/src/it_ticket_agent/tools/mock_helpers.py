from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..runtime.contracts import TaskEnvelope
from ..service_names import canonical_service_name, infer_service_name
from ..testing.world_simulator import project_world_state_tool_result
from .contracts import ToolExecutionResult


DEFAULT_CASE_PROFILES_PATH = Path(__file__).resolve().parents[3] / "data" / "mock_case_profiles.json"


def build_context(
    task: TaskEnvelope,
    arguments: dict[str, Any] | None = None,
    *,
    target_key: str = "service",
    default_target: str = "unknown-service",
) -> dict[str, Any]:
    arguments = arguments or {}
    shared = task.shared_context if isinstance(task.shared_context, dict) else {}
    return {
        "message": arguments.get("query") or shared.get("message", ""),
        "service": (
            canonical_service_name(arguments.get(target_key))
            or canonical_service_name(shared.get(target_key))
            or infer_service_name(arguments.get("query") or shared.get("message", ""))
            or default_target
        ),
        "cluster": arguments.get("environment") or shared.get("cluster", "prod-shanghai-1"),
        "namespace": arguments.get("namespace") or shared.get("namespace", "default"),
    }


def match_any(message: str, keywords: list[str]) -> bool:
    normalized = str(message or "").lower()
    return any(keyword.lower() in normalized for keyword in keywords)


def canonical_name(value: str, aliases: dict[str, str] | None = None) -> str:
    normalized = canonical_service_name(value)
    if aliases and normalized:
        alias_value = aliases.get(normalized.lower()) or aliases.get(normalized)
        if alias_value:
            return str(alias_value)
    return normalized



def load_case_profiles() -> dict[str, Any]:
    raw_path = os.getenv("IT_TICKET_AGENT_CASE_PROFILES_PATH", "").strip()
    path = Path(raw_path) if raw_path else DEFAULT_CASE_PROFILES_PATH
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}



def structured_mock_payload(raw: dict[str, Any]) -> dict[str, Any]:
    candidate = raw.get("payload")
    if isinstance(candidate, dict):
        return dict(candidate)
    return {
        key: value
        for key, value in raw.items()
        if key not in {"status", "risk", "summary", "evidence"}
    }


def mock_tool_result(tool_name: str, raw: dict[str, Any]) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        status=str(raw.get("status") or "completed"),
        payload=structured_mock_payload(raw),
        risk=str(raw.get("risk") or "low"),
    )


def resolve_case_name(
    task: TaskEnvelope,
    target_name: str,
    arguments: dict[str, Any] | None = None,
) -> str | None:
    arguments = arguments or {}
    inline = str(arguments.get("mock_case") or "").strip()
    if inline:
        return inline

    shared = task.shared_context if isinstance(task.shared_context, dict) else {}
    case_map = shared.get("mock_cases")
    if isinstance(case_map, dict):
        case_name = case_map.get(target_name)
        if case_name:
            return str(case_name).strip()

    shared_global = str(shared.get("mock_case") or "").strip()
    if shared_global:
        return shared_global

    env_map_raw = os.getenv("IT_TICKET_AGENT_CASES", "").strip()
    if env_map_raw:
        try:
            payload = json.loads(env_map_raw)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            case_name = payload.get(target_name)
            if case_name:
                return str(case_name).strip()

    env_global = os.getenv("IT_TICKET_AGENT_CASE", "").strip()
    return env_global or None


def resolve_case_mock(
    task: TaskEnvelope,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    aliases: dict[str, str] | None = None,
    target_key: str = "service",
    default_target: str = "unknown-service",
) -> ToolExecutionResult | None:
    ctx = build_context(task, arguments, target_key=target_key, default_target=default_target)
    target_name = canonical_name(ctx["service"], aliases)
    if not target_name:
        return None
    case_name = resolve_case_name(task, target_name, arguments)
    if not case_name:
        return None
    profiles = load_case_profiles()
    case_profile = profiles.get(case_name)
    if not isinstance(case_profile, dict):
        return None
    services = case_profile.get("services") if isinstance(case_profile.get("services"), dict) else {}
    default_tools = case_profile.get("default") if isinstance(case_profile.get("default"), dict) else {}
    service_tools = services.get(target_name) if isinstance(services.get(target_name), dict) else {}
    payload = service_tools.get(tool_name)
    if not isinstance(payload, dict):
        payload = default_tools.get(tool_name)
    if not isinstance(payload, dict):
        return None
    return mock_tool_result(tool_name, payload)



def resolve_inline_or_shared_mock(
    task: TaskEnvelope,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> ToolExecutionResult | None:
    arguments = arguments or {}
    inline = arguments.get("mock_response")
    if isinstance(inline, dict):
        payload = inline
    else:
        shared = task.shared_context.get("mock_tool_responses", {}) if isinstance(task.shared_context, dict) else {}
        payload = shared.get(tool_name) if isinstance(shared, dict) else None

    if not isinstance(payload, dict):
        return None

    return mock_tool_result(tool_name, payload)


def resolve_world_state_mock(
    task: TaskEnvelope,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    aliases: dict[str, str] | None = None,
    target_key: str = "service",
    default_target: str = "unknown-service",
) -> ToolExecutionResult | None:
    arguments = arguments or {}
    inline = arguments.get("mock_world_state")
    if isinstance(inline, dict):
        world_state = inline
    else:
        shared = task.shared_context if isinstance(task.shared_context, dict) else {}
        candidate = shared.get("mock_world_state")
        world_state = candidate if isinstance(candidate, dict) else None
    if not isinstance(world_state, dict) or not world_state:
        return None
    ctx = build_context(task, arguments, target_key=target_key, default_target=default_target)
    target_name = canonical_name(ctx["service"], aliases) or default_target
    return project_world_state_tool_result(
        tool_name,
        world_state,
        service=target_name,
        cluster=str(ctx.get("cluster") or ""),
        namespace=str(ctx.get("namespace") or ""),
    )


def resolve_mock_result(
    task: TaskEnvelope,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    aliases: dict[str, str] | None = None,
    target_key: str = "service",
    default_target: str = "unknown-service",
) -> ToolExecutionResult | None:
    inline = resolve_inline_or_shared_mock(task, tool_name, arguments)
    if inline is not None:
        return inline
    world_state_mock = resolve_world_state_mock(
        task,
        tool_name,
        arguments,
        aliases=aliases,
        target_key=target_key,
        default_target=default_target,
    )
    if world_state_mock is not None:
        return world_state_mock
    return resolve_case_mock(
        task,
        tool_name,
        arguments,
        aliases=aliases,
        target_key=target_key,
        default_target=default_target,
    )
