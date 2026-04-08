from __future__ import annotations

from pathlib import Path

from ..runtime.contracts import TaskEnvelope
from .contracts import BaseTool, ToolExecutionResult
from .mock_helpers import build_context, match_any, resolve_profile_mock


DEFAULT_MOCK_PROFILES_PATH = Path(__file__).resolve().parents[3] / "data" / "mock_finops_profiles.json"
ENV_VAR = "IT_TICKET_AGENT_MOCK_FINOPS_PROFILES_PATH"


class InspectCostAnomalyTool(BaseTool):
    name = "inspect_cost_anomaly"
    summary = "Inspect recent cost anomaly signals"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        anomaly_status = "none"
        if match_any(ctx["message"], ["成本", "费用", "账单", "cost", "spike"]):
            anomaly_status = "suspected"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总成本异常信号。",
            payload={"service": ctx["service"], "anomaly_status": anomaly_status},
            evidence=[f"cost_anomaly={anomaly_status}"],
        )


class InspectBudgetGuardrailTool(BaseTool):
    name = "inspect_budget_guardrail"
    summary = "Inspect budget guardrail and limit status"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        budget_state = "within_budget"
        if match_any(ctx["message"], ["预算", "budget", "超支", "额度"]):
            budget_state = "warning"
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总预算护栏状态。",
            payload={"service": ctx["service"], "budget_state": budget_state},
            evidence=[f"budget={budget_state}"],
        )


class InspectIdleResourceCandidatesTool(BaseTool):
    name = "inspect_idle_resource_candidates"
    summary = "Inspect idle resource candidates for cost optimization"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        candidate_count = 0
        if match_any(ctx["message"], ["闲置", "idle", "降本", "unused"]):
            candidate_count = 2
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总闲置资源候选。",
            payload={"service": ctx["service"], "candidate_count": candidate_count},
            evidence=[f"idle_candidates={candidate_count}"],
        )


class InspectCommitmentCoverageTool(BaseTool):
    name = "inspect_commitment_coverage"
    summary = "Inspect reserved instance or commitment coverage status"
    input_schema = {"type": "object", "properties": {"service": {"type": "string"}}}

    async def run(self, task: TaskEnvelope, arguments: dict | None = None) -> ToolExecutionResult:
        mocked = resolve_profile_mock(task, self.name, DEFAULT_MOCK_PROFILES_PATH, ENV_VAR, arguments)
        if mocked is not None:
            return mocked
        ctx = build_context(task, arguments)
        coverage = 0.72
        if match_any(ctx["message"], ["承诺", "预留", "coverage", "ri"]):
            coverage = 0.45
        return ToolExecutionResult(
            tool_name=self.name,
            status="completed",
            summary="已汇总承诺覆盖率。",
            payload={"service": ctx["service"], "coverage_ratio": coverage},
            evidence=[f"coverage={coverage}"],
        )
