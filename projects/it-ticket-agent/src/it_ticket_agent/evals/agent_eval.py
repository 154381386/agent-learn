from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any, Callable, Literal, Mapping, Sequence

from pydantic import BaseModel, Field
from unittest.mock import AsyncMock

from ..case_memory_analysis import summarize_case_memory_recall
from ..case_retrieval import infer_root_cause_taxonomy
from ..approval_store import ApprovalStore
from ..checkpoint_store import CheckpointStore
from ..execution_store import ExecutionStore
from ..interrupt_store import InterruptStore
from ..memory_store import IncidentCaseStore, ProcessMemoryStore
from ..runtime.orchestrator import SupervisorOrchestrator
from ..schemas import ConversationCreateRequest
from ..settings import Settings
from ..state.models import RAGContextBundle, RetrievalExpansion, SimilarIncidentCase
from ..session_store import SessionStore
from ..system_event_store import SystemEventStore
from ..tools.mock_helpers import DEFAULT_CASE_PROFILES_PATH


class ToolProfileRef(BaseModel):
    case_id: str
    service: str


class AgentEvalSetup(BaseModel):
    tool_profile: ToolProfileRef | None = None
    mock_tool_responses: dict[str, dict[str, Any]] = Field(default_factory=dict)
    world_state: dict[str, Any] = Field(default_factory=dict)
    mock_rag_context: dict[str, Any] = Field(default_factory=dict)
    mock_rag_context_by_query: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mock_similar_cases: list[dict[str, Any]] = Field(default_factory=list)
    mock_similar_cases_by_query: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    mock_retrieval_expansion: dict[str, Any] = Field(default_factory=dict)
    llm_mode: Literal["live", "disabled"] = "live"
    retrieval_planner_llm_mode: Literal["inherit", "disabled"] = "inherit"


class DisabledEvalLLM:
    enabled = False

    async def chat(self, messages, tools=None):  # pragma: no cover - defensive only
        raise RuntimeError("disabled eval llm does not support chat")

    @staticmethod
    def extract_json(content: str):
        return {}


class RetrievalQueryMetricExpectation(BaseModel):
    query_contains: str
    added_rag_hits: int | None = None
    added_case_hits: int | None = None
    root_cause_taxonomy: str | None = None
    matches_primary_root_cause_taxonomy: bool | None = None


class AgentEvalExpectation(BaseModel):
    status: str | None = None
    route: str | None = None
    intent: str | None = None
    stop_reason: str | None = None
    pending_interrupt_type: str | None = None
    approval_required: bool | None = None
    required_tools: list[str] = Field(default_factory=list)
    required_any_tools: list[str] = Field(default_factory=list)
    required_any_tools_min_matches: int = 1
    first_any_tools: list[str] = Field(default_factory=list)
    first_any_tools_min_matches: int = 1
    first_any_tools_window: int = 2
    first_forbidden_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    expanded_domains: list[str] = Field(default_factory=list)
    expansion_probe_required: bool | None = None
    message_contains: list[str] = Field(default_factory=list)
    conclusion_contains: list[str] = Field(default_factory=list)
    primary_root_cause_contains: list[str] = Field(default_factory=list)
    evidence_contains: list[str] = Field(default_factory=list)
    min_tool_calls_used: int | None = None
    max_tool_calls_used: int | None = None
    max_rejected_tool_calls: int | None = None
    min_sources_count: int | None = None
    max_sources_count: int | None = None
    min_retrieval_subquery_count: int | None = None
    max_retrieval_subquery_count: int | None = None
    min_added_rag_hits: int | None = None
    max_added_rag_hits: int | None = None
    min_added_case_hits: int | None = None
    max_added_case_hits: int | None = None
    retrieval_query_contains: list[str] = Field(default_factory=list)
    retrieval_query_metrics: list[RetrievalQueryMetricExpectation] = Field(default_factory=list)
    missing_evidence_contains: list[str] = Field(default_factory=list)


class AgentEvalGate(BaseModel):
    min_pass_rate: float | None = None
    max_avg_tool_calls_used: float | None = None
    max_avg_duration_ms: float | None = None
    max_expansion_probe_cases: int | None = None
    max_rejected_tool_call_cases: int | None = None
    max_rejected_tool_call_total: int | None = None


class AgentEvalCase(BaseModel):
    case_id: str
    description: str = ""
    request: ConversationCreateRequest
    setup: AgentEvalSetup = Field(default_factory=AgentEvalSetup)
    expect: AgentEvalExpectation = Field(default_factory=AgentEvalExpectation)


class AgentEvalDataset(BaseModel):
    schema_version: int = 1
    description: str = ""
    gate: AgentEvalGate = Field(default_factory=AgentEvalGate)
    cases: list[AgentEvalCase] = Field(default_factory=list)


@dataclass
class RetrievalQueryMetricObservation:
    query: str
    target: str = ""
    root_cause_taxonomy: str = ""
    added_rag_hits: int = 0
    added_case_hits: int = 0
    matches_primary_root_cause_taxonomy: bool = False


@dataclass
class AgentEvalObservation:
    status: str
    route: str
    intent: str
    stop_reason: str
    pending_interrupt_type: str
    approval_required: bool
    message: str
    conclusion: str
    primary_root_cause: str
    tool_names: list[str]
    tool_calls_used: int
    evidence: list[str]
    primary_root_cause_taxonomy: str = ""
    transition_notes: list[str] = field(default_factory=list)
    expanded_domains: list[str] = field(default_factory=list)
    expansion_probe_count: int = 0
    expansion_probe_tools: list[str] = field(default_factory=list)
    rejected_tool_call_count: int = 0
    rejected_tool_call_names: list[str] = field(default_factory=list)
    sources_count: int = 0
    retrieval_subquery_count: int = 0
    added_rag_hits: int = 0
    added_case_hits: int = 0
    retrieval_queries: list[str] = field(default_factory=list)
    retrieval_query_metrics: list[RetrievalQueryMetricObservation] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    case_memory_state: str = ""
    case_memory_reason: str = ""
    case_memory_prefetch_status: str = ""
    case_memory_prefetched_case_count: int = 0
    case_memory_tool_search_count: int = 0
    case_memory_last_tool_status: str = ""
    case_memory_last_tool_hit_count: int = 0
    case_memory_tool_failure_count: int = 0
    raw_result: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentEvalCheck:
    name: str
    passed: bool
    expected: Any = None
    actual: Any = None
    detail: str = ""


@dataclass
class AgentEvalScore:
    passed: bool
    passed_checks: int
    total_checks: int
    score: float
    checks: list[AgentEvalCheck] = field(default_factory=list)


@dataclass
class EvalGateResult:
    passed: bool
    passed_checks: int
    total_checks: int
    checks: list[AgentEvalCheck] = field(default_factory=list)


@dataclass
class AgentEvalCaseResult:
    case_id: str
    description: str
    passed: bool
    score: float
    passed_checks: int
    total_checks: int
    duration_ms: int
    observation: AgentEvalObservation | None = None
    checks: list[AgentEvalCheck] = field(default_factory=list)
    error: str = ""


@dataclass
class AgentEvalReport:
    total_cases: int
    passed_cases: int
    failed_cases: int
    errored_cases: int
    pass_rate: float
    avg_tool_calls_used: float = 0.0
    avg_duration_ms: float = 0.0
    expansion_probe_cases: int = 0
    rejected_tool_call_cases: int = 0
    rejected_tool_call_total: int = 0
    stop_reason_counts: dict[str, int] = field(default_factory=dict)
    case_memory_state_counts: dict[str, int] = field(default_factory=dict)
    case_memory_reason_counts: dict[str, int] = field(default_factory=dict)
    gate_result: EvalGateResult | None = None
    results: list[AgentEvalCaseResult] = field(default_factory=list)


def load_agent_eval_dataset(path: str | Path) -> AgentEvalDataset:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return AgentEvalDataset(cases=[AgentEvalCase.model_validate(item) for item in payload])
    return AgentEvalDataset.model_validate(payload)


def _deep_merge_dicts(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge_dicts(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def resolve_tool_profile_mock_responses(
    profile: ToolProfileRef | None,
    *,
    profiles_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    if profile is None:
        return {}
    path = Path(profiles_path or DEFAULT_CASE_PROFILES_PATH)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid tool profile payload: {path}")
    case_payload = payload.get(profile.case_id)
    if not isinstance(case_payload, dict):
        raise ValueError(f"tool profile case not found: {profile.case_id}")
    service_payloads = case_payload.get("services")
    if not isinstance(service_payloads, dict):
        raise ValueError(f"tool profile case has no services: {profile.case_id}")
    service_payload = service_payloads.get(profile.service)
    if not isinstance(service_payload, dict):
        raise ValueError(f"tool profile service not found: {profile.case_id}/{profile.service}")
    default_payload = case_payload.get("default") if isinstance(case_payload.get("default"), dict) else {}
    merged: dict[str, dict[str, Any]] = {}
    for tool_name, result in {**default_payload, **service_payload}.items():
        if isinstance(result, dict):
            merged[str(tool_name)] = dict(result)
    return merged


def extract_eval_observation(result: Mapping[str, Any]) -> AgentEvalObservation:
    payload = dict(result)
    diagnosis = payload.get("diagnosis")
    diagnosis = dict(diagnosis) if isinstance(diagnosis, Mapping) else {}
    react_runtime = diagnosis.get("react_runtime")
    react_runtime = dict(react_runtime) if isinstance(react_runtime, Mapping) else {}
    graph = diagnosis.get("graph")
    graph = dict(graph) if isinstance(graph, Mapping) else {}
    pending_interrupt = payload.get("pending_interrupt")
    pending_interrupt = dict(pending_interrupt) if isinstance(pending_interrupt, Mapping) else {}
    incident_state = diagnosis.get("incident_state")
    incident_state = dict(incident_state) if isinstance(incident_state, Mapping) else {}
    ranked_result = diagnosis.get("ranked_result")
    ranked_result = dict(ranked_result) if isinstance(ranked_result, Mapping) else {}
    routing = diagnosis.get("routing")
    routing = dict(routing) if isinstance(routing, Mapping) else {}
    context_snapshot = diagnosis.get("context_snapshot")
    context_snapshot = dict(context_snapshot) if isinstance(context_snapshot, Mapping) else {}
    retrieval_expansion = context_snapshot.get("retrieval_expansion")
    retrieval_expansion = dict(retrieval_expansion) if isinstance(retrieval_expansion, Mapping) else {}
    case_memory_summary = summarize_case_memory_recall(
        dict(context_snapshot.get("case_recall") or {})
        if isinstance(context_snapshot.get("case_recall"), Mapping)
        else {}
    )
    primary_root_cause = _extract_primary_root_cause(diagnosis, incident_state, ranked_result)
    primary_root_cause_taxonomy = infer_root_cause_taxonomy(
        primary_root_cause
        or str(diagnosis.get("conclusion") or payload.get("message") or "")
    )
    tool_names = _extract_tool_names(diagnosis, incident_state)
    evidence = _extract_evidence(diagnosis)
    tool_calls_used = _extract_tool_call_count(diagnosis, tool_names)
    transition_notes = _extract_string_list(graph.get("transition_notes"))
    expanded_domains = _extract_string_list(react_runtime.get("expanded_domains"))
    expansion_probe_tools = _extract_string_list(react_runtime.get("expansion_probe_tools"))
    rejected_tool_call_names = _extract_string_list(react_runtime.get("rejected_tool_call_names"))
    status = str(payload.get("status") or "")
    approval_required = bool(
        payload.get("approval_request")
        or status == "awaiting_approval"
        or str(pending_interrupt.get("type") or "") == "approval"
    )
    return AgentEvalObservation(
        status=status,
        route=str(diagnosis.get("route") or ""),
        intent=str(routing.get("intent") or ""),
        stop_reason=str(diagnosis.get("stop_reason") or react_runtime.get("stop_reason") or ""),
        pending_interrupt_type=str(pending_interrupt.get("type") or ""),
        approval_required=approval_required,
        message=str(payload.get("message") or ""),
        conclusion=str(diagnosis.get("conclusion") or payload.get("message") or ""),
        primary_root_cause=primary_root_cause,
        primary_root_cause_taxonomy=primary_root_cause_taxonomy,
        tool_names=tool_names,
        tool_calls_used=tool_calls_used,
        evidence=evidence,
        transition_notes=transition_notes,
        expanded_domains=expanded_domains,
        expansion_probe_count=_safe_int(react_runtime.get("expansion_probe_count")),
        expansion_probe_tools=expansion_probe_tools,
        rejected_tool_call_count=_safe_int(react_runtime.get("rejected_tool_call_count")),
        rejected_tool_call_names=rejected_tool_call_names,
        sources_count=len(_extract_string_list(diagnosis.get("sources"))),
        retrieval_subquery_count=len(list(retrieval_expansion.get("subqueries") or [])),
        added_rag_hits=_safe_int(retrieval_expansion.get("added_rag_hits")),
        added_case_hits=_safe_int(retrieval_expansion.get("added_case_hits")),
        retrieval_queries=_extract_retrieval_queries(retrieval_expansion.get("subqueries")),
        retrieval_query_metrics=_extract_retrieval_query_metrics(
            retrieval_expansion.get("subqueries"),
            primary_root_cause_taxonomy=primary_root_cause_taxonomy,
        ),
        missing_evidence=_extract_string_list(retrieval_expansion.get("missing_evidence")),
        case_memory_state=str(case_memory_summary.get("state") or ""),
        case_memory_reason=str(case_memory_summary.get("reason") or ""),
        case_memory_prefetch_status=str(case_memory_summary.get("prefetch_status") or ""),
        case_memory_prefetched_case_count=_safe_int(case_memory_summary.get("prefetched_case_count")),
        case_memory_tool_search_count=_safe_int(case_memory_summary.get("tool_search_count")),
        case_memory_last_tool_status=str(case_memory_summary.get("last_tool_status") or ""),
        case_memory_last_tool_hit_count=_safe_int(case_memory_summary.get("last_tool_hit_count")),
        case_memory_tool_failure_count=_safe_int(case_memory_summary.get("tool_failure_count")),
        raw_result=payload,
    )


def score_agent_eval_case(expectation: AgentEvalExpectation, observation: AgentEvalObservation) -> AgentEvalScore:
    checks: list[AgentEvalCheck] = []

    def add_check(name: str, passed: bool, *, expected: Any = None, actual: Any = None, detail: str = "") -> None:
        checks.append(AgentEvalCheck(name=name, passed=passed, expected=expected, actual=actual, detail=detail))

    if expectation.status is not None:
        add_check(
            "status",
            observation.status == expectation.status,
            expected=expectation.status,
            actual=observation.status,
        )
    if expectation.route is not None:
        add_check(
            "route",
            observation.route == expectation.route,
            expected=expectation.route,
            actual=observation.route,
        )
    if expectation.intent is not None:
        add_check(
            "intent",
            observation.intent == expectation.intent,
            expected=expectation.intent,
            actual=observation.intent,
        )
    if expectation.stop_reason is not None:
        add_check(
            "stop_reason",
            observation.stop_reason == expectation.stop_reason,
            expected=expectation.stop_reason,
            actual=observation.stop_reason,
        )
    if expectation.pending_interrupt_type is not None:
        add_check(
            "pending_interrupt_type",
            observation.pending_interrupt_type == expectation.pending_interrupt_type,
            expected=expectation.pending_interrupt_type,
            actual=observation.pending_interrupt_type,
        )
    if expectation.approval_required is not None:
        add_check(
            "approval_required",
            observation.approval_required is expectation.approval_required,
            expected=expectation.approval_required,
            actual=observation.approval_required,
        )
    if expectation.required_tools:
        missing = [tool for tool in expectation.required_tools if tool not in observation.tool_names]
        add_check(
            "required_tools",
            not missing,
            expected=expectation.required_tools,
            actual=observation.tool_names,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.required_any_tools:
        matched = [tool for tool in expectation.required_any_tools if tool in observation.tool_names]
        min_matches = max(1, int(expectation.required_any_tools_min_matches or 1))
        add_check(
            "required_any_tools",
            len(matched) >= min_matches,
            expected={
                "tools": expectation.required_any_tools,
                "min_matches": min_matches,
            },
            actual=observation.tool_names,
            detail="" if len(matched) >= min_matches else f"matched={matched}",
        )
    if expectation.first_any_tools:
        first_window = max(1, int(expectation.first_any_tools_window or 2))
        first_tools = observation.tool_names[:first_window]
        matched = [tool for tool in expectation.first_any_tools if tool in first_tools]
        min_matches = max(1, int(expectation.first_any_tools_min_matches or 1))
        add_check(
            "first_any_tools",
            len(matched) >= min_matches,
            expected={
                "tools": expectation.first_any_tools,
                "min_matches": min_matches,
                "window": first_window,
            },
            actual=first_tools,
            detail="" if len(matched) >= min_matches else f"matched={matched}",
        )
    if expectation.first_forbidden_tools:
        first_window = max(1, int(expectation.first_any_tools_window or 2))
        first_tools = observation.tool_names[:first_window]
        violated = [tool for tool in expectation.first_forbidden_tools if tool in first_tools]
        add_check(
            "first_forbidden_tools",
            not violated,
            expected={
                "tools": expectation.first_forbidden_tools,
                "window": first_window,
            },
            actual=first_tools,
            detail="" if not violated else f"violated={violated}",
        )
    if expectation.forbidden_tools:
        violated = [tool for tool in expectation.forbidden_tools if tool in observation.tool_names]
        add_check(
            "forbidden_tools",
            not violated,
            expected=expectation.forbidden_tools,
            actual=observation.tool_names,
            detail="" if not violated else f"violated={violated}",
        )
    if expectation.expanded_domains:
        missing = [domain for domain in expectation.expanded_domains if domain not in observation.expanded_domains]
        add_check(
            "expanded_domains",
            not missing,
            expected=expectation.expanded_domains,
            actual=observation.expanded_domains,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.expansion_probe_required is not None:
        add_check(
            "expansion_probe_required",
            (observation.expansion_probe_count > 0) is expectation.expansion_probe_required,
            expected=expectation.expansion_probe_required,
            actual=observation.expansion_probe_count > 0,
            detail=f"count={observation.expansion_probe_count}",
        )
    if expectation.message_contains:
        joined = observation.message.lower()
        missing = [fragment for fragment in expectation.message_contains if fragment.lower() not in joined]
        add_check(
            "message_contains",
            not missing,
            expected=expectation.message_contains,
            actual=observation.message,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.conclusion_contains:
        joined = observation.conclusion.lower()
        missing = [fragment for fragment in expectation.conclusion_contains if fragment.lower() not in joined]
        add_check(
            "conclusion_contains",
            not missing,
            expected=expectation.conclusion_contains,
            actual=observation.conclusion,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.primary_root_cause_contains:
        joined = observation.primary_root_cause.lower()
        missing = [fragment for fragment in expectation.primary_root_cause_contains if fragment.lower() not in joined]
        add_check(
            "primary_root_cause_contains",
            not missing,
            expected=expectation.primary_root_cause_contains,
            actual=observation.primary_root_cause,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.evidence_contains:
        joined = "\n".join(observation.evidence).lower()
        missing = [fragment for fragment in expectation.evidence_contains if fragment.lower() not in joined]
        add_check(
            "evidence_contains",
            not missing,
            expected=expectation.evidence_contains,
            actual=observation.evidence,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.min_tool_calls_used is not None:
        add_check(
            "min_tool_calls_used",
            observation.tool_calls_used >= expectation.min_tool_calls_used,
            expected=expectation.min_tool_calls_used,
            actual=observation.tool_calls_used,
        )
    if expectation.max_tool_calls_used is not None:
        add_check(
            "max_tool_calls_used",
            observation.tool_calls_used <= expectation.max_tool_calls_used,
            expected=expectation.max_tool_calls_used,
            actual=observation.tool_calls_used,
        )
    if expectation.max_rejected_tool_calls is not None:
        add_check(
            "max_rejected_tool_calls",
            observation.rejected_tool_call_count <= expectation.max_rejected_tool_calls,
            expected=expectation.max_rejected_tool_calls,
            actual=observation.rejected_tool_call_count,
        )
    if expectation.min_sources_count is not None:
        add_check(
            "min_sources_count",
            observation.sources_count >= expectation.min_sources_count,
            expected=expectation.min_sources_count,
            actual=observation.sources_count,
        )
    if expectation.max_sources_count is not None:
        add_check(
            "max_sources_count",
            observation.sources_count <= expectation.max_sources_count,
            expected=expectation.max_sources_count,
            actual=observation.sources_count,
        )
    if expectation.min_retrieval_subquery_count is not None:
        add_check(
            "min_retrieval_subquery_count",
            observation.retrieval_subquery_count >= expectation.min_retrieval_subquery_count,
            expected=expectation.min_retrieval_subquery_count,
            actual=observation.retrieval_subquery_count,
        )
    if expectation.max_retrieval_subquery_count is not None:
        add_check(
            "max_retrieval_subquery_count",
            observation.retrieval_subquery_count <= expectation.max_retrieval_subquery_count,
            expected=expectation.max_retrieval_subquery_count,
            actual=observation.retrieval_subquery_count,
        )
    if expectation.min_added_rag_hits is not None:
        add_check(
            "min_added_rag_hits",
            observation.added_rag_hits >= expectation.min_added_rag_hits,
            expected=expectation.min_added_rag_hits,
            actual=observation.added_rag_hits,
        )
    if expectation.max_added_rag_hits is not None:
        add_check(
            "max_added_rag_hits",
            observation.added_rag_hits <= expectation.max_added_rag_hits,
            expected=expectation.max_added_rag_hits,
            actual=observation.added_rag_hits,
        )
    if expectation.min_added_case_hits is not None:
        add_check(
            "min_added_case_hits",
            observation.added_case_hits >= expectation.min_added_case_hits,
            expected=expectation.min_added_case_hits,
            actual=observation.added_case_hits,
        )
    if expectation.max_added_case_hits is not None:
        add_check(
            "max_added_case_hits",
            observation.added_case_hits <= expectation.max_added_case_hits,
            expected=expectation.max_added_case_hits,
            actual=observation.added_case_hits,
        )
    if expectation.retrieval_query_contains:
        joined = "\n".join(observation.retrieval_queries).lower()
        missing = [fragment for fragment in expectation.retrieval_query_contains if fragment.lower() not in joined]
        add_check(
            "retrieval_query_contains",
            not missing,
            expected=expectation.retrieval_query_contains,
            actual=observation.retrieval_queries,
            detail="" if not missing else f"missing={missing}",
        )
    for item in expectation.retrieval_query_metrics:
        observed_metric = next(
            (
                metric
                for metric in observation.retrieval_query_metrics
                if item.query_contains.lower() in metric.query.lower()
            ),
            None,
        )
        add_check(
            f"retrieval_query_metrics[{item.query_contains}].present",
            observed_metric is not None,
            expected=item.query_contains,
            actual=[metric.query for metric in observation.retrieval_query_metrics],
        )
        if observed_metric is None:
            continue
        if item.added_rag_hits is not None:
            add_check(
                f"retrieval_query_metrics[{item.query_contains}].added_rag_hits",
                observed_metric.added_rag_hits == item.added_rag_hits,
                expected=item.added_rag_hits,
                actual=observed_metric.added_rag_hits,
            )
        if item.added_case_hits is not None:
            add_check(
                f"retrieval_query_metrics[{item.query_contains}].added_case_hits",
                observed_metric.added_case_hits == item.added_case_hits,
                expected=item.added_case_hits,
                actual=observed_metric.added_case_hits,
            )
        if item.root_cause_taxonomy is not None:
            add_check(
                f"retrieval_query_metrics[{item.query_contains}].root_cause_taxonomy",
                observed_metric.root_cause_taxonomy == item.root_cause_taxonomy,
                expected=item.root_cause_taxonomy,
                actual=observed_metric.root_cause_taxonomy,
            )
        if item.matches_primary_root_cause_taxonomy is not None:
            add_check(
                f"retrieval_query_metrics[{item.query_contains}].matches_primary_root_cause_taxonomy",
                observed_metric.matches_primary_root_cause_taxonomy == item.matches_primary_root_cause_taxonomy,
                expected=item.matches_primary_root_cause_taxonomy,
                actual=observed_metric.matches_primary_root_cause_taxonomy,
            )
    if expectation.missing_evidence_contains:
        joined = "\n".join(observation.missing_evidence).lower()
        missing = [fragment for fragment in expectation.missing_evidence_contains if fragment.lower() not in joined]
        add_check(
            "missing_evidence_contains",
            not missing,
            expected=expectation.missing_evidence_contains,
            actual=observation.missing_evidence,
            detail="" if not missing else f"missing={missing}",
        )

    passed_checks = sum(1 for check in checks if check.passed)
    total_checks = len(checks)
    score = 1.0 if total_checks == 0 else round(passed_checks / total_checks, 3)
    return AgentEvalScore(
        passed=passed_checks == total_checks,
        passed_checks=passed_checks,
        total_checks=total_checks,
        score=score,
        checks=checks,
    )


def build_eval_report(results: Sequence[AgentEvalCaseResult]) -> AgentEvalReport:
    total_cases = len(results)
    passed_cases = sum(1 for item in results if item.passed)
    errored_cases = sum(1 for item in results if item.error)
    failed_cases = sum(1 for item in results if not item.passed and not item.error)
    pass_rate = 1.0 if total_cases == 0 else round(passed_cases / total_cases, 3)
    observed = [item.observation for item in results if item.observation is not None]
    avg_tool_calls_used = (
        round(sum(item.tool_calls_used for item in observed) / len(observed), 3) if observed else 0.0
    )
    avg_duration_ms = round(sum(item.duration_ms for item in results) / total_cases, 3) if total_cases else 0.0
    expansion_probe_cases = sum(1 for item in observed if item.expansion_probe_count > 0)
    rejected_tool_call_cases = sum(1 for item in observed if item.rejected_tool_call_count > 0)
    rejected_tool_call_total = sum(item.rejected_tool_call_count for item in observed)
    stop_reason_counts: dict[str, int] = {}
    case_memory_state_counts: dict[str, int] = {}
    case_memory_reason_counts: dict[str, int] = {}
    for item in observed:
        stop_reason = str(item.stop_reason or "").strip()
        if stop_reason:
            stop_reason_counts[stop_reason] = stop_reason_counts.get(stop_reason, 0) + 1
        case_memory_state = str(item.case_memory_state or "skipped").strip() or "skipped"
        case_memory_state_counts[case_memory_state] = case_memory_state_counts.get(case_memory_state, 0) + 1
        case_memory_reason = str(item.case_memory_reason or "").strip()
        if case_memory_reason:
            case_memory_reason_counts[case_memory_reason] = case_memory_reason_counts.get(case_memory_reason, 0) + 1
    return AgentEvalReport(
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        errored_cases=errored_cases,
        pass_rate=pass_rate,
        avg_tool_calls_used=avg_tool_calls_used,
        avg_duration_ms=avg_duration_ms,
        expansion_probe_cases=expansion_probe_cases,
        rejected_tool_call_cases=rejected_tool_call_cases,
        rejected_tool_call_total=rejected_tool_call_total,
        stop_reason_counts=stop_reason_counts,
        case_memory_state_counts=case_memory_state_counts,
        case_memory_reason_counts=case_memory_reason_counts,
        results=list(results),
    )


def evaluate_agent_eval_gate(gate: AgentEvalGate, report: AgentEvalReport) -> EvalGateResult | None:
    thresholds = gate.model_dump(exclude_none=True)
    if not thresholds:
        return None

    checks: list[AgentEvalCheck] = []

    def add_check(name: str, passed: bool, *, expected: Any = None, actual: Any = None, detail: str = "") -> None:
        checks.append(AgentEvalCheck(name=name, passed=passed, expected=expected, actual=actual, detail=detail))

    if gate.min_pass_rate is not None:
        add_check(
            "min_pass_rate",
            report.pass_rate >= gate.min_pass_rate,
            expected=gate.min_pass_rate,
            actual=report.pass_rate,
        )
    if gate.max_avg_tool_calls_used is not None:
        add_check(
            "max_avg_tool_calls_used",
            report.avg_tool_calls_used <= gate.max_avg_tool_calls_used,
            expected=gate.max_avg_tool_calls_used,
            actual=report.avg_tool_calls_used,
        )
    if gate.max_avg_duration_ms is not None:
        add_check(
            "max_avg_duration_ms",
            report.avg_duration_ms <= gate.max_avg_duration_ms,
            expected=gate.max_avg_duration_ms,
            actual=report.avg_duration_ms,
        )
    if gate.max_expansion_probe_cases is not None:
        add_check(
            "max_expansion_probe_cases",
            report.expansion_probe_cases <= gate.max_expansion_probe_cases,
            expected=gate.max_expansion_probe_cases,
            actual=report.expansion_probe_cases,
        )
    if gate.max_rejected_tool_call_cases is not None:
        add_check(
            "max_rejected_tool_call_cases",
            report.rejected_tool_call_cases <= gate.max_rejected_tool_call_cases,
            expected=gate.max_rejected_tool_call_cases,
            actual=report.rejected_tool_call_cases,
        )
    if gate.max_rejected_tool_call_total is not None:
        add_check(
            "max_rejected_tool_call_total",
            report.rejected_tool_call_total <= gate.max_rejected_tool_call_total,
            expected=gate.max_rejected_tool_call_total,
            actual=report.rejected_tool_call_total,
        )

    passed_checks = sum(1 for check in checks if check.passed)
    total_checks = len(checks)
    return EvalGateResult(
        passed=passed_checks == total_checks,
        passed_checks=passed_checks,
        total_checks=total_checks,
        checks=checks,
    )


class AgentEvalRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        profiles_path: str | Path | None = None,
        rag_enabled: bool = False,
        require_llm_enabled: bool = True,
        configure_orchestrator: Callable[[SupervisorOrchestrator], None] | None = None,
    ) -> None:
        self.settings = settings
        self.profiles_path = Path(profiles_path or DEFAULT_CASE_PROFILES_PATH)
        self.rag_enabled = rag_enabled
        self.require_llm_enabled = require_llm_enabled
        self.configure_orchestrator = configure_orchestrator

    async def run_case(self, case: AgentEvalCase) -> AgentEvalCaseResult:
        started_at = perf_counter()
        with TemporaryDirectory() as temp_dir:
            try:
                db_path = str(Path(temp_dir) / f"{case.case_id}.db")
                orchestrator = self._build_orchestrator(db_path)
                self._configure_case_mocks(orchestrator, case)
                if self.configure_orchestrator is not None:
                    self.configure_orchestrator(orchestrator)
                if case.setup.llm_mode == "disabled":
                    orchestrator.react_supervisor.llm = DisabledEvalLLM()
                if self.require_llm_enabled and case.setup.llm_mode != "disabled" and not orchestrator.react_supervisor.llm.enabled:
                    raise RuntimeError("LLM is not enabled by current settings")
                request = self._build_request(case)
                result = await orchestrator.start_conversation(request)
                observation = extract_eval_observation(result)
                score = score_agent_eval_case(case.expect, observation)
                duration_ms = int((perf_counter() - started_at) * 1000)
                return AgentEvalCaseResult(
                    case_id=case.case_id,
                    description=case.description,
                    passed=score.passed,
                    score=score.score,
                    passed_checks=score.passed_checks,
                    total_checks=score.total_checks,
                    duration_ms=duration_ms,
                    observation=observation,
                    checks=score.checks,
                )
            except Exception as exc:
                duration_ms = int((perf_counter() - started_at) * 1000)
                return AgentEvalCaseResult(
                    case_id=case.case_id,
                    description=case.description,
                    passed=False,
                    score=0.0,
                    passed_checks=0,
                    total_checks=0,
                    duration_ms=duration_ms,
                    error=f"{exc.__class__.__name__}: {exc}",
                )

    async def run_dataset(
        self,
        dataset: AgentEvalDataset,
        *,
        selected_case_ids: Sequence[str] | None = None,
        fail_fast: bool = False,
    ) -> AgentEvalReport:
        selected = set(selected_case_ids or [])
        cases = [case for case in dataset.cases if not selected or case.case_id in selected]
        results: list[AgentEvalCaseResult] = []
        for case in cases:
            case_result = await self.run_case(case)
            results.append(case_result)
            if fail_fast and (case_result.error or not case_result.passed):
                break
        return build_eval_report(results)

    def _build_request(self, case: AgentEvalCase) -> ConversationCreateRequest:
        request = case.request.model_copy(deep=True)
        profile_mocks = resolve_tool_profile_mock_responses(
            case.setup.tool_profile,
            profiles_path=self.profiles_path,
        )
        merged_mock_tool_responses = {
            **profile_mocks,
            **dict(request.mock_tool_responses or {}),
            **dict(case.setup.mock_tool_responses or {}),
        }
        merged_world_state = _deep_merge_dicts(
            dict(request.mock_world_state or {}),
            dict(case.setup.world_state or {}),
        )
        return request.model_copy(
            update={
                "mock_tool_responses": merged_mock_tool_responses,
                "mock_world_state": merged_world_state,
            }
        )

    def _build_orchestrator(self, db_path: str) -> SupervisorOrchestrator:
        settings = replace(
            self.settings,
            approval_db_path=db_path,
            rag_enabled=self.rag_enabled,
            orchestration_mode="react_tool_first",
        )
        approval_store = ApprovalStore(db_path)
        session_store = SessionStore(db_path)
        interrupt_store = InterruptStore(db_path)
        checkpoint_store = CheckpointStore(db_path)
        process_memory_store = ProcessMemoryStore(db_path)
        execution_store = ExecutionStore(db_path)
        incident_case_store = IncidentCaseStore(db_path)
        system_event_store = SystemEventStore(db_path)
        return SupervisorOrchestrator(
            settings,
            approval_store,
            session_store,
            interrupt_store,
            checkpoint_store,
            process_memory_store,
            execution_store=execution_store,
            incident_case_store=incident_case_store,
            system_event_store=system_event_store,
        )

    def _configure_case_mocks(self, orchestrator: SupervisorOrchestrator, case: AgentEvalCase) -> None:
        if case.setup.retrieval_planner_llm_mode == "disabled":
            orchestrator.retrieval_planner.llm = DisabledEvalLLM()

        default_bundle = (
            RAGContextBundle.model_validate(case.setup.mock_rag_context)
            if dict(case.setup.mock_rag_context or {})
            else None
        )
        bundle_by_query = {
            str(query).strip(): RAGContextBundle.model_validate(payload)
            for query, payload in dict(case.setup.mock_rag_context_by_query or {}).items()
            if str(query).strip()
        }
        if default_bundle is not None or bundle_by_query:
            original_retrieve_for_request = orchestrator.knowledge_service.retrieve_for_request
            original_retrieve_query = orchestrator.knowledge_service.retrieve_query

            async def retrieve_for_request(request, *, top_k=None):
                query = str(getattr(request, "message", "") or "").strip()
                bundle = bundle_by_query.get(query) or default_bundle
                if bundle is not None:
                    return bundle.model_copy(deep=True)
                return await original_retrieve_for_request(request, top_k=top_k)

            async def retrieve_query(*, query: str, service: str = "", top_k: int | None = None):
                bundle = bundle_by_query.get(str(query).strip()) or default_bundle
                if bundle is not None:
                    return bundle.model_copy(deep=True)
                return await original_retrieve_query(query=query, service=service, top_k=top_k)

            orchestrator.knowledge_service.retrieve_for_request = retrieve_for_request
            orchestrator.knowledge_service.retrieve_query = retrieve_query

        default_similar_cases = [
            SimilarIncidentCase.model_validate(item)
            for item in list(case.setup.mock_similar_cases or [])
        ]
        similar_cases_by_query = {
            str(query).strip(): [
                SimilarIncidentCase.model_validate(item)
                for item in list(payload or [])
            ]
            for query, payload in dict(case.setup.mock_similar_cases_by_query or {}).items()
            if str(query).strip()
        }
        if default_similar_cases or similar_cases_by_query:
            original_recall = orchestrator.case_retriever.recall

            async def recall(
                *,
                service: str,
                cluster: str,
                namespace: str,
                message: str,
                session_id: str,
                limit: int = 6,
                failure_mode: str = "",
                root_cause_taxonomy: str = "",
            ):
                query = str(message or "").strip()
                matched_cases = similar_cases_by_query.get(query)
                if matched_cases is not None:
                    return [item.model_copy(deep=True) for item in matched_cases]
                if default_similar_cases:
                    return [item.model_copy(deep=True) for item in default_similar_cases]
                return await original_recall(
                    service=service,
                    cluster=cluster,
                    namespace=namespace,
                    message=message,
                    session_id=session_id,
                    limit=limit,
                    failure_mode=failure_mode,
                    root_cause_taxonomy=root_cause_taxonomy,
                )

            orchestrator.case_retriever.recall = AsyncMock(side_effect=recall)

        if case.setup.mock_retrieval_expansion:
            expansion = RetrievalExpansion.model_validate(case.setup.mock_retrieval_expansion)
            orchestrator.retrieval_planner.plan = AsyncMock(return_value=expansion.model_copy(deep=True))


def _extract_primary_root_cause(
    diagnosis: Mapping[str, Any],
    incident_state: Mapping[str, Any],
    ranked_result: Mapping[str, Any],
) -> str:
    primary = ranked_result.get("primary")
    if isinstance(primary, Mapping) and primary.get("root_cause"):
        return str(primary.get("root_cause") or "")
    metadata = incident_state.get("metadata")
    if isinstance(metadata, Mapping) and metadata.get("selected_root_cause"):
        return str(metadata.get("selected_root_cause") or "")
    verification_results = diagnosis.get("verification_results")
    if isinstance(verification_results, Sequence):
        for item in verification_results:
            if isinstance(item, Mapping) and item.get("root_cause"):
                return str(item.get("root_cause") or "")
    return ""


def _extract_tool_names(diagnosis: Mapping[str, Any], incident_state: Mapping[str, Any]) -> list[str]:
    tool_names: list[str] = []

    def append(tool_name: Any) -> None:
        value = str(tool_name or "").strip()
        if value and value not in tool_names:
            tool_names.append(value)

    observations = diagnosis.get("observations")
    if isinstance(observations, Sequence):
        for item in observations:
            if isinstance(item, Mapping):
                append(item.get("tool_name"))

    verification_results = diagnosis.get("verification_results")
    if isinstance(verification_results, Sequence):
        for result in verification_results:
            if not isinstance(result, Mapping):
                continue
            evidence_items = result.get("evidence_items")
            if isinstance(evidence_items, Sequence):
                for item in evidence_items:
                    if isinstance(item, Mapping):
                        append(item.get("skill"))

    incident_observations = incident_state.get("metadata", {})
    if isinstance(incident_observations, Mapping):
        react_observations = incident_observations.get("react_observations")
        if isinstance(react_observations, Sequence):
            for item in react_observations:
                if isinstance(item, Mapping):
                    append(item.get("tool_name"))

    return tool_names


def _extract_evidence(diagnosis: Mapping[str, Any]) -> list[str]:
    evidence: list[str] = []
    raw_evidence = diagnosis.get("evidence")
    if isinstance(raw_evidence, Sequence) and not isinstance(raw_evidence, (str, bytes)):
        for item in raw_evidence:
            value = str(item or "").strip()
            if value and value not in evidence:
                evidence.append(value)
    observations = diagnosis.get("observations")
    if isinstance(observations, Sequence):
        for item in observations:
            if not isinstance(item, Mapping):
                continue
            result = item.get("result")
            result = dict(result) if isinstance(result, Mapping) else {}
            for entry in list(result.get("evidence") or []):
                value = str(entry or "").strip()
                if value and value not in evidence:
                    evidence.append(value)
    return evidence


def _extract_tool_call_count(diagnosis: Mapping[str, Any], tool_names: Sequence[str]) -> int:
    try:
        return int(diagnosis.get("tool_calls_used") or 0)
    except Exception:
        return len(tool_names)


def _extract_string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def _extract_retrieval_queries(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    queries: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("query") or "").strip()
        if text and text not in queries:
            queries.append(text)
    return queries


def _extract_retrieval_query_metrics(
    value: Any,
    *,
    primary_root_cause_taxonomy: str = "",
) -> list[RetrievalQueryMetricObservation]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    metrics: list[RetrievalQueryMetricObservation] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        query = str(item.get("query") or "").strip()
        if not query:
            continue
        query_root_cause_taxonomy = str(item.get("root_cause_taxonomy") or "").strip()
        metrics.append(
            RetrievalQueryMetricObservation(
                query=query,
                target=str(item.get("target") or ""),
                root_cause_taxonomy=query_root_cause_taxonomy,
                added_rag_hits=_safe_int(item.get("added_rag_hits")),
                added_case_hits=_safe_int(item.get("added_case_hits")),
                matches_primary_root_cause_taxonomy=bool(
                    query_root_cause_taxonomy
                    and primary_root_cause_taxonomy
                    and query_root_cause_taxonomy == primary_root_cause_taxonomy
                ),
            )
        )
    return metrics


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def serialize_report(report: AgentEvalReport) -> dict[str, Any]:
    return {
        "total_cases": report.total_cases,
        "passed_cases": report.passed_cases,
        "failed_cases": report.failed_cases,
        "errored_cases": report.errored_cases,
        "pass_rate": report.pass_rate,
        "avg_tool_calls_used": report.avg_tool_calls_used,
        "avg_duration_ms": report.avg_duration_ms,
        "expansion_probe_cases": report.expansion_probe_cases,
        "rejected_tool_call_cases": report.rejected_tool_call_cases,
        "rejected_tool_call_total": report.rejected_tool_call_total,
        "stop_reason_counts": dict(report.stop_reason_counts),
        "case_memory_state_counts": dict(report.case_memory_state_counts),
        "case_memory_reason_counts": dict(report.case_memory_reason_counts),
        "gate_result": (
            {
                "passed": report.gate_result.passed,
                "passed_checks": report.gate_result.passed_checks,
                "total_checks": report.gate_result.total_checks,
                "checks": [asdict(check) for check in report.gate_result.checks],
            }
            if report.gate_result is not None
            else None
        ),
        "results": [
            {
                "case_id": item.case_id,
                "description": item.description,
                "passed": item.passed,
                "score": item.score,
                "passed_checks": item.passed_checks,
                "total_checks": item.total_checks,
                "duration_ms": item.duration_ms,
                "error": item.error,
                "checks": [asdict(check) for check in item.checks],
                "observation": asdict(item.observation) if item.observation is not None else None,
            }
            for item in report.results
        ],
    }
