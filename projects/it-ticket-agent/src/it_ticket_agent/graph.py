import os
from typing import Any, Dict, List

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .agent_clients import AgentClient
from .approval_store import ApprovalStore
from .executor import ActionExecutor
from .knowledge import KnowledgeBase
from .llm import OpenAICompatLLM
from .registry import build_registry
from .schemas import RoutingDecision, TaskConstraints, TaskPackage, TicketState, model_to_dict
from .settings import Settings


class TicketGraphFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.knowledge = KnowledgeBase(settings)
        self.llm = OpenAICompatLLM(settings)
        self.registry = build_registry(settings)
        self.agent_client = AgentClient()
        self.approval_store = ApprovalStore(settings.approval_db_path)
        self.executor = ActionExecutor()

    def build(self, checkpointer=None):
        graph = StateGraph(TicketState)
        graph.add_node("normalize_ticket", self.normalize_ticket)
        graph.add_node("retrieve_knowledge", self.retrieve_knowledge)
        graph.add_node("route_agents", self.route_agents)
        graph.add_node("diagnose_parallel", self.diagnose_parallel)
        graph.add_node("fuse_diagnosis", self.fuse_diagnosis)
        graph.add_node("approval_gate", self.approval_gate)
        graph.add_node("execute_action", self.execute_action)
        graph.add_node("finalize_response", self.finalize_response)

        graph.add_edge(START, "normalize_ticket")
        graph.add_edge("normalize_ticket", "retrieve_knowledge")
        graph.add_conditional_edges(
            "retrieve_knowledge",
            self.after_retrieve,
            {"finish": "finalize_response", "continue": "route_agents"},
        )
        graph.add_edge("route_agents", "diagnose_parallel")
        graph.add_edge("diagnose_parallel", "fuse_diagnosis")
        graph.add_conditional_edges(
            "fuse_diagnosis",
            self.after_fuse,
            {"approval": "approval_gate", "finish": "finalize_response"},
        )
        graph.add_edge("approval_gate", "execute_action")
        graph.add_edge("execute_action", "finalize_response")
        graph.add_edge("finalize_response", END)

        checkpoint_folder = os.path.dirname(self.settings.langgraph_checkpoint_db)
        if checkpoint_folder:
            os.makedirs(checkpoint_folder, exist_ok=True)
        if checkpointer is None:
            raise ValueError("A checkpointer instance is required for graph compilation")
        return graph.compile(checkpointer=checkpointer)

    def normalize_ticket(self, state: TicketState) -> TicketState:
        message = state["raw_message"]
        service = state.get("service") or self.extract_service(message)
        known_facts = []
        if "发版" in message or "发布" in message:
            known_facts.append("最近存在发版记录")
        if "重启" in message:
            known_facts.append("用户报告服务存在重启现象")
        return {"service": service, "summary": message, "known_facts": known_facts}

    async def retrieve_knowledge(self, state: TicketState) -> TicketState:
        result = await self.knowledge.search(
            query=state["raw_message"],
            service=state.get("service", ""),
        )
        known_facts = list(state.get("known_facts", []))
        for fact in result.get("facts", []):
            if fact not in known_facts:
                known_facts.append(fact)
        return {
            "rag_hit": result.get("should_respond_directly", False),
            "rag_answer": result.get("direct_answer") or "",
            "rag_query_type": result.get("query_type", "knowledge_lookup"),
            "rag_context": result.get("context", []),
            "rag_sources": result.get("citations", []),
            "known_facts": known_facts,
        }

    def after_retrieve(self, state: TicketState) -> str:
        return "finish" if state.get("rag_hit") else "continue"

    async def route_agents(self, state: TicketState) -> TicketState:
        message = state["raw_message"].lower()
        candidates: List[str] = []
        for name, descriptor in self.registry.items():
            if any(keyword in message for keyword in descriptor.keywords):
                candidates.append(name)

        if not candidates:
            candidates = ["pod-analysis", "root-cause"]

        agent_catalog = [
            {
                "name": descriptor.name,
                "description": descriptor.description,
                "keywords": ",".join(descriptor.keywords),
            }
            for descriptor in self.registry.values()
        ]
        decision = await self.llm.route_ticket(
            message=state["raw_message"],
            known_facts=state.get("known_facts", []),
            agent_catalog=agent_catalog,
        )

        if decision is None:
            confidence = 0.9 if len(candidates) == 1 else 0.45
            decision = RoutingDecision(
                intent="service_diagnosis",
                confidence=confidence,
                complexity_score=0.35 if len(candidates) == 1 else 0.78,
                recommended_mode="serial" if len(candidates) == 1 else "parallel",
                candidate_agents=candidates,
            )

        task = TaskPackage(
            ticket_id=state["ticket_id"],
            service=state["service"],
            cluster=state["cluster"],
            namespace=state["namespace"],
            symptom=state["raw_message"],
            summary=state["summary"],
            known_facts=state.get("known_facts", []),
            knowledge_context=state.get("rag_context", []),
            questions=["是什么原因导致当前故障？", "是否有建议动作？"],
            constraints=TaskConstraints(
                timeout_sec=8,
                allowed_tools=["k8s.describe_pod", "k8s.logs", "monitor.query"],
            ),
        )
        return {"routing": model_to_dict(decision), "task_package": model_to_dict(task)}

    async def diagnose_parallel(self, state: TicketState) -> TicketState:
        task = TaskPackage(**state["task_package"])
        descriptors = [self.registry[name] for name in state["routing"]["candidate_agents"]]
        results = await self.agent_client.run_many(descriptors, task)
        return {"agent_results": results}

    def fuse_diagnosis(self, state: TicketState) -> TicketState:
        results = state.get("agent_results", [])
        if not results:
            return {
                "fused_diagnosis": {
                    "conclusion": "暂无有效诊断结果",
                    "confidence": 0.0,
                    "sources": [],
                    "suggested_actions": [],
                }
            }

        best = max(results, key=lambda item: item.get("confidence", 0.0))
        conclusion = best["conclusion"]
        if any("oom" in result["conclusion"].lower() for result in results) and any(
            "变更" in result["conclusion"] or "发布" in result["conclusion"] for result in results
        ):
            conclusion = "最近发布引入的问题导致 Pod OOM 重启"

        actions: List[Dict[str, Any]] = []
        for result in results:
            actions.extend(result.get("suggested_actions", []))

        return {
            "fused_diagnosis": {
                "conclusion": conclusion,
                "confidence": min(best.get("confidence", 0.0) + 0.08, 0.99),
                "sources": results,
                "suggested_actions": actions,
            }
        }

    def after_fuse(self, state: TicketState) -> str:
        actions = state.get("fused_diagnosis", {}).get("suggested_actions", [])
        needs_approval = any(action.get("risk") in {"high", "critical"} for action in actions)
        return "approval" if needs_approval else "finish"

    async def approval_gate(self, state: TicketState) -> TicketState:
        action = state["fused_diagnosis"]["suggested_actions"][0]
        payload = {
            "ticket_id": state["ticket_id"],
            "thread_id": state["thread_id"],
            "action": action["action"],
            "risk": action["risk"],
            "reason": action["reason"],
            "params": action.get("params", {}),
        }
        approval_request = self.approval_store.create(payload)
        decision = interrupt(approval_request)
        return {"approval_request": approval_request, "approval_decision": decision}

    def execute_action(self, state: TicketState) -> TicketState:
        decision = state.get("approval_decision", {})
        approval_request = state.get("approval_request", {})
        if not decision or not decision.get("approved"):
            return {
                "action_result": {
                    "status": "rejected",
                    "message": "审批未通过，未执行任何高风险动作",
                }
            }

        result = self.executor.execute(approval_request["action"], approval_request.get("params", {}))
        return {"action_result": result}

    async def finalize_response(self, state: TicketState) -> TicketState:
        if state.get("rag_hit"):
            llm_response = await self.llm.render_final_response(
                user_message=state.get("raw_message", ""),
                rag_hit=True,
                rag_answer=state["rag_answer"],
                diagnosis={},
                action_result=state.get("action_result"),
                knowledge_context=state.get("rag_context", []),
            )
            return {"final_response": llm_response or state["rag_answer"]}

        diagnosis = state.get("fused_diagnosis", {})
        llm_response = await self.llm.render_final_response(
            user_message=state.get("raw_message", ""),
            rag_hit=False,
            rag_answer=state.get("rag_answer", ""),
            diagnosis=diagnosis,
            action_result=state.get("action_result"),
            knowledge_context=state.get("rag_context", []),
        )
        if llm_response:
            return {"final_response": llm_response}

        response_parts = [f"诊断结论：{diagnosis.get('conclusion', '暂无结论')}"]
        if diagnosis.get("confidence") is not None:
            response_parts.append(f"置信度：{diagnosis.get('confidence'):.2f}")
        if state.get("action_result"):
            response_parts.append(f"执行结果：{state['action_result'].get('message')}")
        if state.get("rag_context"):
            references = "、".join(
                f"《{item['title']}》" for item in state["rag_context"][:2] if item.get("title")
            )
            if references:
                response_parts.append(f"参考知识：{references}")
        return {"final_response": "；".join(response_parts)}

    @staticmethod
    def extract_service(message: str) -> str:
        for token in message.replace("，", " ").replace(",", " ").split():
            if token.endswith("-service"):
                return token
        return "unknown-service"
