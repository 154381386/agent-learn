from __future__ import annotations

import json
from typing import Iterable, Sequence

from pydantic import BaseModel, Field

from ..runtime.contracts import AgentAction, AgentFinding, AgentResult, ClarificationField, ClarificationRequest
from ..schemas import model_to_dict
from ..state import SubAgentResult, subagent_result_from_agent_result
from .parallel_dispatcher import DispatchFailure

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class AggregationResult(BaseModel):
    aggregated_result: AgentResult
    subagent_results: list[SubAgentResult] = Field(default_factory=list)
    failures: list[DispatchFailure] = Field(default_factory=list)


class Aggregator:
    def aggregate(
        self,
        agent_results: Sequence[AgentResult],
        *,
        ticket_id: str = "",
        dispatch_failures: Sequence[DispatchFailure] | None = None,
    ) -> AggregationResult:
        failures = [
            item if isinstance(item, DispatchFailure) else DispatchFailure.model_validate(item)
            for item in (dispatch_failures or [])
        ]
        results = list(agent_results)
        subagent_results = [subagent_result_from_agent_result(result, ticket_id=ticket_id) for result in results]

        if not results:
            failure_summary = "；".join(f"{item.agent_name}: {item.message}" for item in failures[:3]) or "没有可用的子 Agent 结果。"
            aggregated = AgentResult(
                agent_name="aggregator",
                domain="incident",
                status="failed",
                summary=f"并行分发未产出可用结果：{failure_summary}",
                execution_path="parallel_aggregated",
                findings=[],
                evidence=[],
                tool_results=[],
                recommended_actions=[],
                risk_level="medium",
                confidence=0.0,
                open_questions=[],
                needs_handoff=True,
                raw_refs=[],
            )
            return AggregationResult(aggregated_result=aggregated, subagent_results=subagent_results, failures=failures)

        clarification_request = self._merge_clarification_requests(results)
        aggregated = AgentResult(
            agent_name="aggregator",
            domain="incident",
            status=self._status_for(results, clarification_request),
            summary=self._build_summary(results, failures),
            execution_path="parallel_aggregated",
            findings=self._merge_findings(results),
            evidence=self._merge_strings((result.evidence for result in results), limit=12),
            tool_results=self._merge_tool_results(results),
            recommended_actions=self._merge_actions(results),
            risk_level=self._highest_risk(results),
            confidence=max((result.confidence for result in results), default=0.0),
            open_questions=self._merge_strings((result.open_questions for result in results), limit=8),
            needs_handoff=any(result.needs_handoff for result in results) or bool(failures),
            raw_refs=self._merge_strings((result.raw_refs for result in results), limit=12),
            clarification_request=clarification_request,
        )
        aggregated.tool_results.append(
            {
                "tool_name": "aggregation.parallel_dispatch",
                "status": "completed",
                "summary": f"aggregated {len(results)} agent results",
                "payload": {
                    "source_agents": [result.agent_name for result in results],
                    "failure_count": len(failures),
                },
                "evidence": [failure.message for failure in failures[:3]],
                "risk": "low",
            }
        )
        return AggregationResult(aggregated_result=aggregated, subagent_results=subagent_results, failures=failures)

    @staticmethod
    def _build_summary(results: Sequence[AgentResult], failures: Sequence[DispatchFailure]) -> str:
        top_summaries = [f"{result.agent_name}: {result.summary}" for result in results[:3] if result.summary]
        summary = " | ".join(top_summaries) if top_summaries else "并行分析已完成。"
        if failures:
            summary = f"{summary}；其中 {len(failures)} 个子 Agent 失败或超时。"
        return summary

    @staticmethod
    def _status_for(results: Sequence[AgentResult], clarification_request: ClarificationRequest | None) -> str:
        if clarification_request is not None:
            return "awaiting_clarification"
        statuses = {str(result.status or "") for result in results}
        if statuses == {"failed"}:
            return "failed"
        if "completed" in statuses:
            return "completed"
        return next(iter(statuses - {"failed"}), "completed")

    @staticmethod
    def _highest_risk(results: Sequence[AgentResult]) -> str:
        return max((str(result.risk_level or "low").lower() for result in results), key=lambda item: _RISK_ORDER.get(item, 0), default="low")

    @staticmethod
    def _merge_findings(results: Sequence[AgentResult]) -> list[AgentFinding]:
        merged: list[AgentFinding] = []
        seen: set[tuple[str, str]] = set()
        for result in results:
            for finding in result.findings:
                key = (finding.title, finding.detail)
                if key in seen:
                    continue
                merged.append(finding.model_copy(deep=True))
                seen.add(key)
        return merged[:12]

    @staticmethod
    def _merge_tool_results(results: Sequence[AgentResult]) -> list[dict]:
        merged: list[dict] = []
        for result in results:
            for item in result.tool_results:
                payload = model_to_dict(item) if not isinstance(item, dict) else dict(item)
                payload.setdefault("metadata", {})
                if isinstance(payload["metadata"], dict):
                    payload["metadata"].setdefault("source_agent", result.agent_name)
                merged.append(payload)
        return merged[:20]

    @staticmethod
    def _merge_actions(results: Sequence[AgentResult]) -> list[AgentAction]:
        merged: list[AgentAction] = []
        seen: set[str] = set()
        for result in results:
            for action in result.recommended_actions:
                key = json.dumps(
                    {
                        "action": action.action,
                        "risk": action.risk,
                        "reason": action.reason,
                        "params": action.params,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if key in seen:
                    continue
                merged.append(action.model_copy(deep=True))
                seen.add(key)
        return merged[:10]

    @staticmethod
    def _merge_strings(groups: Iterable[Iterable[str]], *, limit: int) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for item in group:
                normalized = str(item or "").strip()
                if not normalized or normalized in seen:
                    continue
                merged.append(normalized)
                seen.add(normalized)
                if len(merged) >= limit:
                    return merged
        return merged

    @staticmethod
    def _merge_clarification_requests(results: Sequence[AgentResult]) -> ClarificationRequest | None:
        requests = [result.clarification_request for result in results if result.clarification_request is not None]
        if not requests:
            return None
        merged_fields: list[ClarificationField] = []
        field_index: dict[str, ClarificationField] = {}
        requested_by: list[str] = []
        for request in requests:
            requested_by.append(request.agent_name)
            for field in request.fields:
                existing = field_index.get(field.name)
                if existing is None:
                    clone = field.model_copy(deep=True)
                    field_index[field.name] = clone
                    merged_fields.append(clone)
                    continue
                existing.requested_by = Aggregator._merge_strings([existing.requested_by, field.requested_by, [request.agent_name]], limit=10)
                if field.required:
                    existing.required = True
                if _RISK_ORDER.get(field.priority, 0) > _RISK_ORDER.get(existing.priority, 0):
                    existing.priority = field.priority
                if not existing.values and field.values:
                    existing.values = list(field.values)
        joined = "、".join(field.description for field in merged_fields[:5])
        return ClarificationRequest(
            agent_name="aggregator",
            domain="incident",
            reason="多个子 Agent 仍缺少继续分析所需的关键上下文字段。",
            question=f"继续并行诊断前，请补充以下信息：{joined}。" if joined else "继续并行诊断前，请补充缺失信息。",
            fields=merged_fields,
        )
