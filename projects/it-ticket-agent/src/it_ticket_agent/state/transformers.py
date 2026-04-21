from __future__ import annotations

from typing import Any, Dict

from ..service_names import infer_service_name
from ..schemas import TicketRequest, model_to_dict
from .incident_state import IncidentState
from .models import ApprovalProposal, ExecutionResult


APPROVAL_REQUIRED_RISKS = {"high", "critical"}


def risk_requires_approval(risk: str) -> bool:
    return str(risk).lower() in APPROVAL_REQUIRED_RISKS


def build_initial_incident_state(
    request: TicketRequest,
    *,
    routing: Dict[str, Any] | None = None,
) -> IncidentState:
    routing_payload = model_to_dict(routing) if routing is not None and not isinstance(routing, dict) else dict(routing or {})
    resolved_service = request.service or infer_service_name(request.message)
    return IncidentState(
        ticket_id=request.ticket_id,
        user_id=request.user_id,
        thread_id=request.ticket_id,
        message=request.message,
        service=resolved_service,
        environment=request.environment,
        host_identifier=request.host_identifier,
        db_name=request.db_name,
        db_type=request.db_type,
        cluster=request.cluster,
        namespace=request.namespace,
        channel=request.channel,
        status="received",
        routing=routing_payload,
        shared_context={
            "message": request.message,
            "service": resolved_service or "",
            "environment": request.environment or "",
            "host_identifier": request.host_identifier or "",
            "db_name": request.db_name or "",
            "db_type": request.db_type or "",
            "cluster": request.cluster,
            "namespace": request.namespace,
            "channel": request.channel,
            "mock_scenario": request.mock_scenario or "",
            "mock_scenarios": dict(request.mock_scenarios or {}),
            "mock_tool_responses": dict(request.mock_tool_responses or {}),
            "mock_world_state": dict(request.mock_world_state or {}),
        },
    )


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
    analyzed_state.status = "ranked"
    analyzed_state.final_summary = "主根因候选已收敛，需要审批后执行动作。"
    analyzed_state.approval_proposals = [
        ApprovalProposal(
            proposal_id="proposal-demo-1",
            source_agent="ranker",
            action="cicd.rollback_service",
            risk="high",
            reason="最近一次发布后 5xx 激增，建议在确认影响面后执行回滚。",
            params={"service": "order-service", "environment": "prod-shanghai-1"},
            requires_approval=True,
            title="cicd.rollback_service",
            target="order-service",
            evidence=["Pipeline 状态失败", "发布与故障时间重合"],
            metadata={},
        )
    ]
    verified_state = analyzed_state.model_copy(deep=True)
    verified_state.status = "completed"
    verified_state.execution_results = [
        ExecutionResult(
            action="cicd.rollback_service",
            status="completed",
            summary="已执行回滚并恢复服务。",
            payload={"service": "order-service", "result": "success"},
            evidence=["rollback job finished", "error rate recovered"],
            risk="high",
            executor="approval_resume_graph",
            metadata={},
        )
    ]
    return {
        "initial_incident_state": initial_state.model_dump(),
        "awaiting_approval_state": analyzed_state.model_dump(),
        "completed_state": verified_state.model_dump(),
    }
