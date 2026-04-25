from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from it_ticket_agent.graph.nodes import OrchestratorGraphNodes
except ModuleNotFoundError:  # pragma: no cover - optional langgraph dependency in slim test envs
    OrchestratorGraphNodes = None
from it_ticket_agent.memory_store import DiagnosisPlaybookStore
from it_ticket_agent.playbook_extraction import build_playbook_candidate_from_cases
from it_ticket_agent.playbook_retrieval import PlaybookRetriever
from it_ticket_agent.state.models import DiagnosisPlaybookCard


class PlaybookMemoryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "playbooks.db")
        self.store = DiagnosisPlaybookStore(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_only_human_verified_playbook_is_recalled(self) -> None:
        saved = self.store.upsert(
            {
                "playbook_id": "pb-timeout",
                "title": "上游依赖超时排查",
                "status": "verified",
                "human_verified": False,
                "service_type": "k8s_service",
                "failure_modes": ["dependency_timeout"],
                "trigger_conditions": ["timeout", "502"],
                "diagnostic_goal": "验证上游依赖是否退化。",
                "diagnostic_steps": [
                    {"tool_name": "check_service_health", "purpose": "确认影响窗口"},
                    {"tool_name": "inspect_upstream_dependency", "purpose": "确认依赖 timeout 比例"},
                ],
                "evidence_requirements": ["需要实时依赖 timeout 证据"],
                "guardrails": ["不要只凭历史模式下结论"],
            }
        )
        self.assertEqual(saved["status"], "pending_review")

        retriever = PlaybookRetriever(self.store)
        self.assertEqual(
            await retriever.recall(
                service="order-service",
                cluster="prod-shanghai-1",
                namespace="default",
                environment="prod",
                message="order-service timeout 502，请排查",
            ),
            [],
        )

        self.store.review("pb-timeout", human_verified=True, reviewed_by="sre")
        cards = await retriever.recall(
            service="order-service",
            cluster="prod-shanghai-1",
            namespace="default",
            environment="prod",
            message="order-service timeout 502，请排查",
        )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].playbook_id, "pb-timeout")
        self.assertEqual(cards[0].recommended_steps[0]["tool_name"], "check_service_health")
        self.assertGreater(cards[0].recall_score, 0.3)

    async def test_playbook_hit_defers_initial_case_prefetch_unless_history_requested(self) -> None:
        if OrchestratorGraphNodes is None:
            self.skipTest("langgraph is not installed")
        playbook = DiagnosisPlaybookCard(playbook_id="pb-timeout", title="timeout 排查")

        should_prefetch, reason = OrchestratorGraphNodes._should_prefetch_similar_cases(
            message="order-service timeout 502，请排查",
            service="order-service",
            cluster="prod-shanghai-1",
            namespace="default",
            diagnosis_playbooks=[playbook],
        )
        self.assertFalse(should_prefetch)
        self.assertEqual(reason, "deferred_by_playbook")

        should_prefetch, reason = OrchestratorGraphNodes._should_prefetch_similar_cases(
            message="order-service timeout 502，查一下有没有类似历史案例",
            service="order-service",
            cluster="prod-shanghai-1",
            namespace="default",
            diagnosis_playbooks=[playbook],
        )
        self.assertTrue(should_prefetch)
        self.assertEqual(reason, "explicit_history_case_request")

    def test_verified_case_cluster_creates_pending_review_playbook_candidate(self) -> None:
        cases = [
            {
                "case_id": f"case-{index}",
                "case_status": "verified",
                "human_verified": True,
                "service": "order-service",
                "cluster": "prod-shanghai-1",
                "namespace": "default",
                "failure_mode": "dependency_timeout",
                "signal_pattern": "timeout+gateway_unhealthy",
                "verification_passed": True,
            }
            for index in range(3)
        ]

        candidate = build_playbook_candidate_from_cases(cases)

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["status"], "pending_review")
        self.assertFalse(candidate["human_verified"])
        self.assertEqual(candidate["failure_modes"], ["dependency_timeout"])
        self.assertEqual(len(candidate["source_case_ids"]), 3)
        self.assertGreaterEqual(len(candidate["diagnostic_steps"]), 2)


if __name__ == "__main__":
    unittest.main()
