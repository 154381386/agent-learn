from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from it_ticket_agent.approval_store import ApprovalStore
from it_ticket_agent.checkpoint_store import CheckpointStore
from it_ticket_agent.interrupt_store import InterruptStore
from it_ticket_agent.memory_store import IncidentCaseStore, ProcessMemoryStore
from it_ticket_agent.runtime.orchestrator import SupervisorOrchestrator
from it_ticket_agent.schemas import ConversationCreateRequest, ConversationResumeRequest
from it_ticket_agent.settings import Settings
from it_ticket_agent.session.models import ConversationSession
from it_ticket_agent.state.incident_state import IncidentState


class ConversationRuntimeSmokeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        db_path = str(Path(self.temp_dir.name) / "smoke.db")
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
        self.session_db_path = db_path
        self.interrupt_store = InterruptStore(db_path)
        self.checkpoint_store = CheckpointStore(db_path)
        self.process_memory_store = ProcessMemoryStore(db_path)
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
            incident_case_store=self.incident_case_store,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_pending_approval_fixture(self, *, session_id: str, ticket_id: str, service: str) -> tuple[dict, dict]:
        incident_state = IncidentState(
            ticket_id=ticket_id,
            user_id="fixture-user",
            message=f"{service} 发布后故障，待审批回滚",
            thread_id=session_id,
            service=service,
            cluster="prod-shanghai-1",
            namespace="default",
            channel="feishu",
            status="awaiting_approval",
            metadata={},
        )
        session = self.session_store.create(
            ConversationSession(
                session_id=session_id,
                thread_id=session_id,
                ticket_id=ticket_id,
                user_id="fixture-user",
                status="awaiting_approval",
                current_stage="awaiting_approval",
                current_agent="cicd_agent",
                incident_state=incident_state,
                session_memory={
                    "original_user_message": incident_state.message,
                    "current_intent": {
                        "agent_name": "cicd_agent",
                        "mode": "router",
                        "route_source": "fixture",
                    },
                    "key_entities": {"service": service, "cluster": "prod-shanghai-1", "namespace": "default"},
                    "clarification_answers": {},
                    "pending_approval": None,
                    "current_stage": "awaiting_approval",
                    "pending_interrupt": None,
                },
            )
        )
        approval = self.approval_store.create(
            {
                "approval_id": f"approval-{ticket_id}",
                "ticket_id": ticket_id,
                "thread_id": session_id,
                "action": "cicd.rollback_release",
                "risk": "high",
                "reason": "发布后故障，需要审批回滚",
                "params": {
                    "service": service,
                    "cluster": "prod-shanghai-1",
                    "namespace": "default",
                    "mcp_server": "http://fixture-mcp",
                    "agent_name": "cicd_agent",
                    "source_agent": "cicd_agent",
                    "incident_state": incident_state.model_dump(),
                },
            }
        )
        interrupt = self.interrupt_store.create_approval_interrupt(
            session_id=session_id,
            ticket_id=ticket_id,
            reason="发布后故障，需要审批回滚",
            question="是否批准执行该高风险动作？",
            expected_input_schema={
                "type": "object",
                "properties": {
                    "approved": {"type": "boolean"},
                    "approver_id": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "required": ["approved", "approver_id"],
            },
            metadata={"approval_id": approval["approval_id"], "thread_id": session_id},
        )
        checkpoint = self.checkpoint_store.create(
            {
                "session_id": session_id,
                "thread_id": session_id,
                "ticket_id": ticket_id,
                "stage": "awaiting_approval",
                "next_action": "wait_for_approval",
                "state_snapshot": incident_state.model_dump(),
                "metadata": {"approval_id": approval["approval_id"], "interrupt_id": interrupt["interrupt_id"]},
            }
        )
        self.session_store.update_state(
            session_id,
            incident_state=incident_state.model_dump(),
            status="awaiting_approval",
            current_stage="awaiting_approval",
            latest_approval_id=approval["approval_id"],
            pending_interrupt_id=interrupt["interrupt_id"],
            last_checkpoint_id=checkpoint["checkpoint_id"],
            session_memory={
                "pending_approval": {
                    "approval_id": approval["approval_id"],
                    "action": approval["action"],
                    "risk": approval["risk"],
                    "reason": approval["reason"],
                },
                "current_stage": "awaiting_approval",
                "pending_interrupt": {
                    "interrupt_id": interrupt["interrupt_id"],
                    "type": "approval",
                    "reason": interrupt["reason"],
                    "question": interrupt["question"],
                },
            },
        )
        self.process_memory_store.append(
            {
                "session_id": session_id,
                "thread_id": session_id,
                "ticket_id": ticket_id,
                "event_type": "approval_requested",
                "stage": "awaiting_approval",
                "source": "test-fixture",
                "summary": "fixture approval pending",
                "payload": {"action": approval["action"], "risk": approval["risk"]},
                "refs": {"approval_id": approval["approval_id"], "interrupt_id": interrupt["interrupt_id"]},
            }
        )
        return approval, interrupt

    async def test_s1_session_resume_after_clarification(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u1",
                message="帮我看一下现在发布失败了",
                service=None,
            )
        )
        self.assertEqual(result["status"], "awaiting_clarification")
        session_id = result["session"]["session_id"]
        interrupt = result["pending_interrupt"]
        self.assertIsNotNone(interrupt)
        self.assertEqual(interrupt["type"], "clarification")

        resumed = await self.orchestrator.resume_conversation(
            session_id,
            ConversationResumeRequest(
                interrupt_id=interrupt["interrupt_id"],
                answer_payload={"text": "checkout-service"},
            ),
        )
        self.assertIn(resumed["status"], {"completed", "awaiting_approval"})
        session = self.session_store.get(session_id)
        self.assertIsNotNone(session)
        self.assertIsNone(session["pending_interrupt_id"])
        self.assertEqual(session["session_memory"]["key_entities"]["service"], "checkout-service")
        turns = self.session_store.list_turns(session_id)
        self.assertEqual(len([turn for turn in turns if turn["role"] == "user"]), 2)
        summary = self.process_memory_store.summarize(session_id)
        self.assertEqual(summary["latest_clarification"]["event_type"], "clarification_answered")

    async def test_clarification_resume_reuses_checkpoint_snapshot(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-checkpoint",
                message="帮我看 deploy 失败了",
                service=None,
            )
        )
        self.assertEqual(result["status"], "awaiting_clarification")
        session_id = result["session"]["session_id"]
        interrupt = result["pending_interrupt"]
        session = self.session_store.get(session_id)
        self.assertIsNotNone(session)

        checkpoint = self.checkpoint_store.create(
            {
                "session_id": session_id,
                "thread_id": session_id,
                "ticket_id": session["ticket_id"],
                "stage": "awaiting_clarification",
                "next_action": "wait_for_clarification",
                "state_snapshot": {
                    **session["incident_state"],
                    "metadata": {
                        **dict(session["incident_state"].get("metadata") or {}),
                        "resume_probe": "checkpoint-first",
                    },
                },
                "metadata": {"source": "test-case"},
            }
        )
        self.session_store.update_state(
            session_id,
            incident_state=session["incident_state"],
            status=session["status"],
            current_stage=session["current_stage"],
            latest_approval_id=session.get("latest_approval_id"),
            pending_interrupt_id=session.get("pending_interrupt_id"),
            last_checkpoint_id=checkpoint["checkpoint_id"],
        )

        captured_state: dict[str, object] = {}

        async def fake_ainvoke(graph_input):
            captured_state["incident_state"] = graph_input["incident_state"].model_dump()
            return {
                **graph_input,
                "incident_state": graph_input["incident_state"],
                "response": {
                    "ticket_id": graph_input["request"].ticket_id,
                    "status": "completed",
                    "message": "恢复完成",
                    "diagnosis": {"summary": "ok"},
                },
                "approval_request": None,
                "pending_node": None,
            }

        with patch.object(self.orchestrator.ticket_graph, "ainvoke", AsyncMock(side_effect=fake_ainvoke)):
            resumed = await self.orchestrator.resume_conversation(
                session_id,
                ConversationResumeRequest(
                    interrupt_id=interrupt["interrupt_id"],
                    answer_payload={"text": "checkout-service"},
                ),
            )

        self.assertEqual(resumed["status"], "completed")
        replay_state = captured_state.get("incident_state")
        self.assertIsNotNone(replay_state)
        self.assertEqual(replay_state["service"], "checkout-service")
        self.assertEqual(replay_state["metadata"]["resume_probe"], "checkpoint-first")

    async def test_s2_approval_resume_after_decision(self) -> None:
        session_id = "s2-session"
        approval, interrupt = self._create_pending_approval_fixture(
            session_id=session_id,
            ticket_id="S2-TICKET",
            service="checkout-service",
        )

        with patch(
            "it_ticket_agent.graph.nodes.MCPClient.call_tool",
            return_value={
                "structuredContent": {"status": "completed", "job_id": "rollback-123"},
                "content": [{"text": "回滚任务已提交并执行完成。"}],
            },
        ):
            resumed = await self.orchestrator.resume_conversation(
                session_id,
                ConversationResumeRequest(
                    interrupt_id=interrupt["interrupt_id"],
                    approval_id=approval["approval_id"],
                    approved=True,
                    approver_id="ops-admin",
                    comment="同意回滚",
                ),
            )
        self.assertEqual(resumed["status"], "completed")
        session = self.session_store.get(session_id)
        self.assertEqual(session["status"], "completed")
        self.assertIsNone(session["pending_interrupt_id"])
        self.assertTrue(session["last_checkpoint_id"])
        self.assertEqual(session["current_agent"], "cicd_agent")
        self.assertIsNotNone(session["closed_at"])
        cases = self.incident_case_store.list_cases(service="checkout-service")
        self.assertTrue(cases)
        self.assertEqual(cases[0]["session_id"], session_id)
        self.assertEqual(cases[0]["final_action"], "cicd.rollback_release")
        self.assertTrue(cases[0]["approval_required"])
        summary = self.process_memory_store.summarize(session_id)
        self.assertEqual(summary["latest_approval"]["event_type"], "approval_decided")

    async def test_resume_rejects_selector_mismatch(self) -> None:
        approval, interrupt = self._create_pending_approval_fixture(
            session_id="selector-session",
            ticket_id="SELECTOR-TICKET",
            service="selector-service",
        )
        with self.assertRaises(RuntimeError):
            await self.orchestrator.resume_conversation(
                "selector-session",
                ConversationResumeRequest(
                    interrupt_id="wrong-interrupt-id",
                    approved=True,
                    approver_id="ops-admin",
                ),
            )
        with self.assertRaises(RuntimeError):
            await self.orchestrator.resume_conversation(
                "selector-session",
                ConversationResumeRequest(
                    interrupt_id=interrupt["interrupt_id"],
                    approval_id="wrong-approval-id",
                    approved=True,
                    approver_id="ops-admin",
                ),
            )
        session = self.session_store.get("selector-session")
        self.assertEqual(session["pending_interrupt_id"], interrupt["interrupt_id"])

    async def test_s3_approval_rejection_reaches_terminal_state(self) -> None:
        session_id = "s3-session"
        approval, interrupt = self._create_pending_approval_fixture(
            session_id=session_id,
            ticket_id="S3-TICKET",
            service="order-service",
        )

        resumed = await self.orchestrator.resume_conversation(
            session_id,
            ConversationResumeRequest(
                interrupt_id=interrupt["interrupt_id"],
                approval_id=approval["approval_id"],
                approved=False,
                approver_id="ops-admin",
                comment="风险过高，先人工处理",
            ),
        )
        self.assertEqual(resumed["status"], "completed")
        session = self.session_store.get(session_id)
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["current_stage"], "finalize")
        self.assertEqual(session["current_agent"], "cicd_agent")
        self.assertIsNotNone(session["closed_at"])
        summary = self.process_memory_store.summarize(session_id)
        self.assertEqual(summary["latest_approval"]["event_type"], "approval_decided")

    async def test_s4_restart_recovery_uses_persisted_state(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u4",
                message="帮我看 deploy 失败了",
                service=None,
            )
        )
        self.assertEqual(result["status"], "awaiting_clarification")
        session_id = result["session"]["session_id"]

        from it_ticket_agent.session_store import SessionStore

        restarted = SupervisorOrchestrator(
            self.settings,
            ApprovalStore(self.session_db_path),
            SessionStore(self.session_db_path),
            InterruptStore(self.session_db_path),
            CheckpointStore(self.session_db_path),
            ProcessMemoryStore(self.session_db_path),
        )
        detail = restarted.get_conversation(session_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["session"]["status"], "awaiting_clarification")
        self.assertIsNotNone(detail["pending_interrupt"])
        restored_state = restarted._restore_incident_state_for_session(detail["session"])
        self.assertIsInstance(restored_state, dict)
        self.assertEqual(restored_state.get("ticket_id"), detail["session"]["ticket_id"])


if __name__ == "__main__":
    unittest.main()
