from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from it_ticket_agent.approval_store import ApprovalStore
from it_ticket_agent.checkpoint_store import CheckpointStore
from it_ticket_agent.execution_store import ExecutionStore
from it_ticket_agent.interrupt_store import InterruptStore
from it_ticket_agent.memory_store import IncidentCaseStore, ProcessMemoryStore
from it_ticket_agent.runtime.orchestrator import SupervisorOrchestrator
from it_ticket_agent.schemas import ConversationCreateRequest
from it_ticket_agent.settings import Settings
from it_ticket_agent.state.models import RAGContextBundle
from it_ticket_agent.system_event_store import SystemEventStore


class SmartRouterGraphSmokeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        db_path = str(Path(self.temp_dir.name) / "parallel-smoke.db")
        mcp_config = str(Path("/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/mcp_connections.yaml"))
        self.settings = Settings(
            approval_db_path=db_path,
            mcp_connections_path=mcp_config,
            llm_base_url="",
            llm_api_key="",
            llm_model="",
            rag_enabled=False,
        )
        self.approval_store = ApprovalStore(db_path)
        self.interrupt_store = InterruptStore(db_path)
        self.checkpoint_store = CheckpointStore(db_path)
        self.process_memory_store = ProcessMemoryStore(db_path)
        self.execution_store = ExecutionStore(db_path)
        self.system_event_store = SystemEventStore(db_path)
        self.incident_case_store = IncidentCaseStore(db_path)
        from it_ticket_agent.session_store import SessionStore

        self.session_store = SessionStore(db_path)
        self.orchestrator = SupervisorOrchestrator(
            self.settings,
            self.approval_store,
            self.session_store,
            self.interrupt_store,
            self.checkpoint_store,
            self.process_memory_store,
            execution_store=self.execution_store,
            incident_case_store=self.incident_case_store,
            system_event_store=self.system_event_store,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_high_confidence_faq_goes_to_direct_answer(self) -> None:
        self.orchestrator.knowledge_service.retrieve_for_request = AsyncMock(
            return_value=RAGContextBundle(
                query="K8s HPA 是什么",
                query_type="search",
                should_respond_directly=True,
                direct_answer="HPA 会根据指标自动调整副本数。",
                hits=[],
                context=[],
                citations=["Kubernetes 文档 / HPA"],
                index_info={"ready": True},
            )
        )
        response = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u1",
                message="K8s HPA 是什么？",
                service="order-service",
                environment="prod",
            )
        )

        diagnosis = dict(response.get("diagnosis") or {})
        self.assertEqual(diagnosis.get("routing", {}).get("intent"), "direct_answer")
        self.assertEqual(response.get("message"), "HPA 会根据指标自动调整副本数。")
        graph_notes = ((diagnosis.get("graph") or {}).get("transition_notes") or [])
        self.assertTrue(any("direct_answer" in note for note in graph_notes))
        self.assertEqual(response.get("status"), "completed")

    async def test_symptom_request_goes_to_hypothesis_graph_entry(self) -> None:
        self.orchestrator.knowledge_service.retrieve_for_request = AsyncMock(
            return_value=RAGContextBundle(
                query="order-service 发布后 502 超时",
                query_type="search",
                should_respond_directly=False,
                direct_answer=None,
                hits=[],
                context=[],
                citations=["发布手册 / 故障排查"],
                index_info={"ready": True},
            )
        )
        response = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u2",
                message="order-service 发布后 ingress 502 超时，帮我排查",
                service="order-service",
                environment="prod",
            )
        )

        diagnosis = dict(response.get("diagnosis") or {})
        routing = dict(diagnosis.get("routing") or {})
        self.assertEqual(routing.get("intent"), "hypothesis_graph")
        self.assertEqual(response.get("status"), "completed")
        self.assertIn("根因", response.get("message") or "")
        snapshot = dict(diagnosis.get("context_snapshot") or {})
        self.assertIn("network", snapshot.get("matched_tool_domains") or [])
        retrieval_expansion = dict(snapshot.get("retrieval_expansion") or {})
        self.assertTrue(retrieval_expansion.get("subqueries"))
        self.assertGreaterEqual(len(retrieval_expansion.get("subqueries") or []), 1)
        hypotheses = list(diagnosis.get("hypotheses") or [])
        self.assertTrue(hypotheses)
        first_plan = hypotheses[0].get("verification_plan") or []
        self.assertTrue(first_plan)
        used_tool_names = [item.get("tool_name") for item in first_plan if isinstance(item, dict)]
        self.assertTrue(all(name for name in used_tool_names))
        verification_results = list(diagnosis.get("verification_results") or [])
        self.assertEqual(len(verification_results), len(hypotheses))
        self.assertIn(verification_results[0].get("status"), {"passed", "inconclusive", "failed"})
        ranked_result = dict(diagnosis.get("ranked_result") or {})
        self.assertIn("primary", ranked_result)
        self.assertTrue(ranked_result.get("primary"))
        incident_state = dict(diagnosis.get("incident_state") or {})
        approval_proposals = list(incident_state.get("approval_proposals") or [])
        self.assertLessEqual(len(approval_proposals), 1)
        session_payload = dict(response.get("session") or {})
        session = self.session_store.get_by_thread_id(str(session_payload.get("thread_id") or ""))
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.get("current_agent"), "hypothesis_graph")
