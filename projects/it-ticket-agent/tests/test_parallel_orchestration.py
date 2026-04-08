from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from it_ticket_agent.approval_store import ApprovalStore
from it_ticket_agent.checkpoint_store import CheckpointStore
from it_ticket_agent.execution_store import ExecutionStore
from it_ticket_agent.interrupt_store import InterruptStore
from it_ticket_agent.memory_store import IncidentCaseStore, ProcessMemoryStore
from it_ticket_agent.orchestration import Aggregator, ParallelDispatcher
from it_ticket_agent.runtime.contracts import (
    AgentAction,
    AgentFinding,
    AgentResult,
    RoutingDecision,
    TaskEnvelope,
)
from it_ticket_agent.runtime.orchestrator import SupervisorOrchestrator
from it_ticket_agent.schemas import ConversationCreateRequest
from it_ticket_agent.settings import Settings
from it_ticket_agent.system_event_store import SystemEventStore


class _StaticAgent:
    def __init__(self, result: AgentResult | None = None, *, delay_sec: float = 0.0, error: Exception | None = None) -> None:
        self._result = result
        self._delay_sec = delay_sec
        self._error = error

    async def run(self, task: TaskEnvelope) -> AgentResult:
        if self._delay_sec > 0:
            await asyncio.sleep(self._delay_sec)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result.model_copy(update={"execution_path": task.mode})


class ParallelDispatcherTest(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_isolates_failures_and_timeouts(self) -> None:
        dispatcher = ParallelDispatcher(max_concurrency=2, timeout_sec=0.02)
        task = TaskEnvelope(task_id="task-1", ticket_id="ticket-1", goal="并行诊断")
        agents = {
            "ok_agent": _StaticAgent(
                AgentResult(
                    agent_name="ok_agent",
                    domain="general",
                    status="completed",
                    summary="ok",
                )
            ),
            "boom_agent": _StaticAgent(error=RuntimeError("boom")),
            "slow_agent": _StaticAgent(
                AgentResult(
                    agent_name="slow_agent",
                    domain="general",
                    status="completed",
                    summary="slow",
                ),
                delay_sec=0.2,
            ),
        }

        batch = await dispatcher.dispatch(
            task=task,
            candidate_agents=["ok_agent", "boom_agent", "slow_agent", "missing_agent"],
            agents=agents,
        )

        self.assertEqual([result.agent_name for result in batch.results], ["ok_agent"])
        self.assertEqual(len(batch.failures), 3)
        failure_types = {failure.agent_name: failure.error_type for failure in batch.failures}
        self.assertEqual(failure_types["boom_agent"], "RuntimeError")
        self.assertEqual(failure_types["slow_agent"], "TimeoutError")
        self.assertEqual(failure_types["missing_agent"], "AgentNotConfigured")


class AggregatorTest(unittest.TestCase):
    def test_aggregator_merges_results_and_deduplicates_actions(self) -> None:
        aggregator = Aggregator()
        result_a = AgentResult(
            agent_name="cicd_agent",
            domain="cicd",
            status="completed",
            summary="发布窗口内 pipeline 失败",
            findings=[AgentFinding(title="Pipeline", detail="最近一次发布失败", severity="high")],
            evidence=["pipeline failed", "release window matched"],
            recommended_actions=[
                AgentAction(
                    action="cicd.rollback_release",
                    risk="high",
                    reason="发布失败后建议回滚",
                    params={"service": "order-service"},
                )
            ],
            risk_level="high",
            confidence=0.82,
        )
        result_b = AgentResult(
            agent_name="general_sre_agent",
            domain="general",
            status="completed",
            summary="应用错误率升高",
            findings=[AgentFinding(title="Errors", detail="5xx 激增", severity="high")],
            evidence=["5xx increased", "release window matched"],
            recommended_actions=[
                AgentAction(
                    action="cicd.rollback_release",
                    risk="high",
                    reason="发布失败后建议回滚",
                    params={"service": "order-service"},
                )
            ],
            risk_level="medium",
            confidence=0.51,
        )

        aggregated = aggregator.aggregate([result_a, result_b], ticket_id="ticket-1")

        self.assertEqual(aggregated.aggregated_result.agent_name, "aggregator")
        self.assertEqual(aggregated.aggregated_result.status, "completed")
        self.assertEqual(len(aggregated.subagent_results), 2)
        self.assertEqual(len(aggregated.aggregated_result.recommended_actions), 1)
        self.assertEqual(aggregated.aggregated_result.risk_level, "high")
        self.assertIn("cicd_agent", aggregated.aggregated_result.summary)
        self.assertIn("general_sre_agent", aggregated.aggregated_result.summary)


class ParallelGraphSmokeTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_fan_out_path_aggregates_multiple_subagent_results(self) -> None:
        decision = RoutingDecision(
            agent_name="cicd_agent",
            mode="fan_out",
            route_source="test",
            reason="进入 Phase 2 fan-out smoke",
            confidence=0.9,
            candidate_agents=["cicd_agent", "general_sre_agent"],
        )
        with patch.object(self.orchestrator.supervisor, "route", AsyncMock(return_value=decision)):
            response = await self.orchestrator.start_conversation(
                ConversationCreateRequest(
                    user_id="u1",
                    message="order-service 发布后 5xx 激增，帮我排查",
                    service="order-service",
                )
            )

        diagnosis = dict(response.get("diagnosis") or {})
        incident_state = dict(diagnosis.get("incident_state") or {})
        self.assertEqual(diagnosis.get("agent_name"), "aggregator")
        self.assertEqual(diagnosis.get("routing", {}).get("mode"), "fan_out")
        self.assertEqual(len(incident_state.get("subagent_results") or []), 2)
        graph_notes = ((diagnosis.get("graph") or {}).get("transition_notes") or [])
        self.assertTrue(any("aggregator synthesized" in note for note in graph_notes))
        self.assertIn(response.get("status"), {"completed", "awaiting_approval"})

    async def test_supervisor_auto_fan_out_enables_parallel_network_and_approval_context(self) -> None:
        with patch("it_ticket_agent.agents.cicd.CICDAgent._should_request_rollback", return_value=True):
            response = await self.orchestrator.start_conversation(
                ConversationCreateRequest(
                    user_id="u2",
                    message="order-service 发布后 ingress 502 超时，怀疑网络或发布变更导致",
                    service="order-service",
                )
            )

        diagnosis = dict(response.get("diagnosis") or {})
        routing = dict(diagnosis.get("routing") or {})
        incident_state = dict(diagnosis.get("incident_state") or {})
        subagent_results = list(incident_state.get("subagent_results") or [])
        subagent_names = [item.get("agent_name") for item in subagent_results if isinstance(item, dict)]

        self.assertEqual(routing.get("mode"), "fan_out")
        self.assertIn("cicd_agent", routing.get("candidate_agents") or [])
        self.assertIn("network_agent", routing.get("candidate_agents") or [])
        self.assertIn("cicd_agent", subagent_names)
        self.assertIn("network_agent", subagent_names)
        self.assertEqual(response.get("status"), "awaiting_approval")

        approval_request = dict(response.get("approval_request") or {})
        approval_id = str(approval_request.get("approval_id") or "")
        self.assertTrue(approval_id)
        stored_request = self.approval_store.get_request(approval_id)
        self.assertIsNotNone(stored_request)
        assert stored_request is not None
        self.assertIn("aggregated_result", stored_request.context)
        self.assertIn("source_agents", stored_request.context)
        self.assertIn("network_agent", stored_request.context.get("source_agents") or [])
        self.assertEqual(str(stored_request.context.get("aggregated_result", {}).get("agent_name") or ""), "aggregator")

        session_payload = dict(response.get("session") or {})
        session = self.session_store.get_by_thread_id(str(session_payload.get("thread_id") or ""))
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.get("current_agent"), "aggregator")
