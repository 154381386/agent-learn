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
        graph.add_node("smart_router", self.nodes.smart_router)
        graph.add_node("rag_direct_answer", self.nodes.rag_direct_answer)
        graph.add_node("context_collector", self.nodes.context_collector)
        graph.add_node("hypothesis_generator", self.nodes.hypothesis_generator)
        graph.add_node("parallel_verification", self.nodes.parallel_verification)
        graph.add_node("ranker", self.nodes.ranker)
        graph.add_node("approval_gate", self.nodes.approval_gate)
        graph.add_node("execute", self.nodes.execute)
        graph.add_node("hypothesis_graph", self.nodes.hypothesis_graph)

        graph.add_edge(START, "ingest")
        graph.add_edge("ingest", "smart_router")
        graph.add_conditional_edges(
            "smart_router",
            self.nodes.route_after_smart_router,
            {
                "direct_answer": "rag_direct_answer",
                "hypothesis_graph": "context_collector",
            },
        )
        graph.add_edge("rag_direct_answer", END)
        graph.add_edge("context_collector", "hypothesis_generator")
        graph.add_edge("hypothesis_generator", "parallel_verification")
        graph.add_edge("parallel_verification", "ranker")
        graph.add_edge("ranker", "approval_gate")
        graph.add_conditional_edges(
            "approval_gate",
            self.nodes.route_after_ticket_approval_gate,
            {
                "execute": "execute",
                "hypothesis_graph": "hypothesis_graph",
                "end": END,
            },
        )
        graph.add_edge("execute", END)
        graph.add_edge("hypothesis_graph", END)
        return graph.compile(name="it_ticket_hypothesis_router_graph")

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
