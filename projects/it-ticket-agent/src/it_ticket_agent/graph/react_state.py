from __future__ import annotations

from typing import Any, Dict, List, Literal

from typing_extensions import TypedDict

from ..runtime.contracts import SmartRouterDecision
from ..schemas import TicketRequest
from ..state.incident_state import IncidentState
from ..state.models import ContextSnapshot, Hypothesis, RankedResult, VerificationResult
from ..state.transformers import build_initial_incident_state


class ReactTicketGraphState(TypedDict, total=False):
    request: TicketRequest
    session_id: str
    thread_id: str
    incident_state: IncidentState
    route_decision: SmartRouterDecision
    context_snapshot: ContextSnapshot
    hypotheses: List[Hypothesis]
    verification_results: List[VerificationResult]
    ranked_result: RankedResult
    approval_request: Dict[str, Any] | None
    response: Dict[str, Any]
    pending_node: str | None
    resume_target: str | None
    resume_kind: Literal["ticket"]
    transition_notes: List[str]
    iterations: int
    tool_calls_used: int
    confidence: float
    stop_reason: str
    tool_cache: Dict[str, Any]
    observation_ledger: List[Dict[str, Any]]
    working_memory_summary: str
    pinned_findings: List[str]


ReactGraphResponse = Dict[str, Any]


def build_react_graph_input(
    request: TicketRequest,
    *,
    session_id: str | None = None,
    thread_id: str | None = None,
    incident_state: IncidentState | None = None,
    resume_target: str | None = None,
) -> ReactTicketGraphState:
    next_incident_state = incident_state or build_initial_incident_state(request)
    resolved_session_id = session_id or next_incident_state.thread_id or request.ticket_id
    resolved_thread_id = thread_id or next_incident_state.thread_id or request.ticket_id
    next_incident_state.thread_id = resolved_thread_id
    return {
        "request": request,
        "session_id": resolved_session_id,
        "thread_id": resolved_thread_id,
        "incident_state": next_incident_state,
        "pending_node": "light_router",
        "resume_target": resume_target,
        "resume_kind": "ticket",
        "transition_notes": [],
        "iterations": 0,
        "tool_calls_used": 0,
        "confidence": 0.0,
        "stop_reason": "",
        "tool_cache": {},
        "observation_ledger": [],
        "working_memory_summary": "",
        "pinned_findings": [],
    }


def extract_react_graph_response(state: ReactTicketGraphState) -> ReactGraphResponse:
    response = state.get("response")
    if response is None:
        raise ValueError("react graph completed without response payload")
    return response
