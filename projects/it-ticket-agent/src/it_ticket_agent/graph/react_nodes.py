from __future__ import annotations

from typing import Any, Dict

from ..runtime.contracts import SmartRouterDecision
from ..execution.tool_middleware import ToolExecutionMiddleware
from ..runtime.react_supervisor import ReactSupervisor
from ..runtime.smart_router import SmartRouter
from ..state.incident_state import IncidentState
from ..state.transformers import build_initial_incident_state
from .nodes import OrchestratorGraphNodes
from .react_state import ReactTicketGraphState


class ReactGraphNodes:
    def __init__(
        self,
        *,
        smart_router: SmartRouter,
        legacy_nodes: OrchestratorGraphNodes,
        supervisor: ReactSupervisor,
        tool_middleware: ToolExecutionMiddleware,
        action_executor,
    ) -> None:
        self.smart_router = smart_router
        self.legacy_nodes = legacy_nodes
        self.supervisor = supervisor
        self.tool_middleware = tool_middleware
        self.action_executor = action_executor

    async def light_router(self, state: ReactTicketGraphState) -> Dict[str, Any]:
        request = state["request"]
        incident_state = state.get("incident_state") or build_initial_incident_state(request)
        resume_target = str(state.get("resume_target") or "")
        if resume_target == "supervisor_loop":
            decision = SmartRouterDecision(
                intent="hypothesis_graph",
                route_source="resume",
                reason="clarification resume continues from react supervisor loop",
                confidence=1.0,
                matched_signals=["clarification_resume"],
                should_respond_directly=False,
            )
            incident_state.routing = decision.model_dump()
            incident_state.status = "resumed"
            return {
                "incident_state": incident_state,
                "route_decision": decision,
                "pending_node": "supervisor_loop",
                "resume_target": None,
            }
        decision = self.smart_router.route(request, rag_context=incident_state.rag_context)
        incident_state.routing = decision.model_dump()
        incident_state.status = "routed"
        return {
            "incident_state": incident_state,
            "route_decision": decision,
            "pending_node": decision.intent,
        }

    @staticmethod
    def route_after_light_router(state: ReactTicketGraphState) -> str:
        decision = state["route_decision"]
        return "direct_answer" if decision.intent == "direct_answer" else "supervisor_loop"

    async def direct_answer(self, state: ReactTicketGraphState) -> Dict[str, Any]:
        return await self.legacy_nodes.rag_direct_answer(state)  # type: ignore[arg-type]

    async def supervisor_loop(self, state: ReactTicketGraphState) -> Dict[str, Any]:
        return await self.supervisor.run_loop(state)

    @staticmethod
    def route_after_supervisor_loop(state: ReactTicketGraphState) -> str:
        if state.get("response") is not None:
            return "finalize"
        return "approval_gate"

    async def approval_gate(self, state: ReactTicketGraphState) -> Dict[str, Any]:
        return await self.legacy_nodes.approval_gate(state)  # type: ignore[arg-type]

    @staticmethod
    def route_after_approval_gate(state: ReactTicketGraphState) -> str:
        response = state.get("response") or {}
        status = str(response.get("status") or "")
        pending_node = str(state.get("pending_node") or "")
        if status.startswith("awaiting_"):
            return "await_user"
        if pending_node == "execute":
            return "execute_approved_action"
        return "finalize"

    async def await_user(self, state: ReactTicketGraphState) -> Dict[str, Any]:
        response = dict(state.get("response") or {})
        status = str(response.get("status") or "")
        resume_target = "supervisor_loop"
        if status == "awaiting_approval":
            resume_target = "execute_approved_action"
        elif status == "awaiting_clarification":
            resume_target = "supervisor_loop"
        diagnosis = dict(response.get("diagnosis") or {})
        graph_meta = dict(diagnosis.get("graph") or {})
        graph_meta["resume_target"] = resume_target
        graph_meta["await_user"] = True
        diagnosis["graph"] = graph_meta
        response["diagnosis"] = diagnosis
        return {
            "response": response,
            "incident_state": state.get("incident_state"),
            "approval_request": state.get("approval_request"),
            "pending_node": "finalize",
        }

    async def execute_approved_action(self, state: ReactTicketGraphState) -> Dict[str, Any]:
        return await self.legacy_nodes.execute(state)  # type: ignore[arg-type]

    async def finalize(self, state: ReactTicketGraphState) -> Dict[str, Any]:
        if state.get("response") is not None:
            incident_state = state.get("incident_state")
            request = state.get("request")
            if incident_state is not None and request is not None and getattr(incident_state, "ranked_result", None) is not None:
                feedback_interrupt = self.legacy_nodes._create_feedback_interrupt(
                    incident_state=incident_state,
                    session_id=str(state.get("session_id") or state.get("thread_id") or incident_state.ticket_id),
                    thread_id=str(state.get("thread_id") or incident_state.thread_id or incident_state.ticket_id),
                    ticket_id=str(request.ticket_id),
                )
                if feedback_interrupt is not None:
                    incident_state.metadata["feedback_interrupt"] = feedback_interrupt
            return {
                "response": state.get("response"),
                "incident_state": incident_state,
                "approval_request": state.get("approval_request"),
                "pending_node": None,
            }
        return await self.legacy_nodes.hypothesis_graph(state)  # type: ignore[arg-type]
