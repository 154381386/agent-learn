from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from it_ticket_agent.approval_store import ApprovalStore
from it_ticket_agent.checkpoint_store import CheckpointStore
from it_ticket_agent.execution_store import ExecutionStore
from it_ticket_agent.interrupt_store import InterruptStore
from it_ticket_agent.knowledge import KnowledgeService
from it_ticket_agent.memory_store import IncidentCaseStore, ProcessMemoryStore
from it_ticket_agent.runtime.contracts import TaskEnvelope
from it_ticket_agent.runtime.orchestrator import SupervisorOrchestrator
from it_ticket_agent.schemas import ConversationCreateRequest, TicketRequest
from it_ticket_agent.settings import Settings
from it_ticket_agent.state.models import RAGContextBundle
from it_ticket_agent.system_event_store import SystemEventStore
from it_ticket_agent.tools.cicd import SearchKnowledgeBaseTool


class KnowledgeServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_retrieve_for_request_keeps_document_and_section_fields(self) -> None:
        client = type("StubClient", (), {})()
        client.search = AsyncMock(
            return_value={
                "query": "支付服务发布失败",
                "hits": [
                    {
                        "chunk_id": "chunk-1",
                        "title": "支付服务发布手册",
                        "section": "回滚步骤 / 第 2 段",
                        "path": "runbooks/payment-deploy.md",
                        "category": "runbook",
                        "score": 0.92,
                        "snippet": "如果 readiness probe 连续失败，优先回滚上一稳定版本。",
                    }
                ],
                "context": [],
                "citations": [],
                "facts": [],
                "index_info": {"ready": True, "vector_backend": "pgvector"},
            }
        )
        service = KnowledgeService(client)

        bundle = await service.retrieve_for_request(
            TicketRequest(ticket_id="T-1", user_id="u-1", message="支付服务发布失败", service="支付服务", environment="prod")
        )

        self.assertEqual(bundle.hits[0].title, "支付服务发布手册")
        self.assertEqual(bundle.hits[0].section, "回滚步骤 / 第 2 段")
        self.assertEqual(bundle.hits[0].path, "runbooks/payment-deploy.md")
        self.assertIn("支付服务发布手册", bundle.citations[0])
        self.assertIn("回滚步骤 / 第 2 段", bundle.citations[0])


class SearchKnowledgeBaseToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_prefers_shared_rag_context_before_remote_search(self) -> None:
        client = type("StubClient", (), {})()
        client.search = AsyncMock(side_effect=AssertionError("remote search should not be called"))
        tool = SearchKnowledgeBaseTool(client)
        task = TaskEnvelope(
            task_id="task-rag",
            ticket_id="T-rag",
            goal="诊断",
            shared_context={
                "message": "支付服务发布失败",
                "service": "支付服务",
                "rag_context": {
                    "query": "支付服务发布失败",
                    "context": [
                        {
                            "chunk_id": "chunk-2",
                            "title": "支付服务运行手册",
                            "section": "回滚检查 / 第 3 段",
                            "path": "runbooks/payment-runtime.md",
                            "category": "runbook",
                            "score": 0.88,
                            "snippet": "观察 error budget 与报警收敛情况后再恢复流量。",
                        }
                    ],
                    "hits": [],
                    "citations": ["支付服务运行手册 / 回滚检查 / 第 3 段"],
                },
            },
        )

        result = await tool.run(task)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["hits"][0]["title"], "支付服务运行手册")
        self.assertEqual(result.payload["hits"][0]["section"], "回滚检查 / 第 3 段")
        self.assertIn("支付服务运行手册", result.evidence[0])


class SharedKnowledgeOrchestratorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        db_path = str(Path(self.temp_dir.name) / "shared-knowledge.db")
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

    async def test_start_conversation_persists_shared_rag_context_and_uses_direct_answer_path(self) -> None:
        shared_bundle = RAGContextBundle(
            query="支付服务发布流程是什么",
            query_type="search",
            should_respond_directly=True,
            direct_answer="支付服务发布前需要先完成构建、审批和发布窗口确认。",
            hits=[
                {
                    "chunk_id": "chunk-3",
                    "title": "支付服务发布手册",
                    "section": "发布流程 / 第 1 段",
                    "path": "runbooks/payment-deploy.md",
                    "category": "runbook",
                    "score": 0.95,
                    "snippet": "发布前先确认构建成功、审批通过和发布窗口。",
                }
            ],
            context=[
                {
                    "chunk_id": "chunk-3",
                    "title": "支付服务发布手册",
                    "section": "发布流程 / 第 1 段",
                    "path": "runbooks/payment-deploy.md",
                    "category": "runbook",
                    "score": 0.95,
                    "snippet": "发布前先确认构建成功、审批通过和发布窗口。",
                }
            ],
            citations=["支付服务发布手册 / 发布流程 / 第 1 段 / runbooks/payment-deploy.md"],
            index_info={"ready": True, "vector_backend": "pgvector"},
        )
        self.orchestrator.knowledge_service.retrieve_for_request = AsyncMock(return_value=shared_bundle)

        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-shared-rag",
                message="支付服务发布流程是什么？",
                service="支付服务",
                environment="prod",
            )
        )

        diagnosis_rag = result["diagnosis"]["incident_state"]["rag_context"]
        self.assertEqual(diagnosis_rag["hits"][0]["path"], "runbooks/payment-deploy.md")
        self.assertEqual(result["session"]["incident_state"]["rag_context"]["query"], "支付服务发布流程是什么")
        self.assertEqual(result["message"], "支付服务发布前需要先完成构建、审批和发布窗口确认。")
        self.assertEqual(result["diagnosis"]["routing"]["intent"], "direct_answer")
        self.assertIsNone(result["diagnosis"].get("context_snapshot"))

        persisted = self.session_store.get(result["session"]["session_id"])
        self.assertEqual(persisted["incident_state"]["rag_context"]["hits"][0]["title"], "支付服务发布手册")


if __name__ == "__main__":
    unittest.main()
