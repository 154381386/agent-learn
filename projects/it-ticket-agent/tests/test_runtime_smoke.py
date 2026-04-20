from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from it_ticket_agent.approval import ApprovalStateError
from it_ticket_agent.approval_store import ApprovalStore
from it_ticket_agent.checkpoint_store import CheckpointStore
from it_ticket_agent.execution_store import ExecutionStore
from it_ticket_agent.interrupt_store import InterruptStore
from it_ticket_agent.memory_store import IncidentCaseStore, ProcessMemoryStore
from it_ticket_agent.runtime.orchestrator import SupervisorOrchestrator
from it_ticket_agent.schemas import ConversationCreateRequest, ConversationMessageRequest, ConversationResumeRequest
from it_ticket_agent.settings import Settings
from it_ticket_agent.session.models import ConversationSession
from it_ticket_agent.system_event_store import SystemEventStore
from it_ticket_agent.state.incident_state import IncidentState
from it_ticket_agent.state.models import Hypothesis, RAGContextBundle, VerificationStep


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

    def _create_pending_approval_fixture(self, *, session_id: str, ticket_id: str, service: str) -> tuple[dict, dict]:
        incident_state = IncidentState(
            ticket_id=ticket_id,
            user_id="fixture-user",
            message=f"{service} 发布后故障，待审批回滚",
            thread_id=session_id,
            service=service,
            environment="prod",
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
                    "key_entities": {"service": service, "environment": "prod", "cluster": "prod-shanghai-1", "namespace": "default"},
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

    async def test_s1_faq_request_is_answered_via_direct_answer_path(self) -> None:
        self.orchestrator.knowledge_service.retrieve_for_request = AsyncMock(
            return_value=RAGContextBundle(
                query="发布流程是什么",
                query_type="search",
                should_respond_directly=True,
                direct_answer="标准发布流程包括构建、审批、发布和回滚验证。",
                citations=["发布手册 / 发布流程"],
                index_info={"ready": True},
            )
        )
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u1",
                message="发布流程是什么？",
                service="checkout-service",
                environment="prod",
            )
        )
        self.assertEqual(result["status"], "completed")
        session_id = result["session"]["session_id"]
        session = self.session_store.get(session_id)
        self.assertIsNotNone(session)
        self.assertIsNone(session["pending_interrupt_id"])
        self.assertEqual(session["current_agent"], "direct_answer")
        self.assertEqual(result["message"], "标准发布流程包括构建、审批、发布和回滚验证。")
        self.assertEqual(result["diagnosis"]["routing"]["intent"], "direct_answer")
        turns = self.session_store.list_turns(session_id)
        self.assertEqual(len([turn for turn in turns if turn["role"] == "user"]), 1)
        summary = self.process_memory_store.summarize(session_id)
        self.assertEqual(summary["latest_execution"]["event_type"], "run_summary")

    async def test_missing_environment_triggers_clarification_before_diagnosis(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-clarify-env",
                message="订单服务超时怎么办",
                service="order-service",
                cluster="",
                namespace="",
                environment=None,
            )
        )

        self.assertEqual(result["status"], "awaiting_clarification")
        pending = result["pending_interrupt"]
        self.assertIsNotNone(pending)
        self.assertEqual(pending["type"], "clarification")
        self.assertIn("environment", str(pending.get("expected_input_schema") or ""))

    async def test_missing_host_identifier_triggers_clarification(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-clarify-host",
                message="机器启动不了怎么办",
                cluster="",
                namespace="",
            )
        )

        self.assertEqual(result["status"], "awaiting_clarification")
        pending = result["pending_interrupt"]
        self.assertIsNotNone(pending)
        self.assertEqual(pending["type"], "clarification")
        self.assertIn("host_identifier", str(pending.get("expected_input_schema") or ""))

    async def test_hypothesis_request_routes_to_new_entry_path(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-checkpoint",
                message="帮我看 deploy 失败了",
                service="checkout-service",
                environment="prod",
            )
        )
        self.assertIn(result["status"], {"completed", "awaiting_approval"})
        session_id = result["session"]["session_id"]
        session = self.session_store.get(session_id)
        self.assertIsNotNone(session)
        self.assertEqual(session["current_agent"], "hypothesis_graph")
        self.assertEqual(result["diagnosis"]["routing"]["intent"], "hypothesis_graph")
        self.assertTrue(result["message"])
        snapshot = result["diagnosis"]["context_snapshot"]
        self.assertIsNotNone(snapshot)
        self.assertIn("cicd", snapshot["matched_tool_domains"])
        hypotheses = result["diagnosis"]["hypotheses"]
        self.assertTrue(hypotheses)
        self.assertIn("verification_plan", hypotheses[0])
        verification_results = result["diagnosis"]["verification_results"]
        self.assertTrue(verification_results)
        self.assertEqual(len(verification_results), len(hypotheses))
        ranked_result = result["diagnosis"]["ranked_result"]
        self.assertIsNotNone(ranked_result)
        self.assertTrue(ranked_result["primary"])
        approval_proposals = result["diagnosis"]["incident_state"]["approval_proposals"]
        self.assertLessEqual(len(approval_proposals), 1)
        if result["status"] == "awaiting_approval":
            self.assertIsNotNone(result["approval_request"])
            self.assertIsNotNone(result["pending_interrupt"])
        elif result["status"] == "completed":
            self.assertIsNotNone(result["pending_interrupt"])
            self.assertEqual(result["pending_interrupt"]["type"], "feedback")

    async def test_high_risk_primary_action_enters_approval_interrupt(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-high-risk",
                message="checkout-service 发布失败，需要排查最近变更",
                service="checkout-service",
                environment="prod",
            )
        )

        self.assertEqual(result["status"], "awaiting_approval")
        self.assertIsNotNone(result["approval_request"])
        self.assertIsNotNone(result["pending_interrupt"])
        self.assertEqual(result["pending_interrupt"]["type"], "approval")
        diagnosis = result["diagnosis"]
        self.assertTrue(diagnosis["ranked_result"]["primary"])
        self.assertEqual(
            diagnosis["incident_state"]["metadata"]["selected_root_cause"],
            diagnosis["ranked_result"]["primary"]["root_cause"],
        )

    async def test_low_risk_primary_action_auto_executes_in_main_graph(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-auto-exec",
                message="checkout-service 需要一个低风险自动修复动作",
                service="checkout-service",
                environment="prod",
            )
        )

        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["approval_request"])
        self.assertIn("审批已通过", result["message"])
        self.assertIsNotNone(result["pending_interrupt"])
        self.assertEqual(result["pending_interrupt"]["type"], "feedback")
        execution_results = result["diagnosis"]["incident_state"]["execution_results"]
        self.assertTrue(execution_results)
        self.assertEqual(execution_results[0]["status"], "completed")
        self.assertEqual(execution_results[0]["action"], "observe_service")

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
        events = self.approval_store.list_events(approval["approval_id"])
        self.assertEqual([event["event_type"] for event in events], ["created", "approved", "resumed"])
        plans = self.execution_store.list_plans(session_id)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan["status"], "completed")

    async def test_feedback_resume_updates_incident_case(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-feedback",
                message="checkout-service 需要一个低风险自动修复动作",
                service="checkout-service",
                environment="prod",
            )
        )
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["pending_interrupt"])
        self.assertEqual(result["pending_interrupt"]["type"], "feedback")
        session_id = result["session"]["session_id"]
        feedback_interrupt = result["pending_interrupt"]
        ranked_result = result["diagnosis"]["ranked_result"]
        actual_hypothesis_id = ranked_result["primary"]["hypothesis_id"]

        resumed = await self.orchestrator.resume_conversation(
            session_id,
            ConversationResumeRequest(
                interrupt_id=feedback_interrupt["interrupt_id"],
                answer_payload={
                    "human_verified": True,
                    "actual_root_cause_hypothesis": actual_hypothesis_id,
                    "hypothesis_accuracy": {actual_hypothesis_id: 1.0},
                    "comment": "判断正确",
                },
            ),
        )
        self.assertEqual(resumed["status"], "completed")
        self.assertIsNone(resumed["pending_interrupt"])
        case = self.incident_case_store.get_by_session_id(session_id)
        assert case is not None
        self.assertTrue(case["human_verified"])
        self.assertEqual(case["actual_root_cause_hypothesis"], actual_hypothesis_id)
        self.assertEqual(case["hypothesis_accuracy"][actual_hypothesis_id], 1.0)

    async def test_post_message_records_topic_shift_history(self) -> None:
        self.orchestrator.knowledge_service.retrieve_for_request = AsyncMock(
            return_value=RAGContextBundle(
                query="发布流程是什么",
                query_type="search",
                should_respond_directly=True,
                direct_answer="标准发布流程包括构建、审批、发布和回滚验证。",
                citations=["发布手册 / 发布流程"],
                index_info={"ready": True},
            )
        )
        created = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-topic",
                message="发布流程是什么？",
                service="checkout-service",
                environment="prod",
            )
        )
        session_id = created["session"]["session_id"]
        updated = await self.orchestrator.post_message(
            session_id,
            ConversationMessageRequest(
                message="现在看起来更像数据库连接池问题，还有慢查询",
            ),
        )
        self.assertIn(updated["status"], {"completed", "awaiting_approval"})
        session = self.session_store.get(session_id)
        assert session is not None
        history = list((session.get("session_memory") or {}).get("current_intent_history") or [])
        self.assertTrue(history)
        self.assertTrue(history[-1]["topic_shift_detected"])
        snapshot = updated["diagnosis"]["context_snapshot"]
        self.assertIn("db", snapshot["matched_tool_domains"])

    async def test_topic_shift_supersedes_pending_approval_and_restarts_analysis(self) -> None:
        session_id = "topic-shift-approval"
        approval, interrupt = self._create_pending_approval_fixture(
            session_id=session_id,
            ticket_id="TOPIC-SHIFT-1",
            service="checkout-service",
        )

        updated = await self.orchestrator.post_message(
            session_id,
            ConversationMessageRequest(
                message="现在更像数据库连接池耗尽和慢查询，不做回滚了",
            ),
        )

        self.assertIn(updated["status"], {"completed", "awaiting_approval"})
        approval_record = self.approval_store.get(approval["approval_id"])
        self.assertIsNotNone(approval_record)
        self.assertEqual(approval_record["status"], "cancelled")
        interrupt_record = self.interrupt_store.get(interrupt["interrupt_id"])
        self.assertIsNotNone(interrupt_record)
        self.assertEqual(interrupt_record["status"], "cancelled")
        session = self.session_store.get(session_id)
        assert session is not None
        history = list((session.get("session_memory") or {}).get("current_intent_history") or [])
        self.assertTrue(history)

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
        events = self.approval_store.list_events(approval["approval_id"])
        self.assertEqual([event["event_type"] for event in events], ["created", "rejected", "resumed"])

    async def test_d4_unregistered_action_is_blocked_before_tool_execution(self) -> None:
        session_id = "d4-unregistered-session"
        approval, interrupt = self._create_pending_approval_fixture(
            session_id=session_id,
            ticket_id="D4-UNREGISTERED",
            service="blocked-service",
        )
        approval["action"] = "dangerous.shell"
        with sqlite3.connect(self.session_db_path) as conn:
            row = conn.execute(
                "select proposals_json from approval_request_v2 where approval_id = ?",
                (approval["approval_id"],),
            ).fetchone()
            proposals = __import__("json").loads(row[0])
            proposals[0]["action"] = "dangerous.shell"
            proposals[0]["metadata"]["registered_action"] = False
            proposals[0]["metadata"]["registration_error"] = "action is not registered for execution: dangerous.shell"
            conn.execute(
                "update approval_request_v2 set proposals_json = ? where approval_id = ?",
                (__import__("json").dumps(proposals, ensure_ascii=False), approval["approval_id"]),
            )
            conn.commit()

        with patch("it_ticket_agent.graph.nodes.MCPClient.call_tool", new_callable=AsyncMock) as mocked_call:
            resumed = await self.orchestrator.resume_conversation(
                session_id,
                ConversationResumeRequest(
                    interrupt_id=interrupt["interrupt_id"],
                    approval_id=approval["approval_id"],
                    approved=True,
                    approver_id="ops-admin",
                    comment="尝试执行未注册动作",
                ),
            )

        self.assertEqual(resumed["status"], "failed")
        self.assertEqual(mocked_call.await_count, 0)
        steps = self.execution_store.list_steps(self.execution_store.list_plans(session_id)[0]["plan_id"])
        self.assertEqual(steps[0]["status"], "failed")
        self.assertIn("not registered", steps[0]["result_summary"])

    async def test_d4_snapshot_mismatch_is_blocked_before_tool_execution(self) -> None:
        session_id = "d4-snapshot-session"
        approval, interrupt = self._create_pending_approval_fixture(
            session_id=session_id,
            ticket_id="D4-SNAPSHOT",
            service="snapshot-service",
        )
        with sqlite3.connect(self.session_db_path) as conn:
            row = conn.execute(
                "select proposals_json from approval_request_v2 where approval_id = ?",
                (approval["approval_id"],),
            ).fetchone()
            proposals = __import__("json").loads(row[0])
            proposals[0]["params"]["service"] = "tampered-service"
            conn.execute(
                "update approval_request_v2 set proposals_json = ? where approval_id = ?",
                (__import__("json").dumps(proposals, ensure_ascii=False), approval["approval_id"]),
            )
            conn.commit()

        with patch("it_ticket_agent.graph.nodes.MCPClient.call_tool", new_callable=AsyncMock) as mocked_call:
            resumed = await self.orchestrator.resume_conversation(
                session_id,
                ConversationResumeRequest(
                    interrupt_id=interrupt["interrupt_id"],
                    approval_id=approval["approval_id"],
                    approved=True,
                    approver_id="ops-admin",
                    comment="尝试执行篡改后的快照",
                ),
            )

        self.assertEqual(resumed["status"], "failed")
        self.assertEqual(mocked_call.await_count, 0)
        recovery = self.orchestrator.get_execution_recovery(session_id)
        self.assertEqual(recovery["recovery_action"], "manual_intervention")
        self.assertEqual(recovery["failed_step_id"], recovery["resume_from_step_id"])
        self.assertIn("snapshot mismatch", resumed["message"])

    async def test_d2_execution_failure_records_checkpoint_and_recovery_hint(self) -> None:
        session_id = "d2-failure-session"
        approval, interrupt = self._create_pending_approval_fixture(
            session_id=session_id,
            ticket_id="D2-TICKET",
            service="failure-service",
        )

        with patch(
            "it_ticket_agent.graph.nodes.MCPClient.call_tool",
            side_effect=RuntimeError("rollback tool failed"),
        ):
            resumed = await self.orchestrator.resume_conversation(
                session_id,
                ConversationResumeRequest(
                    interrupt_id=interrupt["interrupt_id"],
                    approval_id=approval["approval_id"],
                    approved=True,
                    approver_id="ops-admin",
                    comment="执行失败场景",
                ),
            )

        self.assertEqual(resumed["status"], "failed")
        session = self.session_store.get(session_id)
        self.assertEqual(session["status"], "failed")
        self.assertIsNone(session["pending_interrupt_id"])
        self.assertTrue(session["last_checkpoint_id"])
        checkpoint = self.checkpoint_store.get(session["last_checkpoint_id"])
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["next_action"], "retry_execution_step")
        self.assertEqual(checkpoint["metadata"]["response_status"], "failed")
        plans = self.execution_store.list_plans(session_id)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["status"], "failed")
        steps = self.execution_store.list_steps(plans[0]["plan_id"])
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0]["status"], "completed")
        self.assertEqual(steps[1]["status"], "failed")
        self.assertEqual(steps[2]["status"], "pending")
        recovery = self.orchestrator.get_execution_recovery(session_id)
        self.assertIsNotNone(recovery)
        self.assertEqual(recovery["recovery_action"], "retry_execution_step")
        self.assertEqual(recovery["failed_step_id"], steps[1]["step_id"])
        self.assertEqual(recovery["resume_from_step_id"], steps[1]["step_id"])
        self.assertIsNotNone(recovery["latest_checkpoint"])
        self.assertIsNotNone(recovery["execution_plan"])
        self.assertEqual(recovery["execution_plan"]["status"], "failed")
        system_events = self.system_event_store.list_for_session(session_id)
        event_types = [event["event_type"] for event in system_events]
        self.assertIn("execution.started", event_types)
        self.assertIn("execution.step_finished", event_types)
        self.assertIn("conversation.closed", event_types)

    async def test_c1_illegal_approval_transition_is_rejected(self) -> None:
        approval, _ = self._create_pending_approval_fixture(
            session_id="c1-session",
            ticket_id="C1-TICKET",
            service="risk-service",
        )

        self.approval_store.decide(
            approval["approval_id"],
            False,
            "ops-admin",
            "先人工处理",
        )

        with self.assertRaises(ApprovalStateError):
            self.approval_store.decide(
                approval["approval_id"],
                True,
                "ops-admin",
                "重复审批",
            )

    async def test_c3_and_c4_approval_expire_records_events_and_finalizes_session(self) -> None:
        session_id = "expire-session"
        approval, _ = self._create_pending_approval_fixture(
            session_id=session_id,
            ticket_id="EXPIRE-TICKET",
            service="expire-service",
        )

        result = await self.orchestrator.expire_approval(
            approval,
            actor_id="system-timeout",
            comment="审批超时",
        )

        self.assertEqual(result["status"], "completed")
        session = self.session_store.get(session_id)
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["current_stage"], "finalize")
        self.assertIsNone(session["pending_interrupt_id"])
        events = self.approval_store.list_events(approval["approval_id"])
        self.assertEqual([event["event_type"] for event in events], ["created", "expired", "resumed"])
        self.assertEqual(events[1]["detail"]["status"], "expired")

    async def test_c1_cancelled_status_records_events_and_finalizes_session(self) -> None:
        session_id = "cancel-session"
        approval, _ = self._create_pending_approval_fixture(
            session_id=session_id,
            ticket_id="CANCEL-TICKET",
            service="cancel-service",
        )

        result = await self.orchestrator.cancel_approval(
            approval,
            actor_id="ops-admin",
            comment="工单已转人工",
        )

        self.assertEqual(result["status"], "completed")
        session = self.session_store.get(session_id)
        self.assertEqual(session["status"], "completed")
        self.assertIsNone(session["pending_interrupt_id"])
        events = self.approval_store.list_events(approval["approval_id"])
        self.assertEqual([event["event_type"] for event in events], ["created", "cancelled", "resumed"])

    async def test_s4_restart_recovery_uses_persisted_state(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u4",
                message="帮我看 deploy 失败了",
                service="checkout-service",
                environment="prod",
            )
        )
        self.assertIn(result["status"], {"completed", "awaiting_approval"})
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
        self.assertEqual(detail["session"]["status"], result["status"])
        if result["status"] == "awaiting_approval":
            self.assertIsNotNone(detail["pending_interrupt"])
        else:
            self.assertIsNone(detail["pending_interrupt"])
        restored_state = restarted._restore_incident_state_for_session(detail["session"])
        self.assertIsInstance(restored_state, dict)
        self.assertEqual(restored_state.get("ticket_id"), detail["session"]["ticket_id"])


if __name__ == "__main__":
    unittest.main()
