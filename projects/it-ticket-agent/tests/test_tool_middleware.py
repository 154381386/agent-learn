from __future__ import annotations

import asyncio
import unittest

from it_ticket_agent.execution.tool_middleware import ToolExecutionMiddleware
from it_ticket_agent.runtime.contracts import TaskEnvelope
from it_ticket_agent.state.incident_state import IncidentState
from it_ticket_agent.tools.contracts import BaseTool, ToolExecutionResult


class SuccessfulTool(BaseTool):
    name = "successful_tool"
    summary = "success"
    timeout_sec = 1

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="tool completed",
            payload={"service": arguments.get("service") if arguments else ""},
            evidence=["success=true"],
        )


class RetryableFlakyTool(BaseTool):
    name = "retryable_flaky_tool"
    summary = "flaky"
    retryable = True
    timeout_sec = 1

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient")
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="tool completed after retry",
            payload={"calls": self.calls},
            evidence=["retried=true"],
        )


class TimeoutTool(BaseTool):
    name = "timeout_tool"
    summary = "timeout"
    timeout_sec = 1

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        await asyncio.sleep(2)
        return ToolExecutionResult(tool_name=self.name, status="completed", summary="late")


class HighRiskTool(BaseTool):
    name = "high_risk_tool"
    summary = "approval required"
    risk_level = "high"

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=self.name, status="completed", summary="should not execute")


class ToolMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.task = TaskEnvelope(
            task_id="task-1",
            ticket_id="ticket-1",
            goal="diagnose",
            shared_context={"service": "checkout-service"},
            allowed_actions=["run_tool"],
        )
        self.incident_state = IncidentState(
            ticket_id="ticket-1",
            user_id="user-1",
            message="checkout-service 故障",
            service="checkout-service",
        )

    async def test_run_returns_structured_envelope_for_successful_tool(self) -> None:
        middleware = ToolExecutionMiddleware({"successful_tool": SuccessfulTool()})

        result = await middleware.run("successful_tool", task=self.task, arguments={"service": "checkout-service"})

        self.assertEqual(result.kind, "tool")
        self.assertEqual(result.name, "successful_tool")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["service"], "checkout-service")
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(result.error_type, "")
        self.assertFalse(result.approval_required)
        self.assertGreaterEqual(result.latency_ms, 0)

    async def test_run_retries_retryable_tool_and_records_retry_count(self) -> None:
        flaky_tool = RetryableFlakyTool()
        middleware = ToolExecutionMiddleware({"retryable_flaky_tool": flaky_tool})

        result = await middleware.run("retryable_flaky_tool", task=self.task, arguments={"service": "checkout-service"})

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(result.payload["calls"], 2)
        self.assertEqual(flaky_tool.calls, 2)
        self.assertEqual(result.error_type, "")

    async def test_run_returns_structured_timeout_error(self) -> None:
        middleware = ToolExecutionMiddleware({"timeout_tool": TimeoutTool()})

        result = await middleware.run("timeout_tool", task=self.task, arguments={"service": "checkout-service"})

        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_type, "timeout")
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(result.payload["timeout_sec"], 1)
        self.assertIn("tool_error=timeout", result.evidence)

    async def test_run_requires_approval_for_high_risk_tool(self) -> None:
        middleware = ToolExecutionMiddleware({"high_risk_tool": HighRiskTool()})

        result = await middleware.run("high_risk_tool", task=self.task, arguments={"service": "checkout-service"})

        self.assertEqual(result.status, "approval_required")
        self.assertTrue(result.approval_required)
        self.assertEqual(result.risk, "high")
        self.assertEqual(result.error_type, "approval_required")

    async def test_run_returns_structured_error_for_unregistered_tool(self) -> None:
        middleware = ToolExecutionMiddleware({})

        result = await middleware.run("missing_tool", task=self.task, arguments={"service": "checkout-service"})

        self.assertEqual(result.kind, "tool")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_type, "tool_not_registered")
        self.assertEqual(result.payload["arguments"], {"service": "checkout-service"})
        self.assertFalse(result.approval_required)

    async def test_run_action_requires_approval_for_high_risk_action(self) -> None:
        middleware = ToolExecutionMiddleware({})

        result = await middleware.run_action(
            "rollback_deploy",
            params={"service": "checkout-service"},
            incident_state=self.incident_state,
            executor=self._unexpected_executor,
            approved=False,
        )

        self.assertEqual(result["status"], "awaiting_approval")
        execution = result["diagnosis"]["execution"]
        self.assertEqual(execution["kind"], "action")
        self.assertEqual(execution["status"], "approval_required")
        self.assertTrue(execution["approval_required"])
        self.assertEqual(execution["risk"], "high")

    async def test_run_action_returns_registration_error_for_unknown_action(self) -> None:
        middleware = ToolExecutionMiddleware({})

        result = await middleware.run_action(
            "missing.action",
            params={"service": "checkout-service"},
            incident_state=self.incident_state,
            executor=self._unexpected_executor,
            approved=True,
        )

        execution = result["diagnosis"]["execution"]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(execution["kind"], "action")
        self.assertEqual(execution["status"], "failed")
        self.assertEqual(execution["error_type"], "registration_error")
        self.assertEqual(execution["risk"], "unknown")

    async def test_run_action_returns_validation_error_for_bad_params(self) -> None:
        middleware = ToolExecutionMiddleware({})

        result = await middleware.run_action(
            "scale_replicas",
            params={"service": "checkout-service", "count": "two"},
            incident_state=self.incident_state,
            executor=self._unexpected_executor,
            approved=True,
        )

        execution = result["diagnosis"]["execution"]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(execution["kind"], "action")
        self.assertEqual(execution["status"], "failed")
        self.assertEqual(execution["error_type"], "validation_error")
        self.assertEqual(execution["risk"], "unknown")
        self.assertIn("count", execution["summary"])

    async def test_run_action_wraps_executor_result_in_envelope(self) -> None:
        middleware = ToolExecutionMiddleware({})

        async def executor(action: str, *, params: dict, incident_state: IncidentState | None):
            self.assertEqual(action, "observe_service")
            self.assertEqual(params, {"service": "checkout-service"})
            self.assertEqual(incident_state.ticket_id, "ticket-1")
            return {
                "ticket_id": "ticket-1",
                "status": "completed",
                "message": "观察完成",
                "diagnosis": {
                    "execution": {
                        "status": "completed",
                        "detail": "p99 latency normal",
                    }
                },
            }

        result = await middleware.run_action(
            "observe_service",
            params={"service": "checkout-service"},
            incident_state=self.incident_state,
            executor=executor,
            approved=True,
        )

        execution = result["diagnosis"]["execution"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(execution["kind"], "action")
        self.assertEqual(execution["name"], "observe_service")
        self.assertEqual(execution["status"], "completed")
        self.assertEqual(execution["payload"]["params"], {"service": "checkout-service"})
        self.assertTrue(execution["payload"]["approved"])
        self.assertEqual(execution["payload"]["detail"], "p99 latency normal")
        self.assertEqual(execution["error_type"], "")
        self.assertFalse(execution["approval_required"])
        self.assertGreaterEqual(execution["latency_ms"], 0)

    async def _unexpected_executor(self, action: str, *, params: dict, incident_state: IncidentState | None):
        raise AssertionError("executor should not be called for approval_required action")
