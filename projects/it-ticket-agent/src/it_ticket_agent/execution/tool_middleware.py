from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping

from ..runtime.contracts import TaskEnvelope
from ..state.incident_state import IncidentState
from ..tools.contracts import BaseTool, ToolExecutionResult
from .models import ToolExecutionEnvelope


class ToolExecutionMiddleware:
    def __init__(self, tools: Mapping[str, BaseTool]) -> None:
        self.tools = dict(tools)

    async def run(self, tool_name: str, *, task: TaskEnvelope, arguments: dict[str, Any] | None = None) -> ToolExecutionEnvelope:
        tool = self.tools.get(tool_name)
        if tool is None:
            return ToolExecutionEnvelope(kind="tool", name=tool_name, status="error", summary=f"tool 未注册: {tool_name}", payload={"arguments": dict(arguments or {}), "error_type": "tool_not_registered"}, evidence=[], risk="unknown", retry_count=0, latency_ms=0, error_type="tool_not_registered", approval_required=False)
        risk_level = str(getattr(tool, "risk_level", "low") or "low").lower()
        if risk_level in {"high", "critical"}:
            return ToolExecutionEnvelope(kind="tool", name=tool_name, status="approval_required", summary=f"{tool_name} 为高风险 tool，需进入审批链后才能执行。", payload={"arguments": dict(arguments or {}), "approval_required": True}, evidence=[], risk=risk_level, retry_count=0, latency_ms=0, error_type="approval_required", approval_required=True)

        attempts = 1 + (1 if bool(getattr(tool, "retryable", False)) else 0)
        timeout_sec = int(getattr(tool, "timeout_sec", 30) or 30)
        last_error: str | None = None
        for attempt in range(attempts):
            started_at = time.perf_counter()
            try:
                result = await asyncio.wait_for(tool.run(task, arguments), timeout=timeout_sec)
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                payload = dict(result.payload)
                return ToolExecutionEnvelope(kind="tool", name=tool_name, status=result.status, summary=result.summary, payload=payload, evidence=list(result.evidence), risk=risk_level, retry_count=attempt, latency_ms=latency_ms, error_type="", approval_required=False)
            except asyncio.TimeoutError:
                last_error = "timeout"
            except Exception as exc:
                last_error = exc.__class__.__name__
                if not bool(getattr(tool, "retryable", False)):
                    break
        return ToolExecutionEnvelope(kind="tool", name=tool_name, status="error", summary=f"{tool_name} 执行失败：{last_error or 'unknown_error'}", payload={"arguments": dict(arguments or {}), "timeout_sec": timeout_sec}, evidence=[f"tool_error={last_error or 'unknown_error'}"], risk=risk_level, retry_count=max(attempts - 1, 0), latency_ms=0, error_type=last_error or "unknown_error", approval_required=False)
    async def run_action(
        self,
        action: str,
        *,
        params: dict[str, Any],
        incident_state: IncidentState | None,
        executor,
        approved: bool = False,
    ) -> dict[str, Any]:
        from ..execution.action_registry import ExecutionSafetyError, get_action_registration, infer_target, normalize_executable_params

        normalized_action = str(action or "")
        registration = get_action_registration(normalized_action)
        if registration is None:
            envelope = ToolExecutionEnvelope(
                kind="action",
                name=normalized_action,
                status="failed",
                summary=f"动作 {normalized_action} 未在执行注册表中登记。",
                target="",
                approved=approved,
                payload={"params": dict(params), "approved": approved},
                evidence=[],
                metadata={"registered": False, "validation_passed": False},
                risk="unknown",
                retry_count=0,
                latency_ms=0,
                error_type="registration_error",
                approval_required=False,
            )
            return {"ticket_id": incident_state.ticket_id if incident_state is not None else "", "status": "failed", "message": envelope.summary, "diagnosis": {"execution": envelope.model_dump()}}
        try:
            validated_params = normalize_executable_params(normalized_action, params)
        except ExecutionSafetyError as exc:
            envelope = ToolExecutionEnvelope(
                kind="action",
                name=normalized_action,
                status="failed",
                summary=str(exc),
                target=infer_target(normalized_action, None, params),
                approved=approved,
                payload={"params": dict(params), "approved": approved},
                evidence=[],
                metadata={
                    "registered": True,
                    "validation_passed": False,
                    "allowed_risks": sorted(registration.allowed_risks),
                },
                risk="unknown",
                retry_count=0,
                latency_ms=0,
                error_type="validation_error",
                approval_required=False,
            )
            return {"ticket_id": incident_state.ticket_id if incident_state is not None else "", "status": "failed", "message": envelope.summary, "diagnosis": {"execution": envelope.model_dump()}}
        high_risk = bool(registration.allowed_risks & {"high", "critical"})
        target = infer_target(normalized_action, None, validated_params)
        if high_risk and not approved:
            envelope = ToolExecutionEnvelope(
                kind="action",
                name=normalized_action,
                status="approval_required",
                summary=f"动作 {normalized_action} 需要审批后才能执行。",
                target=target,
                approved=False,
                payload={"params": validated_params, "approved": False},
                evidence=[],
                metadata={
                    "registered": True,
                    "validation_passed": True,
                    "allowed_risks": sorted(registration.allowed_risks),
                },
                risk="high",
                retry_count=0,
                latency_ms=0,
                error_type="approval_required",
                approval_required=True,
            )
            return {"ticket_id": incident_state.ticket_id if incident_state is not None else "", "status": "awaiting_approval", "message": envelope.summary, "diagnosis": {"execution": envelope.model_dump()}}
        started_at = time.perf_counter()
        response = await executor(normalized_action, params=validated_params, incident_state=incident_state)
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        diagnosis = dict(response.get("diagnosis") or {})
        execution = dict(diagnosis.get("execution") or {})
        envelope = ToolExecutionEnvelope(
            kind="action",
            name=normalized_action,
            status=str(execution.get("status") or response.get("status") or "completed"),
            summary=str(response.get("message") or execution.get("status") or normalized_action),
            target=target,
            approved=approved,
            payload={"params": validated_params, "approved": approved, **{k: v for k, v in execution.items() if k not in {"action", "params", "approved", "latency_ms", "evidence", "metadata"}}},
            evidence=list(execution.get("evidence") or []),
            metadata={
                "registered": True,
                "validation_passed": True,
                "allowed_risks": sorted(registration.allowed_risks),
                "executor_status": str(response.get("status") or ""),
                "executor_message": str(response.get("message") or ""),
                **dict(execution.get("metadata") or {}),
            },
            risk="high" if high_risk else "low",
            retry_count=0,
            latency_ms=latency_ms,
            error_type="",
            approval_required=False,
        )
        diagnosis["execution"] = envelope.model_dump()
        response["diagnosis"] = diagnosis
        return response
