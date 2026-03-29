from __future__ import annotations

from typing import Any, Dict, List, Literal

from typing_extensions import TypedDict

from ..runtime.contracts import AgentResult, RoutingDecision, TaskEnvelope
from ..schemas import ApprovalDecisionRequest, TicketRequest
from ..state.incident_state import IncidentState
from ..state.transformers import build_initial_incident_state


class TicketGraphState(TypedDict, total=False):
    request: TicketRequest
    incident_state: IncidentState
    routing_decision: RoutingDecision
    task: TaskEnvelope
    agent_result: AgentResult
    approval_request: Dict[str, Any] | None
    response: Dict[str, Any]
    pending_node: str | None
    resume_kind: Literal["ticket"]
    transition_notes: List[str]


class ApprovalGraphState(TypedDict, total=False):
    approval_record: Dict[str, Any]
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


def build_ticket_graph_input(request: TicketRequest) -> TicketGraphState:
    return {
        "request": request,
        "incident_state": build_initial_incident_state(request),
        "pending_node": "ingest",
        "resume_kind": "ticket",
        "transition_notes": [],
    }


def build_approval_graph_input(
    approval_record: Dict[str, Any],
    approval_decision_request: ApprovalDecisionRequest,
) -> ApprovalGraphState:
    return {
        "approval_record": approval_record,
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
