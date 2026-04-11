from __future__ import annotations

from typing import Any, Dict, List, Literal

from typing_extensions import TypedDict

from ..context.models import ExecutionContext
from ..runtime.contracts import SmartRouterDecision
from ..schemas import ApprovalDecisionRequest, TicketRequest
from ..state.incident_state import IncidentState
from ..state.models import ContextSnapshot, Hypothesis, RankedResult, VerificationResult
from ..state.transformers import build_initial_incident_state


class TicketGraphState(TypedDict, total=False):
    request: TicketRequest
    session_id: str
    thread_id: str
    execution_context: ExecutionContext
    incident_state: IncidentState
    route_decision: SmartRouterDecision
    context_snapshot: ContextSnapshot
    hypotheses: List[Hypothesis]
    verification_results: List[VerificationResult]
    ranked_result: RankedResult
    approval_request: Dict[str, Any] | None
    response: Dict[str, Any]
    pending_node: str | None
    resume_kind: Literal["ticket"]
    transition_notes: List[str]


class ApprovalGraphState(TypedDict, total=False):
    approval_record: Dict[str, Any]
    session_id: str
    thread_id: str
    approval_request_domain: Dict[str, Any]
    approval_decision_request: ApprovalDecisionRequest
    approval_decision_record: Dict[str, Any]
    incident_state: IncidentState
    approval_result: Dict[str, Any]
    response: Dict[str, Any]
    pending_node: str | None
    resume_action: Literal["execute_approved_action", "finalize"] | None
    resume_kind: Literal["approval_decision"]
    transition_notes: List[str]


GraphResponse = Dict[str, Any]


def build_ticket_graph_input(
    request: TicketRequest,
    *,
    session_id: str | None = None,
    thread_id: str | None = None,
    incident_state: IncidentState | None = None,
) -> TicketGraphState:
    next_incident_state = incident_state or build_initial_incident_state(request)
    resolved_session_id = session_id or next_incident_state.thread_id or request.ticket_id
    resolved_thread_id = thread_id or next_incident_state.thread_id or request.ticket_id
    next_incident_state.thread_id = resolved_thread_id
    return {
        "request": request,
        "session_id": resolved_session_id,
        "thread_id": resolved_thread_id,
        "incident_state": next_incident_state,
        "pending_node": "ingest",
        "resume_kind": "ticket",
        "transition_notes": [],
    }


def build_approval_graph_input(
    approval_record: Dict[str, Any],
    approval_decision_request: ApprovalDecisionRequest,
    *,
    approval_request_domain: Dict[str, Any] | None = None,
) -> ApprovalGraphState:
    thread_id = str(
        (approval_request_domain or {}).get("thread_id")
        or approval_record.get("thread_id")
        or approval_record.get("ticket_id")
        or ""
    )
    return {
        "approval_record": approval_record,
        "session_id": thread_id,
        "thread_id": thread_id,
        "approval_request_domain": approval_request_domain,
        "approval_decision_request": approval_decision_request,
        "pending_node": "ingest_approval_decision",
        "resume_kind": "approval_decision",
        "transition_notes": [],
    }


def extract_graph_response(state: TicketGraphState | ApprovalGraphState) -> GraphResponse:
    response = state.get("response")
    if response is None:
        raise ValueError("graph completed without response payload")
    return response
