from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import OrchestratorGraphNodes
from .state import ApprovalGraphState, TicketGraphState


class OrchestratorGraphBuilder:
    def __init__(self, nodes: OrchestratorGraphNodes) -> None:
        self.nodes = nodes

    def build_ticket_graph(self):
        graph = StateGraph(TicketGraphState)
        graph.add_node("ingest", self.nodes.ingest)
        graph.add_node("supervisor_route", self.nodes.supervisor_route)
        graph.add_node("dispatch_subagents", self.nodes.dispatch_subagents)
        graph.add_node("aggregate_subagent_results", self.nodes.aggregate_subagent_results)
        graph.add_node("domain_agent", self.nodes.domain_agent)
        graph.add_node("clarification_gate", self.nodes.clarification_gate)
        graph.add_node("approval_gate", self.nodes.approval_gate)
        graph.add_node("finalize", self.nodes.finalize)

        graph.add_edge(START, "ingest")
        graph.add_edge("ingest", "supervisor_route")
        graph.add_conditional_edges(
            "supervisor_route",
            self.nodes.route_after_supervisor_route,
            {
                "domain_agent": "domain_agent",
                "dispatch_subagents": "dispatch_subagents",
            },
        )
        graph.add_edge("dispatch_subagents", "aggregate_subagent_results")
        graph.add_edge("aggregate_subagent_results", "clarification_gate")
        graph.add_edge("domain_agent", "clarification_gate")
        graph.add_conditional_edges(
            "clarification_gate",
            self.nodes.route_after_clarification_gate,
            {
                "approval_gate": "approval_gate",
                "end": END,
            },
        )
        graph.add_edge("approval_gate", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile(name="it_ticket_router_graph")

    def build_approval_graph(self):
        graph = StateGraph(ApprovalGraphState)
        graph.add_node("ingest_approval_decision", self.nodes.ingest_approval_decision)
        graph.add_node("approval_decision", self.nodes.approval_decision)
        graph.add_node(
            "execute_approved_action_transition",
            self.nodes.execute_approved_action_transition,
        )
        graph.add_node("finalize_approval_decision", self.nodes.finalize_approval_decision)

        graph.add_edge(START, "ingest_approval_decision")
        graph.add_edge("ingest_approval_decision", "approval_decision")
        graph.add_conditional_edges(
            "approval_decision",
            self.nodes.route_after_approval_decision,
            {
                "execute_approved_action": "execute_approved_action_transition",
                "finalize": "finalize_approval_decision",
            },
        )
        graph.add_edge("execute_approved_action_transition", "finalize_approval_decision")
        graph.add_edge("finalize_approval_decision", END)
        return graph.compile(name="it_ticket_approval_resume_graph")
