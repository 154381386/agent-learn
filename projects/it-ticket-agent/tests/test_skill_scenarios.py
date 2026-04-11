from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from it_ticket_agent.approval_store import ApprovalStore
from it_ticket_agent.checkpoint_store import CheckpointStore
from it_ticket_agent.execution_store import ExecutionStore
from it_ticket_agent.interrupt_store import InterruptStore
from it_ticket_agent.memory_store import IncidentCaseStore, ProcessMemoryStore
from it_ticket_agent.orchestration.verification_agent import VerificationAgent
from it_ticket_agent.runtime.orchestrator import SupervisorOrchestrator
from it_ticket_agent.schemas import ConversationCreateRequest
from it_ticket_agent.settings import Settings
from it_ticket_agent.skills import SkillRegistry
from it_ticket_agent.skills.local_executor import LocalSkillExecutor
from it_ticket_agent.state.models import ContextSnapshot, Hypothesis, VerificationStep
from it_ticket_agent.system_event_store import SystemEventStore
from it_ticket_agent.tools import __all__ as exported_tools


def _build_snapshot(*, message: str, service: str, mock_scenario: str) -> ContextSnapshot:
    return ContextSnapshot(
        request={
            "ticket_id": "T-SKILL-1",
            "user_id": "u1",
            "message": message,
            "service": service,
            "cluster": "prod-shanghai-1",
            "namespace": "default",
            "mock_scenario": mock_scenario,
        },
        matched_skill_categories=["k8s", "monitor"],
    )


class SkillScenarioExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_check_memory_trend_supports_oom_scenario(self) -> None:
        executor = LocalSkillExecutor()
        snapshot = _build_snapshot(
            message="checkout-service pod OOMKilled，帮我排查内存问题",
            service="checkout-service",
            mock_scenario="oom",
        )

        result = await executor.execute_skill(
            "check_memory_trend",
            params={"service": "checkout-service", "namespace": "default"},
            context_snapshot=snapshot,
        )

        self.assertEqual(result.status, "matched")
        self.assertTrue(result.payload["inspect_pod_logs"]["oom_detected"])
        self.assertEqual(result.payload["inspect_pod_events"]["last_termination_reason"], "OOMKilled")
        self.assertEqual(result.payload["check_service_health"]["health_status"], "unhealthy")

    async def test_check_memory_trend_supports_normal_scenario(self) -> None:
        executor = LocalSkillExecutor()
        snapshot = _build_snapshot(
            message="checkout-service pod OOMKilled，帮我排查内存问题",
            service="checkout-service",
            mock_scenario="normal",
        )

        result = await executor.execute_skill(
            "check_memory_trend",
            params={"service": "checkout-service", "namespace": "default"},
            context_snapshot=snapshot,
        )

        self.assertEqual(result.status, "not_matched")
        self.assertFalse(result.payload["inspect_pod_logs"]["oom_detected"])
        self.assertEqual(result.payload["inspect_pod_events"]["last_termination_reason"], "none")
        self.assertEqual(result.payload["check_service_health"]["health_status"], "healthy")

    async def test_verification_agent_executes_real_skill_pipeline(self) -> None:
        agent = VerificationAgent(SkillRegistry(), skill_executor=LocalSkillExecutor())
        snapshot = _build_snapshot(
            message="checkout-service pod OOMKilled，帮我排查内存问题",
            service="checkout-service",
            mock_scenario="oom",
        )
        hypothesis = Hypothesis(
            hypothesis_id="H-K8S",
            root_cause="Pod 健康异常或资源不足导致服务不稳定",
            confidence_prior=0.8,
            verification_plan=[
                VerificationStep(
                    skill_name="check_memory_trend",
                    params={"service": "checkout-service", "namespace": "default"},
                    purpose="确认是否存在 OOM 与内存持续上涨",
                )
            ],
            expected_evidence="Pod 出现 OOMKilled 或内存上涨明显。",
        )

        result = await agent.verify(hypothesis, snapshot)

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.metadata["verification_mode"], "skill_executor")
        self.assertTrue(result.evidence_items[0].result["payload"]["inspect_pod_logs"]["oom_detected"])


class SkillScenarioRuntimeIntegrationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        db_path = str(Path(self.temp_dir.name) / "skill-scenarios.db")
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

    async def test_conversation_mock_scenario_reaches_skill_tools(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-skill",
                message="checkout-service pod OOMKilled，帮我排查",
                service="checkout-service",
                mock_scenario="oom",
            )
        )

        self.assertIn(result["status"], {"completed", "awaiting_approval"})
        verification_results = list(result["diagnosis"]["verification_results"])
        k8s_result = next(
            item for item in verification_results if "Pod 健康异常" in str(item.get("root_cause") or "")
        )
        memory_item = next(
            item for item in k8s_result["evidence_items"] if item.get("skill") == "check_memory_trend"
        )
        self.assertTrue(memory_item["result"]["payload"]["inspect_pod_logs"]["oom_detected"])
        self.assertEqual(memory_item["result"]["payload"]["inspect_pod_events"]["last_termination_reason"], "OOMKilled")

    async def test_env_case1_supports_plain_question_with_service_in_message(self) -> None:
        with patch.dict(os.environ, {"IT_TICKET_AGENT_CASE": "case1"}, clear=False):
            result = await self.orchestrator.start_conversation(
                ConversationCreateRequest(
                    user_id="u-case1",
                    message="order service为什么总是超时",
                )
            )

        self.assertIn(result["status"], {"completed", "awaiting_approval"})
        verification_results = list(result["diagnosis"]["verification_results"])
        k8s_result = next(item for item in verification_results if "Pod 健康异常" in str(item.get("root_cause") or ""))
        memory_item = next(item for item in k8s_result["evidence_items"] if item.get("skill") == "check_memory_trend")
        self.assertTrue(memory_item["result"]["payload"]["inspect_pod_logs"]["oom_detected"])
        network_result = next(item for item in verification_results if "网络链路异常" in str(item.get("root_cause") or ""))
        network_item = next(item for item in network_result["evidence_items"] if item.get("skill") == "check_network_latency")
        self.assertEqual(network_item["result"]["payload"]["inspect_vpc_connectivity"]["connectivity_status"], "healthy")
        self.assertEqual(network_item["result"]["payload"]["inspect_load_balancer_status"]["lb_status"], "healthy")

    async def test_env_case2_prefers_network_and_monitor_instability(self) -> None:
        with patch.dict(os.environ, {"IT_TICKET_AGENT_CASE": "case2"}, clear=False):
            result = await self.orchestrator.start_conversation(
                ConversationCreateRequest(
                    user_id="u-case2",
                    message="order service为什么总是超时",
                )
            )

        self.assertIn(result["status"], {"completed", "awaiting_approval"})
        verification_results = list(result["diagnosis"]["verification_results"])
        k8s_result = next(item for item in verification_results if "Pod 健康异常" in str(item.get("root_cause") or ""))
        memory_item = next(item for item in k8s_result["evidence_items"] if item.get("skill") == "check_memory_trend")
        self.assertFalse(memory_item["result"]["payload"]["inspect_pod_logs"]["oom_detected"])
        network_result = next(item for item in verification_results if "网络链路异常" in str(item.get("root_cause") or ""))
        network_item = next(item for item in network_result["evidence_items"] if item.get("skill") == "check_network_latency")
        self.assertEqual(network_item["result"]["payload"]["inspect_vpc_connectivity"]["connectivity_status"], "blocked")
        self.assertEqual(network_item["result"]["payload"]["inspect_upstream_dependency"]["dependency_status"], "degraded")
        monitor_result = next(item for item in verification_results if "日志与告警" in str(item.get("root_cause") or ""))
        monitor_item = next(item for item in monitor_result["evidence_items"] if item.get("skill") == "check_log_errors")
        self.assertEqual(monitor_item["result"]["payload"]["inspect_thread_pool_status"]["pool_state"], "saturated")


class ToolInventoryTest(unittest.TestCase):
    def test_exported_tool_inventory_exceeds_thirty(self) -> None:
        tool_names = [name for name in exported_tools if name.endswith("Tool")]
        self.assertGreaterEqual(len(tool_names), 30)


if __name__ == "__main__":
    unittest.main()
