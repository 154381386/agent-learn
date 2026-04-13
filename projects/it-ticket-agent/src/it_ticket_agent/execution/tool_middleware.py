from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping

from ..runtime.contracts import TaskEnvelope
from ..state.incident_state import IncidentState
from ..tools.contracts import BaseTool, ToolExecutionResult


class ToolExecutionMiddleware:
    def __init__(self, tools: Mapping[str, BaseTool]) -> None:
        self.tools = dict(tools)

    async def run(self, tool_name: str, *, task: TaskEnvelope, arguments: dict[str, Any] | None = None) -> ToolExecutionResult:
        tool = self.tools.get(tool_name)
        if tool is None:
            return ToolExecutionResult(
                tool_name=tool_name,
                status="error",
                summary=f"tool 未注册: {tool_name}",
                payload={"arguments": dict(arguments or {}), "error_type": "tool_not_registered", "retry_count": 0},
                evidence=[],
                risk="unknown",
            )
        risk_level = str(getattr(tool, "risk_level", "low") or "low").lower()
        if risk_level in {"high", "critical"}:
            return ToolExecutionResult(
                tool_name=tool_name,
                status="approval_required",
                summary=f"{tool_name} 为高风险 tool，需进入审批链后才能执行。",
                payload={"arguments": dict(arguments or {}), "approval_required": True, "error_type": "approval_required", "retry_count": 0},
                evidence=[],
                risk=risk_level,
            )

        attempts = 1 + (1 if bool(getattr(tool, "retryable", False)) else 0)
        timeout_sec = int(getattr(tool, "timeout_sec", 30) or 30)
        last_error: str | None = None
        for attempt in range(attempts):
            started_at = time.perf_counter()
            try:
                result = await asyncio.wait_for(tool.run(task, arguments), timeout=timeout_sec)
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                payload = dict(result.payload)
                payload.setdefault("retry_count", attempt)
                payload.setdefault("latency_ms", latency_ms)
                payload.setdefault("error_type", "")
                return result.model_copy(update={"payload": payload, "risk": risk_level})
            except asyncio.TimeoutError:
                last_error = "timeout"
            except Exception as exc:
                last_error = exc.__class__.__name__
                if not bool(getattr(tool, "retryable", False)):
                    break
        return ToolExecutionResult(
            tool_name=tool_name,
            status="error",
            summary=f"{tool_name} 执行失败：{last_error or 'unknown_error'}",
            payload={
                "arguments": dict(arguments or {}),
                "error_type": last_error or "unknown_error",
                "retry_count": max(attempts - 1, 0),
                "timeout_sec": timeout_sec,
                "latency_ms": 0,
            },
            evidence=[f"tool_error={last_error or 'unknown_error'}"],
            risk=risk_level,
        )
    async def run_action(
        self,
        action: str,
        *,
        params: dict[str, Any],
        incident_state: IncidentState | None,
        executor,
        approved: bool = False,
    ) -> dict[str, Any]:
        from ..execution.action_registry import ExecutionSafetyError, get_action_registration, normalize_executable_params

        normalized_action = str(action or "")
        registration = get_action_registration(normalized_action)
        if registration is None:
            return {
                "ticket_id": incident_state.ticket_id if incident_state is not None else "",
                "status": "failed",
                "message": f"动作 {normalized_action} 未在执行注册表中登记。",
                "diagnosis": {"execution": {"status": "registration_error", "action": normalized_action, "params": dict(params), "approved": approved, "latency_ms": 0}},
            }
        try:
            validated_params = normalize_executable_params(normalized_action, params)
        except ExecutionSafetyError as exc:
            return {
                "ticket_id": incident_state.ticket_id if incident_state is not None else "",
                "status": "failed",
                "message": str(exc),
                "diagnosis": {"execution": {"status": "validation_error", "action": normalized_action, "params": dict(params), "approved": approved, "latency_ms": 0}},
            }
        high_risk = bool(registration.allowed_risks & {"high", "critical"})
        if high_risk and not approved:
            return {
                "ticket_id": incident_state.ticket_id if incident_state is not None else "",
                "status": "awaiting_approval",
                "message": f"动作 {normalized_action} 需要审批后才能执行。",
                "diagnosis": {"execution": {"status": "approval_required", "action": normalized_action, "params": validated_params, "approved": False, "latency_ms": 0}},
            }
        started_at = time.perf_counter()
        response = await executor(normalized_action, params=validated_params, incident_state=incident_state)
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        diagnosis = dict(response.get("diagnosis") or {})
        execution = dict(diagnosis.get("execution") or {})
        execution.setdefault("action", normalized_action)
        execution.setdefault("params", validated_params)
        execution.setdefault("approved", approved)
        execution.setdefault("latency_ms", latency_ms)
        diagnosis["execution"] = execution
        response["diagnosis"] = diagnosis
        return response

