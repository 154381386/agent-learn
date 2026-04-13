from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .react_nodes import ReactGraphNodes
from .react_state import ReactTicketGraphState


class ReactGraphBuilder:
    def __init__(self, nodes: ReactGraphNodes) -> None:
        self.nodes = nodes

    def build_ticket_graph(self):
        graph = StateGraph(ReactTicketGraphState)
        graph.add_node("light_router", self.nodes.light_router)
        graph.add_node("direct_answer", self.nodes.direct_answer)
        graph.add_node("supervisor_loop", self.nodes.supervisor_loop)
        graph.add_node("approval_gate", self.nodes.approval_gate)
        graph.add_node("await_user", self.nodes.await_user)
        graph.add_node("execute_approved_action", self.nodes.execute_approved_action)
        graph.add_node("finalize", self.nodes.finalize)

        graph.add_edge(START, "light_router")
        graph.add_conditional_edges(
            "light_router",
            self.nodes.route_after_light_router,
            {
                "direct_answer": "direct_answer",
                "supervisor_loop": "supervisor_loop",
            },
        )
        graph.add_edge("direct_answer", "finalize")
        graph.add_conditional_edges(
            "supervisor_loop",
            self.nodes.route_after_supervisor_loop,
            {
                "approval_gate": "approval_gate",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "approval_gate",
            self.nodes.route_after_approval_gate,
            {
                "await_user": "await_user",
                "execute_approved_action": "execute_approved_action",
                "finalize": "finalize",
            },
        )
        graph.add_edge("await_user", "finalize")
        graph.add_edge("execute_approved_action", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile(name="it_ticket_react_tool_first_graph")
