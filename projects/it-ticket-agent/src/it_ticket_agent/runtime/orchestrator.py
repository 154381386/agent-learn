from __future__ import annotations

import logging
from typing import Dict

from ..agents import CICDAgent, GeneralSREAgent
from ..agents.base import BaseDomainAgent
from ..approval_store import ApprovalStore
from ..graph import (
    OrchestratorGraphBuilder,
    OrchestratorGraphNodes,
    build_approval_graph_input,
    build_ticket_graph_input,
    extract_graph_response,
)
from ..mcp import MCPConnectionManager
from ..schemas import ApprovalDecisionRequest, TicketRequest
from ..settings import Settings
from .supervisor import RuleBasedSupervisor


logger = logging.getLogger(__name__)


class SupervisorOrchestrator:
    def __init__(self, settings: Settings, approval_store: ApprovalStore) -> None:
        self.settings = settings
        self.approval_store = approval_store
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
            connection_manager=self.connection_manager,
            agents=self.agents,
        )
        self.graph_builder = OrchestratorGraphBuilder(self.graph_nodes)
        self.ticket_graph = self.graph_builder.build_ticket_graph()
        self.approval_graph = self.graph_builder.build_approval_graph()

    async def handle_ticket(self, request: TicketRequest) -> Dict[str, object]:
        state = await self.ticket_graph.ainvoke(build_ticket_graph_input(request))
        return extract_graph_response(state)

    async def handle_approval_decision(
        self,
        approval: Dict[str, object],
        request: ApprovalDecisionRequest,
    ) -> Dict[str, object]:
        state = await self.approval_graph.ainvoke(build_approval_graph_input(approval, request))
        return extract_graph_response(state)
