from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional
from uuid import NAMESPACE_URL, uuid5

from ..runtime.contracts import AgentAction, AgentResult, RoutingDecision
from ..service_names import infer_service_name
from ..schemas import TicketRequest, model_to_dict
from .incident_state import IncidentState
from .models import (
    ApprovalProposal,
    ExecutionResult,
    IncidentFinding,
    RAGContextBundle,
    SubAgentResult,
    ToolResultSnapshot,
    VerificationResult,
)

if TYPE_CHECKING:
    from ..tools.contracts import ToolExecutionResult


APPROVAL_REQUIRED_RISKS = {"high", "critical"}


def risk_requires_approval(risk: str) -> bool:
    return str(risk).lower() in APPROVAL_REQUIRED_RISKS


def approval_proposal_from_action(
    action: AgentAction,
    *,
    agent_name: str,
    ticket_id: str = "",
    evidence: Optional[Iterable[str]] = None,
    index: int = 0,
) -> ApprovalProposal:
    proposal_key = f"{ticket_id}:{agent_name}:{action.action}:{index}"
    return ApprovalProposal(
        proposal_id=str(uuid5(NAMESPACE_URL, proposal_key)),
        source_agent=agent_name,
        action=action.action,
        risk=str(action.risk).lower(),
        reason=action.reason,
        params=dict(action.params),
        requires_approval=risk_requires_approval(action.risk),
        title=action.action,
        target=str(action.params.get("service") or action.params.get("target") or "") or None,
        evidence=list(evidence or []),
        metadata={"legacy_action": True},
    )


def tool_result_snapshot_from_legacy(result: "ToolExecutionResult" | Dict[str, Any]) -> ToolResultSnapshot:
    payload = model_to_dict(result) if not isinstance(result, dict) else dict(result)
    return ToolResultSnapshot(
        tool_name=str(payload.get("tool_name", "unknown_tool")),
        status=str(payload.get("status", "unknown")),
        summary=str(payload.get("summary", "")),
        payload=dict(payload.get("payload", {})),
        evidence=list(payload.get("evidence", [])),
        risk=str(payload.get("risk", "low")).lower(),
    )


def subagent_result_from_agent_result(result: AgentResult, *, ticket_id: str = "") -> SubAgentResult:
    proposals = [
        approval_proposal_from_action(
            action,
            agent_name=result.agent_name,
            ticket_id=ticket_id,
            evidence=result.evidence,
            index=index,
        )
        for index, action in enumerate(result.recommended_actions)
    ]
    return SubAgentResult(
        agent_name=result.agent_name,
        domain=result.domain,
        status=result.status,
        summary=result.summary,
        execution_path=result.execution_path,
        findings=[
            IncidentFinding(title=item.title, detail=item.detail, severity=item.severity)
            for item in result.findings
        ],
        evidence=list(result.evidence),
        tool_results=[tool_result_snapshot_from_legacy(item) for item in result.tool_results],
        approval_proposals=proposals,
        risk_level=str(result.risk_level).lower(),
        confidence=result.confidence,
        open_questions=list(result.open_questions),
        needs_handoff=result.needs_handoff,
        raw_refs=list(result.raw_refs),
        clarification_request=model_to_dict(result.clarification_request) if result.clarification_request is not None else None,
        metadata={"legacy_contract": "AgentResult"},
    )


def build_initial_incident_state(
    request: TicketRequest,
    *,
    routing: RoutingDecision | Dict[str, Any] | None = None,
) -> IncidentState:
    routing_payload = model_to_dict(routing) if routing is not None and not isinstance(routing, dict) else dict(routing or {})
    resolved_service = request.service or infer_service_name(request.message)
    return IncidentState(
        ticket_id=request.ticket_id,
        user_id=request.user_id,
        thread_id=request.ticket_id,
        message=request.message,
        service=resolved_service,
        cluster=request.cluster,
        namespace=request.namespace,
        channel=request.channel,
        status="received",
        routing=routing_payload,
        shared_context={
            "message": request.message,
            "service": resolved_service or "",
            "cluster": request.cluster,
            "namespace": request.namespace,
            "channel": request.channel,
            "mock_scenario": request.mock_scenario or "",
            "mock_scenarios": dict(request.mock_scenarios or {}),
            "mock_tool_responses": dict(request.mock_tool_responses or {}),
        },
    )


def incident_state_from_legacy(
    request: TicketRequest,
    *,
    routing: RoutingDecision | Dict[str, Any] | None = None,
    agent_result: AgentResult | None = None,
    rag_context: RAGContextBundle | Dict[str, Any] | None = None,
) -> IncidentState:
    state = build_initial_incident_state(request, routing=routing)
    if rag_context is not None:
        state.rag_context = rag_context if isinstance(rag_context, RAGContextBundle) else RAGContextBundle(**rag_context)
    if agent_result is None:
        return state

    subagent_result = subagent_result_from_agent_result(agent_result, ticket_id=request.ticket_id)
    state.subagent_results.append(subagent_result)
    state.approval_proposals.extend(subagent_result.approval_proposals)
    state.open_questions.extend(subagent_result.open_questions)
    if subagent_result.clarification_request is not None:
        state.clarification_requests.append(dict(subagent_result.clarification_request))
        state.metadata["clarification_request"] = dict(subagent_result.clarification_request)
        state.status = "awaiting_clarification"
    else:
        state.status = "analyzed"
    state.final_summary = subagent_result.summary
    return state


def incident_state_from_parallel_results(
    request: TicketRequest,
    *,
    routing: RoutingDecision | Dict[str, Any] | None = None,
    agent_results: Iterable[AgentResult] | None = None,
    aggregated_result: AgentResult | None = None,
    dispatch_failures: Iterable[Dict[str, Any]] | None = None,
    rag_context: RAGContextBundle | Dict[str, Any] | None = None,
) -> IncidentState:
    state = build_initial_incident_state(request, routing=routing)
    if rag_context is not None:
        state.rag_context = rag_context if isinstance(rag_context, RAGContextBundle) else RAGContextBundle(**rag_context)
    subagent_results = [
        subagent_result_from_agent_result(result, ticket_id=request.ticket_id)
        for result in (agent_results or [])
    ]
    state.subagent_results.extend(subagent_results)

    open_questions: list[str] = []
    seen_questions: set[str] = set()
    for subagent_result in subagent_results:
        state.approval_proposals.extend(subagent_result.approval_proposals)
        for question in subagent_result.open_questions:
            normalized = str(question or "").strip()
            if not normalized or normalized in seen_questions:
                continue
            open_questions.append(normalized)
            seen_questions.add(normalized)
        if subagent_result.clarification_request is not None:
            state.clarification_requests.append(dict(subagent_result.clarification_request))

    if aggregated_result is not None:
        state.final_summary = aggregated_result.summary
        state.metadata["aggregated_result"] = model_to_dict(aggregated_result)
        for question in aggregated_result.open_questions:
            normalized = str(question or "").strip()
            if not normalized or normalized in seen_questions:
                continue
            open_questions.append(normalized)
            seen_questions.add(normalized)
        if aggregated_result.clarification_request is not None:
            clarification_payload = model_to_dict(aggregated_result.clarification_request)
            state.metadata["clarification_request"] = clarification_payload
            if clarification_payload not in state.clarification_requests:
                state.clarification_requests.append(clarification_payload)
            state.status = "awaiting_clarification"
        elif aggregated_result.status == "failed":
            state.status = "failed"
        else:
            state.status = "analyzed"
    elif subagent_results:
        state.status = "analyzed"

    state.open_questions = open_questions
    failures = [dict(item) for item in (dispatch_failures or []) if isinstance(item, dict)]
    if failures:
        state.metadata["parallel_dispatch_failures"] = failures
    return state


def example_payloads() -> Dict[str, Dict[str, Any]]:
    initial_state = IncidentState(
        ticket_id="T-1001",
        user_id="u-demo",
        thread_id="T-1001",
        message="发布后订单服务 5xx 激增",
        service="order-service",
        cluster="prod-shanghai-1",
        namespace="orders",
        channel="feishu",
        status="received",
        shared_context={
            "message": "发布后订单服务 5xx 激增",
            "service": "order-service",
            "cluster": "prod-shanghai-1",
            "namespace": "orders",
            "channel": "feishu",
        },
    )
    analyzed_state = initial_state.model_copy(deep=True)
    analyzed_state.status = "analyzed"
    analyzed_state.final_summary = "近期发布与故障时间窗口重合，需要进一步确认是否回滚。"
    analyzed_state.approval_proposals = [
        ApprovalProposal(
            proposal_id="proposal-demo-1",
            source_agent="cicd_agent",
            action="cicd.rollback_service",
            risk="high",
            reason="最近一次发布后 5xx 激增，建议在确认影响面后执行回滚。",
            params={"service": "order-service", "environment": "prod-shanghai-1"},
            requires_approval=True,
            title="cicd.rollback_service",
            target="order-service",
            evidence=["Pipeline 状态失败", "发布与故障时间重合"],
            metadata={"legacy_action": True},
        )
    ]
    verified_state = analyzed_state.model_copy(deep=True)
    verified_state.status = "completed"
    verified_state.execution_results = [
        ExecutionResult(
            action="cicd.rollback_service",
            status="completed",
            summary="回滚任务已提交并执行完成。",
            payload={"job_id": "rollback-123"},
            evidence=["执行系统返回 completed"],
            risk="high",
            executor="executor",
            metadata={},
        )
    ]
    verified_state.verification_results = [
        VerificationResult(
            status="passed",
            summary="错误率已恢复到基线范围。",
            checks_passed=["5xx 下降", "告警恢复"],
            checks_failed=[],
            evidence=["监控窗口 15 分钟恢复正常"],
            payload={},
            metadata={},
        )
    ]
    return {
        "initial_incident_state": initial_state.model_dump(),
        "analyzed_incident_state": analyzed_state.model_dump(),
        "verified_incident_state": verified_state.model_dump(),
    }
