from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from it_ticket_agent.approval import ApprovalStateError
from it_ticket_agent.approval_store import ApprovalStore
from it_ticket_agent.bad_case_store import BadCaseCandidateStore
from it_ticket_agent.checkpoint_store import CheckpointStore
from it_ticket_agent.execution_store import ExecutionStore
from it_ticket_agent.interrupt_store import InterruptStore
from it_ticket_agent.memory_store import IncidentCaseStore, ProcessMemoryStore
from it_ticket_agent.runtime.orchestrator import SupervisorOrchestrator
from it_ticket_agent.schemas import (
    ConversationCreateRequest,
    ConversationMessageRequest,
    ConversationResumeRequest,
)
from it_ticket_agent.settings import Settings
from it_ticket_agent.session.models import ConversationSession
from it_ticket_agent.system_event_store import SystemEventStore
from it_ticket_agent.state.incident_state import IncidentState
from it_ticket_agent.state.models import Hypothesis, RAGContextBundle, RetrievalExpansion, RetrievalSubquery, SimilarIncidentCase, VerificationStep


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
        self.bad_case_candidate_store = BadCaseCandidateStore(db_path)
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
            bad_case_candidate_store=self.bad_case_candidate_store,
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



    async def test_diagnostic_message_with_environment_text_enters_tool_diagnosis(self) -> None:
        self.orchestrator.knowledge_service.retrieve_for_request = AsyncMock(
            return_value=RAGContextBundle(
                query="order-service Pod 频繁重启",
                query_type="search",
                should_respond_directly=False,
                citations=[],
                index_info={"ready": True},
            )
        )
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-ui-diagnosis",
                message="order-service 生产环境 Pod 频繁重启，最近 2 小时内刚发过版，集群 prod-shanghai-1，命名空间 default。帮我诊断根因并给出处理建议。",
                service="order-service",
                cluster="prod-shanghai-1",
                namespace="default",
                mock_scenario="oom",
            )
        )

        self.assertNotEqual(result["status"], "awaiting_clarification")
        self.assertIsNone(result["pending_interrupt"])
        self.assertNotIn("已识别为知识咨询", result["message"])
        self.assertIn("初步根因判断", result["message"])
        self.assertIn("关键证据", result["message"])
        self.assertIn("建议下一步", result["message"])
        self.assertIn("为什么没有弹出执行审批", result["message"])
        diagnosis = dict(result.get("diagnosis") or {})
        self.assertEqual(diagnosis.get("display_mode"), "user_report")
        self.assertTrue(diagnosis.get("evidence"))
        self.assertTrue(diagnosis.get("recommended_actions"))
        self.assertIn("只读诊断工具", str(diagnosis.get("approval_explanation") or ""))
        session = result["session"]
        self.assertEqual(session["incident_state"]["environment"], "prod")
        events = self.orchestrator.list_system_events(session["session_id"], limit=200)
        self.assertIn("tool.started", [event["event_type"] for event in events])

    def test_user_report_marks_completed_clean_checks_as_checked(self) -> None:
        request = ConversationCreateRequest(
            user_id="u-report",
            message="order-service 最近变更后偶发超时，帮我查最近变更和运行时状态",
            service="order-service",
            environment="prod",
            cluster="prod-shanghai-1",
            namespace="default",
        )
        observations = [
            {
                "tool_name": "get_change_records",
                "result": {
                    "payload": {
                        "changes": [
                            {"change_id": "CHG-LOCAL-01"},
                            {"change_id": "CHG-LOCAL-02"},
                        ]
                    },
                    "evidence": ["最近变更 CHG-LOCAL-01", "最近变更 CHG-LOCAL-02"],
                },
            },
            {
                "tool_name": "check_pod_status",
                "result": {
                    "payload": {
                        "ready_replicas": 2,
                        "desired_replicas": 2,
                        "pods": [{"name": "order-service-pod-1", "status": "Running", "restarts": 0}],
                    },
                    "evidence": ["ready 2/2"],
                },
            },
            {
                "tool_name": "inspect_pod_logs",
                "result": {
                    "payload": {"oom_detected": False, "error_pattern": "none"},
                    "evidence": ["order-service log: request completed", "order-service log: latency within baseline"],
                },
            },
            {
                "tool_name": "inspect_pod_events",
                "result": {
                    "payload": {"last_termination_reason": "none", "event_count": 1},
                    "evidence": ["last_termination_reason=none", "event_count=1"],
                },
            },
            {
                "tool_name": "inspect_upstream_dependency",
                "result": {
                    "payload": {"dependency_status": "healthy", "timeout_ratio": 0.0},
                    "evidence": ["dependency=healthy", "timeout_ratio=0.0"],
                },
            },
            {
                "tool_name": "inspect_vpc_connectivity",
                "result": {
                    "payload": {"connectivity_status": "healthy"},
                    "evidence": ["connectivity=healthy"],
                },
            },
        ]

        report = self.orchestrator.react_supervisor._build_user_diagnosis_report(
            request=request,
            observations=observations,
            confidence=0.4,
            stop_reason="test",
        )

        message = report["message"]
        actions = "\n".join(report["recommended_actions"])
        evidence = "\n".join(report["evidence"])
        self.assertIn("Pod 就绪副本 2/2，副本数正常", evidence)
        self.assertNotIn("Pod 就绪副本 2/2，存在副本不可用", evidence)
        self.assertNotIn("dependency=healthy", evidence)
        self.assertIn("已查询到变更记录 CHG-LOCAL-01, CHG-LOCAL-02", actions)
        self.assertIn("Pod 状态、日志和事件已检查且未见明显容器异常", actions)
        self.assertIn("上游依赖和 VPC 已检查为 healthy", actions)
        self.assertNotIn("先查看失败 Pod", actions)
        self.assertNotIn("CrashLoopBackOff", actions)
        self.assertIn("为什么没有弹出执行审批", message)

    async def test_tool_activity_events_record_frontend_progress_signal(self) -> None:
        self.orchestrator.knowledge_service.retrieve_for_request = AsyncMock(
            return_value=RAGContextBundle(
                query="checkout-service Pod 频繁重启",
                query_type="search",
                should_respond_directly=False,
                citations=[],
                index_info={"ready": True},
            )
        )
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-tool-progress",
                message="checkout-service 生产环境 Pod 频繁重启，集群 prod-shanghai-1，命名空间 default",
                service="checkout-service",
                environment="prod",
                cluster="prod-shanghai-1",
                namespace="default",
                mock_scenario="oom",
            )
        )

        session_id = result["session"]["session_id"]
        events = self.orchestrator.list_system_events(session_id, limit=200)
        event_types = [event["event_type"] for event in events]
        self.assertIn("tool.started", event_types)
        self.assertIn("tool.completed", event_types)
        tool_events = [event for event in events if event["event_type"] == "tool.started"]
        self.assertTrue(any(event["payload"].get("tool_name") for event in tool_events))
        self.assertFalse(any("arguments" in event["payload"] for event in tool_events))

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

    async def test_clarification_resume_completes_without_feedback_when_no_actionable_guidance(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-clarify-resume",
                message="订单服务超时怎么办",
                service="order-service",
                cluster="",
                namespace="",
                environment=None,
            )
        )

        self.assertEqual(result["status"], "awaiting_clarification")
        session_id = result["session"]["session_id"]
        clarification_interrupt = result["pending_interrupt"]
        self.assertIsNotNone(clarification_interrupt)

        resumed = await self.orchestrator.resume_conversation(
            session_id,
            ConversationResumeRequest(
                interrupt_id=clarification_interrupt["interrupt_id"],
                answer_payload={"environment": "prod"},
            ),
        )

        self.assertEqual(resumed["status"], "completed")
        self.assertIsNone(resumed["pending_interrupt"])
        session = self.session_store.get(session_id)
        self.assertIsNotNone(session)
        self.assertEqual(session["status"], "completed")
        self.assertIsNone(session["pending_interrupt_id"])
        working_memory = dict((session.get("session_memory") or {}).get("working_memory") or {})
        facts = {item["key"]: item["value"] for item in list(working_memory.get("confirmed_facts") or [])}
        self.assertEqual(facts["clarification.environment"], "prod")
        self.assertEqual(working_memory.get("open_questions"), [])

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

    async def test_high_risk_approval_resume_returns_feedback_interrupt(self) -> None:
        created = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-high-risk-feedback",
                message="checkout-service 发布失败，需要排查最近变更",
                service="checkout-service",
                environment="prod",
            )
        )

        self.assertEqual(created["status"], "awaiting_approval")
        session_id = created["session"]["session_id"]
        approval_request = created["approval_request"]
        approval_interrupt = created["pending_interrupt"]
        self.assertIsNotNone(approval_request)
        self.assertIsNotNone(approval_interrupt)

        resumed = await self.orchestrator.resume_conversation(
            session_id,
            ConversationResumeRequest(
                interrupt_id=approval_interrupt["interrupt_id"],
                approval_id=approval_request["approval_id"],
                approved=True,
                approver_id="ops-admin",
                comment="同意执行回滚",
            ),
        )

        self.assertEqual(resumed["status"], "completed")
        self.assertIsNotNone(resumed["pending_interrupt"])
        self.assertEqual(resumed["pending_interrupt"]["type"], "feedback")
        session = self.session_store.get(session_id)
        self.assertIsNotNone(session)
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["pending_interrupt_id"], resumed["pending_interrupt"]["interrupt_id"])

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
        initial_case = self.incident_case_store.get_by_session_id(session_id)
        assert initial_case is not None
        self.assertEqual(initial_case["case_status"], "pending_review")
        self.assertFalse(initial_case["human_verified"])
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
        self.assertEqual(case["case_status"], "verified")
        self.assertEqual(case["review_note"], "判断正确")
        self.assertEqual(case["actual_root_cause_hypothesis"], actual_hypothesis_id)
        self.assertEqual(case["hypothesis_accuracy"][actual_hypothesis_id], 1.0)

    async def test_feedback_resume_with_new_information_reopens_diagnosis(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-feedback-reopen",
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

        resumed = await self.orchestrator.resume_conversation(
            session_id,
            ConversationResumeRequest(
                interrupt_id=feedback_interrupt["interrupt_id"],
                answer_payload={
                    "human_verified": False,
                    "actual_root_cause_hypothesis": "真实根因更像数据库连接池耗尽",
                    "hypothesis_accuracy": {"hypothesis-db": 0.9},
                    "comment": "补充：只有 prod 受影响，而且慢查询明显升高",
                },
            ),
        )
        self.assertTrue(resumed["diagnosis"]["feedback_reopened"])
        self.assertFalse(resumed["diagnosis"]["feedback"]["human_verified"])
        self.assertEqual(
            resumed["diagnosis"]["feedback"]["actual_root_cause_hypothesis"],
            "真实根因更像数据库连接池耗尽",
        )
        self.assertIsNotNone(resumed["pending_interrupt"])
        self.assertNotEqual(resumed["pending_interrupt"]["interrupt_id"], feedback_interrupt["interrupt_id"])
        session = self.session_store.get(session_id)
        assert session is not None
        self.assertEqual(session["pending_interrupt_id"], resumed["pending_interrupt"]["interrupt_id"])
        session_memory = dict(session.get("session_memory") or {})
        event_queue = list(session_memory.get("session_event_queue") or [])
        self.assertTrue(event_queue)
        self.assertEqual(event_queue[-1]["source"], "feedback")
        self.assertEqual(event_queue[-1]["event_type"], "correction")
        self.assertIsNotNone(event_queue[-1]["consumed_at"])
        working_memory = dict(session_memory.get("working_memory") or {})
        corrections = list(working_memory.get("user_corrections") or [])
        self.assertTrue(any("数据库连接池耗尽" in str(item.get("message") or "") for item in corrections))
        case = self.incident_case_store.get_by_session_id(session_id)
        assert case is not None
        self.assertFalse(case["human_verified"])
        self.assertEqual(case["case_status"], "pending_review")
        self.assertEqual(case["actual_root_cause_hypothesis"], "真实根因更像数据库连接池耗尽")
        self.assertEqual(case["hypothesis_accuracy"], {"hypothesis-db": 0.9})
        candidates = self.bad_case_candidate_store.list_candidates(session_id=session_id, limit=10)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "feedback_reopen")
        self.assertIn("human_feedback_negative", candidates[0]["reason_codes"])
        self.assertIn("actual_root_cause_provided", candidates[0]["reason_codes"])

    async def test_runtime_completion_creates_bad_case_candidate_when_retrieval_expansion_has_no_gain(self) -> None:
        self.orchestrator.knowledge_service.retrieve_for_request = AsyncMock(
            return_value=RAGContextBundle(
                query="payment-service timeout 并且数据库告警",
                query_type="search",
                should_respond_directly=False,
            )
        )
        self.orchestrator.knowledge_service.retrieve_query = AsyncMock(
            return_value=RAGContextBundle(
                query="payment-service network timeout retry",
                query_type="search",
                should_respond_directly=False,
            )
        )
        self.orchestrator.case_retriever.recall = AsyncMock(return_value=[])
        self.orchestrator.retrieval_planner.plan = AsyncMock(
            return_value=RetrievalExpansion(
                subqueries=[
                    RetrievalSubquery(
                        query="payment-service network timeout retry",
                        target="both",
                        reason="补充 timeout 背景知识",
                        failure_mode="dependency_timeout",
                        root_cause_taxonomy="network_dependency",
                    )
                ]
            )
        )

        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-bad-case-runtime",
                message="payment-service timeout 并且数据库告警",
                service="payment-service",
                environment="prod",
            )
        )

        self.assertEqual(result["status"], "completed")
        session_id = result["session"]["session_id"]
        candidates = self.bad_case_candidate_store.list_candidates(session_id=session_id, limit=10)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "runtime_completion")
        self.assertIn("retrieval_expansion_no_gain", candidates[0]["reason_codes"])
        self.assertIn("case_memory_empty", candidates[0]["reason_codes"])
        self.assertEqual(candidates[0]["retrieval_expansion"]["added_rag_hits"], 0)
        self.assertEqual(candidates[0]["retrieval_expansion"]["added_case_hits"], 0)

    async def test_bad_case_reason_codes_include_case_memory_failure(self) -> None:
        reason_codes = self.orchestrator._detect_bad_case_reason_codes(
            source="runtime_completion",
            response_payload={
                "status": "completed",
                "message": "继续用实时工具诊断。",
                "diagnosis": {
                    "route": "react_tool_first",
                    "context_snapshot": {
                        "case_recall": {
                            "prefetch_status": "error",
                            "prefetched_case_count": 0,
                            "case_memory_reason": "case_memory_search_failed",
                            "tool_failures": [
                                {"query": "payment-service timeout", "error": "case_memory_search_failed"}
                            ],
                        }
                    },
                },
            },
            incident_state_snapshot={},
            incident_case=None,
        )

        self.assertIn("case_memory_failed", reason_codes)
        self.assertIn("case_memory_failed_case_memory_search_failed", reason_codes)
        self.assertEqual(self.orchestrator._resolve_bad_case_severity(reason_codes), "medium")

    async def test_generic_diagnosis_skips_auto_case_prefetch_until_more_precise_symptom(self) -> None:
        self.orchestrator.case_retriever.recall = AsyncMock(
            return_value=[
                SimilarIncidentCase(
                    case_id="case-order-timeout",
                    service="order-service",
                    failure_mode="dependency_timeout",
                    root_cause_taxonomy="network_path_instability",
                    symptom="order-service timeout",
                    root_cause="上游依赖抖动",
                    summary="历史上曾因上游依赖抖动导致超时",
                    recall_source="semantic_hybrid",
                    recall_score=0.71,
                )
            ]
        )

        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-generic-case-prefetch",
                message="order-service 出问题了，帮我排查一下",
                service="order-service",
                environment="prod",
                cluster="prod-shanghai-1",
            )
        )

        self.assertEqual(self.orchestrator.case_retriever.recall.await_count, 0)
        snapshot = dict(result["diagnosis"]["context_snapshot"] or {})
        self.assertFalse(snapshot["case_recall"]["auto_prefetch_enabled"])
        self.assertEqual(snapshot["case_recall"]["prefetch_reason"], "query_too_generic")
        self.assertEqual(snapshot["similar_cases"], [])

    async def test_specific_diagnosis_prefetches_similar_cases_as_background_hint(self) -> None:
        self.orchestrator.case_retriever.recall = AsyncMock(
            return_value=[
                SimilarIncidentCase(
                    case_id="case-order-timeout",
                    service="order-service",
                    failure_mode="dependency_timeout",
                    root_cause_taxonomy="network_path_instability",
                    symptom="order-service timeout and 502",
                    root_cause="上游依赖超时",
                    final_action="observe_service",
                    summary="历史上曾因上游依赖超时导致 502",
                    recall_source="semantic_hybrid",
                    recall_score=0.83,
                )
            ]
        )

        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-specific-case-prefetch",
                message="order-service 连续 timeout 并出现 502，请排查",
                service="order-service",
                environment="prod",
                cluster="prod-shanghai-1",
            )
        )

        self.assertEqual(self.orchestrator.case_retriever.recall.await_count, 1)
        snapshot = dict(result["diagnosis"]["context_snapshot"] or {})
        self.assertTrue(snapshot["case_recall"]["auto_prefetch_enabled"])
        self.assertIn("failure_mode:dependency_timeout", snapshot["case_recall"]["prefetch_reason"])
        self.assertEqual(len(snapshot["similar_cases"]), 1)

    async def test_specific_diagnosis_degrades_when_auto_case_prefetch_fails(self) -> None:
        self.orchestrator.case_retriever.recall = AsyncMock(side_effect=TimeoutError("case memory timeout"))
        self.orchestrator.retrieval_planner.plan = AsyncMock(return_value=RetrievalExpansion())

        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-case-prefetch-failure",
                message="order-service 连续 timeout 并出现 502，请排查",
                service="order-service",
                environment="prod",
                cluster="prod-shanghai-1",
            )
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.orchestrator.case_retriever.recall.await_count, 1)
        snapshot = dict(result["diagnosis"]["context_snapshot"] or {})
        self.assertTrue(snapshot["case_recall"]["auto_prefetch_enabled"])
        self.assertEqual(snapshot["case_recall"]["prefetch_status"], "error")
        self.assertEqual(snapshot["case_recall"]["prefetch_error_type"], "TimeoutError")
        self.assertEqual(snapshot["case_recall"]["case_memory_reason"], "case_memory_search_failed")
        self.assertEqual(snapshot["similar_cases"], [])

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
        session_memory = dict(session.get("session_memory") or {})
        event_queue = list(session_memory.get("session_event_queue") or [])
        self.assertTrue(event_queue)
        self.assertEqual(event_queue[-1]["source"], "user_message")
        self.assertEqual(event_queue[-1]["event_type"], "correction")
        self.assertIsNotNone(event_queue[-1]["consumed_at"])
        working_memory = dict(session_memory.get("working_memory") or {})
        self.assertEqual(working_memory["task_focus"]["original_user_message"], "现在看起来更像数据库连接池问题，还有慢查询")
        self.assertTrue(any("数据库连接池问题" in str(item.get("message") or "") for item in working_memory.get("user_corrections") or []))
        self.assertEqual(updated["diagnosis"]["message_event"]["event_type"], "correction")
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

    async def test_supplement_message_supersedes_pending_approval_without_topic_shift(self) -> None:
        session_id = "supplement-approval"
        approval, interrupt = self._create_pending_approval_fixture(
            session_id=session_id,
            ticket_id="SUPPLEMENT-TICKET-1",
            service="checkout-service",
        )

        updated = await self.orchestrator.post_message(
            session_id,
            ConversationMessageRequest(
                message="补充：22:10 发布后错误率开始升高，只有 prod 异常。",
            ),
        )

        self.assertIn(updated["status"], {"completed", "awaiting_approval"})
        approval_record = self.approval_store.get(approval["approval_id"])
        self.assertIsNotNone(approval_record)
        self.assertEqual(approval_record["status"], "cancelled")
        interrupt_record = self.interrupt_store.get(interrupt["interrupt_id"])
        self.assertIsNotNone(interrupt_record)
        self.assertEqual(interrupt_record["status"], "cancelled")
        self.assertEqual(updated["diagnosis"]["message_event"]["event_type"], "supplement")
        session = self.session_store.get(session_id)
        assert session is not None
        event_queue = list((session.get("session_memory") or {}).get("session_event_queue") or [])
        self.assertTrue(event_queue)
        self.assertEqual(event_queue[-1]["event_type"], "supplement")
        self.assertIsNotNone(event_queue[-1]["consumed_at"])

    async def test_explicit_supplement_message_mode_overrides_default_classifier(self) -> None:
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
                user_id="u-supplement-mode",
                message="发布流程是什么？",
                service="checkout-service",
                environment="prod",
            )
        )
        session_id = created["session"]["session_id"]

        updated = await self.orchestrator.post_message(
            session_id,
            ConversationMessageRequest(
                message="网络问题，重点排查下",
                message_mode="supplement",
            ),
        )

        self.assertEqual(updated["diagnosis"]["message_event"]["event_type"], "supplement")
        session = self.session_store.get(session_id)
        assert session is not None
        event_queue = list((session.get("session_memory") or {}).get("session_event_queue") or [])
        self.assertTrue(event_queue)
        self.assertEqual(event_queue[-1]["event_type"], "supplement")

    async def test_feedback_reopen_is_rejected_without_actionable_guidance(self) -> None:
        session_id = "feedback-no-action"
        ticket_id = "FEEDBACK-NO-ACTION-1"
        interrupt = self.interrupt_store.create_feedback_interrupt(
            session_id=session_id,
            ticket_id=ticket_id,
            reason="诊断结束，但没有动作建议。",
            question="只允许接受当前结论。",
            expected_input_schema={"type": "object"},
            metadata={
                "selected_hypothesis_id": "hypothesis-plain",
                "can_reject_reopen": False,
            },
        )
        incident_state = IncidentState(
            ticket_id=ticket_id,
            user_id="feedback-user",
            message="服务偶发超时",
            thread_id=session_id,
            service="checkout-service",
            environment="prod",
            cluster="prod-shanghai-1",
            namespace="default",
            channel="feishu",
            status="completed",
            metadata={},
        )
        self.session_store.create(
            ConversationSession(
                session_id=session_id,
                thread_id=session_id,
                ticket_id=ticket_id,
                user_id="feedback-user",
                status="completed",
                current_stage="finalize",
                current_agent="diagnosis_agent",
                pending_interrupt_id=interrupt["interrupt_id"],
                incident_state=incident_state,
                session_memory={
                    "original_user_message": incident_state.message,
                    "current_intent": {},
                    "key_entities": {"service": "checkout-service", "environment": "prod"},
                    "clarification_answers": {},
                    "pending_approval": None,
                    "current_stage": "finalize",
                    "pending_interrupt": {"interrupt_id": interrupt["interrupt_id"], "type": "feedback"},
                    "session_event_queue": [],
                },
            )
        )
        self.incident_case_store.upsert(
            {
                "session_id": session_id,
                "thread_id": session_id,
                "ticket_id": ticket_id,
                "service": "checkout-service",
                "cluster": "prod-shanghai-1",
                "namespace": "default",
                "current_agent": "diagnosis_agent",
                "symptom": "服务偶发超时",
                "root_cause": "线索不足",
                "key_evidence": [],
                "final_action": "",
                "approval_required": False,
                "final_conclusion": "当前没有足够证据给出建议动作。",
            }
        )

        with self.assertRaisesRegex(RuntimeError, "actionable guidance or approval"):
            await self.orchestrator.resume_conversation(
                session_id,
                ConversationResumeRequest(
                    interrupt_id=interrupt["interrupt_id"],
                    answer_payload={
                        "human_verified": False,
                        "actual_root_cause_hypothesis": "更像网络抖动",
                        "comment": "请重新分析",
                    },
                ),
            )

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
        self.assertEqual(checkpoint["next_action"], "manual_intervention")
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
        self.assertEqual(recovery["recovery_action"], "manual_intervention")
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

    async def test_d5_execution_recovery_resume_is_rejected(self) -> None:
        session_id = "d5-manual-only-session"
        approval, interrupt = self._create_pending_approval_fixture(
            session_id=session_id,
            ticket_id="D5-TICKET",
            service="recovery-service",
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
                    comment="执行失败，等待人工介入",
                ),
            )

        self.assertEqual(resumed["status"], "failed")
        recovery = self.orchestrator.get_execution_recovery(session_id)
        self.assertEqual(recovery["recovery_action"], "manual_intervention")

        with self.assertRaisesRegex(RuntimeError, "manual-only"):
            await self.orchestrator.resume_execution_recovery(
                session_id,
                {"actor_id": "ops-admin", "comment": "尝试自动恢复"},
            )

        system_events = self.system_event_store.list_for_session(session_id)
        event_types = [event["event_type"] for event in system_events]
        self.assertNotIn("execution.recovery_started", event_types)
        self.assertNotIn("execution.recovery_finished", event_types)

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
