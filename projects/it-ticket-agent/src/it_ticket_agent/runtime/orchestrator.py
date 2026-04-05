from __future__ import annotations

import logging
from typing import Any, Dict
from uuid import uuid4

from ..agents import CICDAgent, GeneralSREAgent
from ..agents.base import BaseDomainAgent
from ..approval_store import ApprovalStore
from ..checkpoint_store import CheckpointStore
from ..graph import (
    OrchestratorGraphBuilder,
    OrchestratorGraphNodes,
    build_approval_graph_input,
    build_ticket_graph_input,
    extract_graph_response,
)
from ..interrupt_store import InterruptStore
from ..mcp import MCPConnectionManager
from ..schemas import (
    ApprovalDecisionRequest,
    ConversationCreateRequest,
    ConversationMessageRequest,
    ConversationResumeRequest,
    TicketRequest,
)
from ..session.models import ConversationSession, ConversationTurn
from ..session_store import SessionStore
from ..settings import Settings
from .supervisor import RuleBasedSupervisor


logger = logging.getLogger(__name__)


class SupervisorOrchestrator:
    def __init__(
        self,
        settings: Settings,
        approval_store: ApprovalStore,
        session_store: SessionStore,
        interrupt_store: InterruptStore,
        checkpoint_store: CheckpointStore | None = None,
    ) -> None:
        self.settings = settings
        self.approval_store = approval_store
        self.session_store = session_store
        self.interrupt_store = interrupt_store
        self.checkpoint_store = checkpoint_store or CheckpointStore(settings.approval_db_path)
        self.supervisor = RuleBasedSupervisor(settings)
        self.connection_manager = MCPConnectionManager(settings.mcp_connections_path)
        self.agents: Dict[str, BaseDomainAgent] = {
            "cicd_agent": CICDAgent(
                settings,
                self.supervisor.knowledge_client(settings),
                self.connection_manager,
            ),
            "general_sre_agent": GeneralSREAgent(),
        }
        self.graph_nodes = OrchestratorGraphNodes(
            supervisor=self.supervisor,
            approval_store=self.approval_store,
            session_store=self.session_store,
            interrupt_store=self.interrupt_store,
            connection_manager=self.connection_manager,
            agents=self.agents,
        )
        self.graph_builder = OrchestratorGraphBuilder(self.graph_nodes)
        self.ticket_graph = self.graph_builder.build_ticket_graph()
        self.approval_graph = self.graph_builder.build_approval_graph()

    def _append_user_turn(self, session_id: str, *, content: str, structured_payload: dict[str, Any]) -> dict[str, Any]:
        return self.session_store.append_turn(
            ConversationTurn(
                session_id=session_id,
                role="user",
                content=content,
                structured_payload=structured_payload,
            )
        )

    def _append_assistant_turn(self, session_id: str, *, response: dict[str, Any]) -> dict[str, Any]:
        return self.session_store.append_turn(
            ConversationTurn(
                session_id=session_id,
                role="assistant",
                content=str(response.get("message") or ""),
                structured_payload={
                    "status": response.get("status"),
                    "diagnosis": response.get("diagnosis"),
                    "approval_request": response.get("approval_request"),
                },
            )
        )

    def _create_checkpoint(
        self,
        *,
        session: dict[str, Any],
        stage: str,
        next_action: str | None,
        incident_state: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.checkpoint_store.create(
            {
                "session_id": str(session["session_id"]),
                "thread_id": str(session.get("thread_id") or session["session_id"]),
                "ticket_id": str(session.get("ticket_id") or session["session_id"]),
                "stage": stage,
                "next_action": next_action,
                "state_snapshot": incident_state,
                "metadata": dict(metadata or {}),
            }
        )

    def _get_pending_interrupt(self, session: dict[str, Any]) -> dict[str, Any] | None:
        pending_interrupt_id = session.get("pending_interrupt_id")
        if not pending_interrupt_id:
            return None
        return self.interrupt_store.get(str(pending_interrupt_id))

    def _build_conversation_detail(self, session_id: str) -> dict[str, Any] | None:
        session = self.session_store.get(session_id)
        if session is None:
            return None
        pending_interrupt = self._get_pending_interrupt(session)
        return {
            "session": session,
            "turns": self.session_store.list_turns(session_id),
            "pending_interrupt": pending_interrupt,
        }

    async def _run_ticket_message(
        self,
        request: TicketRequest,
        *,
        session_id: str,
        thread_id: str,
        create_session: bool,
    ) -> dict[str, Any]:
        graph_input = build_ticket_graph_input(request, session_id=session_id, thread_id=thread_id)
        incident_state = graph_input["incident_state"]
        if create_session:
            self.session_store.create(
                ConversationSession(
                    session_id=session_id,
                    thread_id=thread_id,
                    ticket_id=request.ticket_id,
                    user_id=request.user_id,
                    status="active",
                    current_stage="ingest",
                    incident_state=incident_state,
                )
            )
        self._append_user_turn(
            session_id,
            content=request.message,
            structured_payload={
                "ticket_id": request.ticket_id,
                "user_id": request.user_id,
                "service": request.service,
                "cluster": request.cluster,
                "namespace": request.namespace,
                "channel": request.channel,
            },
        )
        try:
            state = await self.ticket_graph.ainvoke(graph_input)
        except Exception:
            self.session_store.update_status(session_id, status="failed", current_stage="finalize")
            raise
        response = extract_graph_response(state)
        final_incident_state = state.get("incident_state") or incident_state
        latest_approval_id = None
        approval_request = state.get("approval_request")
        if isinstance(approval_request, dict):
            latest_approval_id = approval_request.get("approval_id")
        final_status = "awaiting_approval" if response.get("status") == "awaiting_approval" else "completed"
        final_stage = "awaiting_approval" if final_status == "awaiting_approval" else "finalize"
        pending_interrupt_id = None
        if isinstance(approval_request, dict):
            pending_interrupt_id = approval_request.get("interrupt_id")
        session = self.session_store.update_state(
            session_id,
            incident_state=final_incident_state.model_dump(),
            status=final_status,
            current_stage=final_stage,
            latest_approval_id=latest_approval_id,
            pending_interrupt_id=pending_interrupt_id,
        )
        checkpoint = None
        if session is not None:
            checkpoint = self._create_checkpoint(
                session=session,
                stage="awaiting_approval" if final_status == "awaiting_approval" else "finalize",
                next_action="wait_for_approval" if final_status == "awaiting_approval" else "complete",
                incident_state=final_incident_state.model_dump(),
                metadata={
                    "source": "ticket_message",
                    "response_status": response.get("status"),
                    "approval_id": latest_approval_id,
                    "interrupt_id": pending_interrupt_id,
                },
            )
            session = self.session_store.update_state(
                session_id,
                incident_state=final_incident_state.model_dump(),
                status=final_status,
                current_stage=final_stage,
                latest_approval_id=latest_approval_id,
                pending_interrupt_id=pending_interrupt_id,
                last_checkpoint_id=checkpoint["checkpoint_id"],
            )
        assistant_turn = self._append_assistant_turn(session_id, response=response)
        return {
            "session": session,
            "status": response.get("status"),
            "message": response.get("message"),
            "diagnosis": response.get("diagnosis"),
            "approval_request": response.get("approval_request"),
            "pending_interrupt": self.interrupt_store.get(str(pending_interrupt_id)) if pending_interrupt_id else None,
            "assistant_turn": assistant_turn,
        }

    async def start_conversation(self, request: ConversationCreateRequest) -> dict[str, Any]:
        ticket_id = request.ticket_id or f"CONV-{uuid4().hex[:12]}"
        ticket_request = TicketRequest(
            ticket_id=ticket_id,
            user_id=request.user_id,
            message=request.message,
            service=request.service,
            cluster=request.cluster,
            namespace=request.namespace,
            channel=request.channel,
        )
        return await self._run_ticket_message(
            ticket_request,
            session_id=ticket_id,
            thread_id=ticket_id,
            create_session=True,
        )

    async def post_message(self, session_id: str, request: ConversationMessageRequest) -> dict[str, Any]:
        session = self.session_store.get(session_id)
        if session is None:
            raise ValueError("session not found")
        if session.get("pending_interrupt_id"):
            raise RuntimeError("conversation is awaiting resume; use the resume endpoint")
        incident_state = dict(session.get("incident_state") or {})
        ticket_request = TicketRequest(
            ticket_id=str(session.get("ticket_id") or session_id),
            user_id=str(session.get("user_id") or ""),
            message=request.message,
            service=incident_state.get("service"),
            cluster=str(incident_state.get("cluster") or "prod-shanghai-1"),
            namespace=str(incident_state.get("namespace") or "default"),
            channel=str(incident_state.get("channel") or "feishu"),
        )
        return await self._run_ticket_message(
            ticket_request,
            session_id=str(session["session_id"]),
            thread_id=str(session.get("thread_id") or session["session_id"]),
            create_session=False,
        )

    async def resume_conversation(self, session_id: str, request: ConversationResumeRequest) -> dict[str, Any]:
        session = self.session_store.get(session_id)
        if session is None:
            raise ValueError("session not found")
        pending_interrupt = self._get_pending_interrupt(session)
        if pending_interrupt is None:
            raise RuntimeError("conversation has no pending interrupt to resume")
        if pending_interrupt.get("type") != "approval":
            raise RuntimeError("only approval resume is supported in A5")
        approval_id = request.approval_id or pending_interrupt.get("metadata", {}).get("approval_id")
        if not approval_id:
            raise ValueError("approval id not found for pending approval interrupt")
        approval = self.approval_store.get(str(approval_id))
        if approval is None:
            raise ValueError("approval not found")
        self.approval_store.decide(str(approval_id), request.approved, request.approver_id, request.comment)
        response = await self.handle_approval_decision(
            approval,
            ApprovalDecisionRequest(
                approved=request.approved,
                approver_id=request.approver_id,
                comment=request.comment,
            ),
        )
        updated_session = self.session_store.get(session_id)
        turns = self.session_store.list_turns(session_id, limit=1)
        assistant_turn = turns[-1] if turns else None
        return {
            "session": updated_session,
            "status": response.get("status"),
            "message": response.get("message"),
            "diagnosis": response.get("diagnosis"),
            "approval_request": response.get("approval_request"),
            "pending_interrupt": self._get_pending_interrupt(updated_session) if updated_session else None,
            "assistant_turn": assistant_turn,
        }

    def get_conversation(self, session_id: str) -> dict[str, Any] | None:
        return self._build_conversation_detail(session_id)

    async def handle_ticket(self, request: TicketRequest) -> Dict[str, object]:
        return await self._run_ticket_message(
            request,
            session_id=request.ticket_id,
            thread_id=request.ticket_id,
            create_session=True,
        )

    async def handle_approval_decision(
        self,
        approval: Dict[str, object],
        request: ApprovalDecisionRequest,
    ) -> Dict[str, object]:
        state = await self.approval_graph.ainvoke(build_approval_graph_input(approval, request))
        response = extract_graph_response(state)
        thread_id = str(approval.get("thread_id") or approval.get("ticket_id") or "")
        session = self.session_store.get_by_thread_id(thread_id) if thread_id else None
        updated_session = session
        if session is not None:
            session_id = str(session["session_id"])
            approval_id = str(approval.get("approval_id") or "") or None
            decision_label = "批准" if request.approved else "拒绝"
            decision_content = f"{decision_label}审批动作"
            if request.comment:
                decision_content = f"{decision_content}：{request.comment}"
            self._append_user_turn(
                session_id,
                content=decision_content,
                structured_payload={
                    "approved": request.approved,
                    "approver_id": request.approver_id,
                    "comment": request.comment,
                    "approval_id": approval_id,
                },
            )
            pending_interrupt_id = session.get("pending_interrupt_id")
            if pending_interrupt_id:
                self.interrupt_store.answer(
                    pending_interrupt_id,
                    answer_payload={
                        "approved": request.approved,
                        "approver_id": request.approver_id,
                        "comment": request.comment,
                        "approval_id": approval.get("approval_id"),
                    },
                )
            final_incident_state = state.get("incident_state")
            if final_incident_state is not None:
                updated_session = self.session_store.update_state(
                    session_id,
                    incident_state=final_incident_state.model_dump(),
                    status="completed",
                    current_stage="finalize",
                    latest_approval_id=str(approval.get("approval_id") or session.get("latest_approval_id") or "") or None,
                    pending_interrupt_id="",
                )
            else:
                updated_session = self.session_store.update_status(
                    session_id,
                    status="completed",
                    current_stage="finalize",
                    latest_approval_id=str(approval.get("approval_id") or session.get("latest_approval_id") or "") or None,
                    pending_interrupt_id="",
                )
            if updated_session is not None:
                checkpoint = self._create_checkpoint(
                    session=updated_session,
                    stage="approval_resume_finalize",
                    next_action="complete",
                    incident_state=(final_incident_state.model_dump() if final_incident_state is not None else dict(updated_session.get("incident_state") or {})),
                    metadata={
                        "source": "approval_resume",
                        "response_status": response.get("status"),
                        "approval_id": approval_id,
                        "interrupt_id": pending_interrupt_id,
                    },
                )
                if final_incident_state is not None:
                    updated_session = self.session_store.update_state(
                        session_id,
                        incident_state=final_incident_state.model_dump(),
                        status="completed",
                        current_stage="finalize",
                        latest_approval_id=str(approval.get("approval_id") or session.get("latest_approval_id") or "") or None,
                        pending_interrupt_id="",
                        last_checkpoint_id=checkpoint["checkpoint_id"],
                    )
                else:
                    updated_session = self.session_store.update_status(
                        session_id,
                        status="completed",
                        current_stage="finalize",
                        latest_approval_id=str(approval.get("approval_id") or session.get("latest_approval_id") or "") or None,
                        pending_interrupt_id="",
                        last_checkpoint_id=checkpoint["checkpoint_id"],
                    )
            self._append_assistant_turn(
                session_id,
                response={
                    **response,
                    "approval_request": response.get("approval_request"),
                },
            )
        return {
            **response,
            "session": updated_session,
        }
