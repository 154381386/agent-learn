from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from it_ticket_agent.approval_store import ApprovalStore
from it_ticket_agent.case_retrieval import CaseRetriever
from it_ticket_agent.checkpoint_store import CheckpointStore
from it_ticket_agent.execution_store import ExecutionStore
from it_ticket_agent.interrupt_store import InterruptStore
from it_ticket_agent.memory_store import IncidentCaseStore, ProcessMemoryStore
from it_ticket_agent.orchestration.ranker_weights import RankerWeightsManager, estimate_adaptive_weights
from it_ticket_agent.orchestration.retrieval_planner import RetrievalPlanner
from it_ticket_agent.runtime.orchestrator import SupervisorOrchestrator
from it_ticket_agent.schemas import ConversationCreateRequest
from it_ticket_agent.settings import Settings
from it_ticket_agent.system_event_store import SystemEventStore
from it_ticket_agent.tools import __all__ as exported_tools


class RuleBasedReactRuntimeIntegrationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        db_path = str(Path(self.temp_dir.name) / "rule-based-react.db")
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

    async def test_mock_scenario_oom_reaches_runtime_tools(self) -> None:
        result = await self.orchestrator.start_conversation(
            ConversationCreateRequest(
                user_id="u-oom",
                message="checkout-service pod OOMKilled，帮我排查",
                service="checkout-service",
                environment="prod",
                mock_scenario="oom",
            )
        )

        self.assertIn(result["status"], {"completed", "awaiting_approval"})
        verification_results = list(result["diagnosis"]["verification_results"])
        k8s_result = next(item for item in verification_results if "Pod 健康异常" in str(item.get("root_cause") or ""))
        log_item = next(item for item in k8s_result["evidence_items"] if item.get("skill") == "inspect_pod_logs")
        event_item = next(item for item in k8s_result["evidence_items"] if item.get("skill") == "inspect_pod_events")
        self.assertTrue(log_item["result"]["payload"]["oom_detected"])
        self.assertEqual(event_item["result"]["payload"]["last_termination_reason"], "OOMKilled")

    async def test_case2_prefers_network_instability(self) -> None:
        with patch.dict(os.environ, {"IT_TICKET_AGENT_CASE": "case2"}, clear=False):
            result = await self.orchestrator.start_conversation(
                ConversationCreateRequest(
                    user_id="u-case2",
                    message="order service为什么总是超时",
                    environment="prod",
                )
            )

        self.assertEqual(result["status"], "completed")
        ranked_primary = result["diagnosis"]["ranked_result"]["primary"]
        self.assertIn("网络链路", ranked_primary["root_cause"])
        network_result = next(
            item for item in result["diagnosis"]["verification_results"] if "网络链路" in str(item.get("root_cause") or "")
        )
        connectivity = next(item for item in network_result["evidence_items"] if item.get("skill") == "inspect_vpc_connectivity")
        dependency = next(item for item in network_result["evidence_items"] if item.get("skill") == "inspect_upstream_dependency")
        self.assertEqual(connectivity["result"]["payload"]["connectivity_status"], "blocked")
        self.assertEqual(dependency["result"]["payload"]["dependency_status"], "degraded")


class ToolInventoryTest(unittest.TestCase):
    def test_exported_tool_inventory_exceeds_twenty(self) -> None:
        tool_names = [name for name in exported_tools if name.endswith("Tool")]
        self.assertGreaterEqual(len(tool_names), 20)


class RankerWeightAdaptationTest(unittest.TestCase):
    def test_estimate_adaptive_weights_uses_verified_feedback_cases(self) -> None:
        weights = estimate_adaptive_weights(
            [
                {
                    "human_verified": True,
                    "selected_hypothesis_id": "H1",
                    "actual_root_cause_hypothesis": "H1",
                    "selected_ranker_features": {
                        "evidence_strength": 0.9,
                        "confidence": 0.8,
                        "history_match": 0.2,
                    },
                },
                {
                    "human_verified": True,
                    "selected_hypothesis_id": "H2",
                    "actual_root_cause_hypothesis": "H3",
                    "selected_ranker_features": {
                        "evidence_strength": 0.2,
                        "confidence": 0.3,
                        "history_match": 0.8,
                    },
                },
            ]
        )

        self.assertGreater(weights["evidence_strength"], weights["history_match"])
        self.assertGreater(weights["confidence"], 0.2)

    def test_ranker_weights_manager_persists_and_activates_snapshots(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            manager = RankerWeightsManager(str(Path(tmp_dir) / "ranker-weights.db"), auto_activate_threshold=2)
            resolved = manager.resolve_weights(
                [
                    {
                        "human_verified": True,
                        "selected_hypothesis_id": "H1",
                        "actual_root_cause_hypothesis": "H1",
                        "selected_ranker_features": {
                            "evidence_strength": 0.9,
                            "confidence": 0.7,
                            "history_match": 0.1,
                        },
                    },
                    {
                        "human_verified": True,
                        "selected_hypothesis_id": "H2",
                        "actual_root_cause_hypothesis": "H2",
                        "selected_ranker_features": {
                            "evidence_strength": 0.8,
                            "confidence": 0.6,
                            "history_match": 0.2,
                        },
                    },
                ]
            )

            snapshots = manager.list_snapshots()
            self.assertTrue(snapshots)
            self.assertEqual(manager.get_active_snapshot()["weights"], resolved)

            manual = manager.save_snapshot(
                {"evidence_strength": 0.2, "confidence": 0.2, "history_match": 0.6},
                sample_count=2,
                strategy="manual_override",
                activate=False,
            )
            active = manager.activate_snapshot(manual["version_id"])
            self.assertEqual(active["version_id"], manual["version_id"])


class CaseRetrieverHybridTest(unittest.IsolatedAsyncioTestCase):
    async def test_case_retriever_keeps_semantic_hybrid_even_with_exact_hits(self) -> None:
        client = SimpleNamespace(case_memory_search=self._fake_case_memory_search)
        retriever = CaseRetriever(client, Settings(rag_enabled=True))
        cases = await retriever.recall(
            service="order-service",
            cluster="prod-shanghai-1",
            namespace="default",
            message="order service 为什么总是 timeout",
            session_id="request-session",
            limit=6,
        )

        sources = {case.case_id: case.recall_source for case in cases}
        self.assertIn("exact", sources["case-order-timeout-1"])
        self.assertIn("pattern", sources["case-payment-timeout-1"])
        self.assertIn("semantic_hybrid", sources["case-payment-timeout-1"])

    async def _fake_case_memory_search(self, **kwargs):
        return {
            "hits": [
                {
                    "case_id": "case-order-timeout-1",
                    "service": "order-service",
                    "symptom": "order timeout",
                    "root_cause": "same service timeout",
                    "summary": "same service timeout",
                    "human_verified": True,
                    "failure_mode": "dependency_timeout",
                    "root_cause_taxonomy": "network_path_instability",
                    "signal_pattern": "",
                    "action_pattern": "",
                    "recall_source": "exact",
                    "score": 0.94,
                },
                {
                    "case_id": "case-payment-timeout-1",
                    "service": "payment-service",
                    "symptom": "cross service timeout",
                    "root_cause": "cross service dependency timeout",
                    "summary": "cross service timeout",
                    "human_verified": True,
                    "failure_mode": "dependency_timeout",
                    "root_cause_taxonomy": "network_path_instability",
                    "signal_pattern": "",
                    "action_pattern": "",
                    "recall_source": "pattern,semantic_hybrid",
                    "score": 0.88,
                },
            ]
        }


class CaseRetrieverTest(unittest.IsolatedAsyncioTestCase):
    async def test_case_retriever_degrades_to_empty_when_case_memory_fails(self) -> None:
        async def failing_case_memory_search(**kwargs):
            raise TimeoutError("case memory unavailable")

        client = SimpleNamespace(case_memory_search=failing_case_memory_search)
        retriever = CaseRetriever(client, Settings(rag_enabled=True))

        cases = await retriever.recall(
            service="order-service",
            cluster="prod-shanghai-1",
            namespace="default",
            message="order-service timeout 502",
            session_id="current-session",
        )

        self.assertEqual(cases, [])
        self.assertEqual(retriever.last_recall_metadata["status"], "error")
        self.assertEqual(retriever.last_recall_metadata["reason"], "case_memory_search_failed")
        self.assertEqual(retriever.last_recall_metadata["error_type"], "TimeoutError")

    async def test_case_retriever_merges_exact_and_pattern_matches(self) -> None:
        client = SimpleNamespace(case_memory_search=self._fake_case_memory_search)
        retriever = CaseRetriever(client, Settings(rag_enabled=True))
        cases = await retriever.recall(
            service="order-service",
            cluster="prod-shanghai-1",
            namespace="default",
            message="order service 为什么总是超时，最近还 OOMKilled",
            session_id="current-session",
        )

        sources = {case.case_id: case.recall_source for case in cases}
        self.assertEqual(sources["case-order-exact"], "exact")
        self.assertIn("pattern", sources["case-payment-pattern"])

    async def _fake_case_memory_search(self, **kwargs):
        return {
            "hits": [
                {
                    "case_id": "case-order-exact",
                    "service": "order-service",
                    "symptom": "order service 超时并 OOM",
                    "root_cause": "Pod 健康异常或资源不足导致服务不稳定",
                    "summary": "exact case",
                    "human_verified": True,
                    "failure_mode": "oom",
                    "root_cause_taxonomy": "resource_exhaustion",
                    "signal_pattern": "pod_restart+heap_pressure",
                    "action_pattern": "restart_pods",
                    "recall_source": "exact",
                    "score": 0.94,
                },
                {
                    "case_id": "case-payment-pattern",
                    "service": "payment-service",
                    "symptom": "payment service OOMKilled",
                    "root_cause": "Pod 健康异常或资源不足导致服务不稳定",
                    "summary": "pattern case",
                    "human_verified": True,
                    "failure_mode": "oom",
                    "root_cause_taxonomy": "resource_exhaustion",
                    "signal_pattern": "pod_restart+heap_pressure",
                    "action_pattern": "restart_pods",
                    "recall_source": "pattern,semantic_hybrid",
                    "score": 0.82,
                },
            ]
        }


class CaseVectorIndexerTest(unittest.TestCase):
    def test_index_case_records_error_without_raising_when_case_memory_sync_fails(self) -> None:
        from it_ticket_agent.case_vector_indexer import CaseVectorIndexer

        class FailingClient:
            async def case_memory_sync(self, *, cases):
                raise ConnectionError("case memory sync unavailable")

        indexer = CaseVectorIndexer(
            Settings(rag_enabled=True),
            SimpleNamespace(list_cases=lambda limit=200: []),
            FailingClient(),
        )

        async def run_index() -> None:
            await indexer.index_case(
                {
                    "case_id": "case-sync-failure",
                    "service": "order-service",
                    "case_status": "verified",
                    "human_verified": True,
                }
            )

        import asyncio

        asyncio.run(run_index())
        self.assertEqual(indexer.last_sync_metadata["status"], "error")
        self.assertEqual(indexer.last_sync_metadata["reason"], "case_memory_sync_failed")
        self.assertEqual(indexer.last_sync_metadata["error_type"], "ConnectionError")

    def test_index_case_skips_unverified_cases_without_syncing(self) -> None:
        from it_ticket_agent.case_vector_indexer import CaseVectorIndexer

        class RecordingClient:
            def __init__(self) -> None:
                self.calls = 0

            async def case_memory_sync(self, *, cases):
                self.calls += 1
                return {"indexed_cases": len(cases)}

        client = RecordingClient()
        indexer = CaseVectorIndexer(
            Settings(rag_enabled=True),
            SimpleNamespace(list_cases=lambda **kwargs: []),
            client,
        )

        async def run_index() -> None:
            await indexer.index_case(
                {
                    "case_id": "case-pending-review",
                    "service": "order-service",
                    "case_status": "pending_review",
                    "human_verified": False,
                }
            )

        import asyncio

        asyncio.run(run_index())
        self.assertEqual(client.calls, 0)
        self.assertEqual(indexer.last_sync_metadata["status"], "skipped")
        self.assertEqual(indexer.last_sync_metadata["reason"], "case_not_verified")

    def test_sync_all_cases_only_indexes_verified_cases(self) -> None:
        from it_ticket_agent.case_vector_indexer import CaseVectorIndexer

        class RecordingClient:
            def __init__(self) -> None:
                self.cases = []

            async def case_memory_sync(self, *, cases):
                self.cases = list(cases)
                return {"indexed_cases": len(cases)}

        class RecordingStore:
            def __init__(self) -> None:
                self.kwargs = {}

            def list_cases(self, **kwargs):
                self.kwargs = dict(kwargs)
                return [
                    {
                        "case_id": "case-verified",
                        "case_status": "verified",
                        "human_verified": True,
                        "service": "order-service",
                    }
                ]

        client = RecordingClient()
        store = RecordingStore()
        indexer = CaseVectorIndexer(Settings(rag_enabled=True), store, client)

        import asyncio

        indexed = asyncio.run(indexer.sync_all_cases(limit=10))
        self.assertEqual(indexed, 1)
        self.assertEqual(store.kwargs["case_status"], "verified")
        self.assertTrue(store.kwargs["human_verified"])
        self.assertEqual(client.cases[0]["case_id"], "case-verified")

    def test_sync_item_contains_stable_checksum_and_source_version(self) -> None:
        from it_ticket_agent.case_vector_indexer import CaseVectorIndexer

        payload = CaseVectorIndexer._to_sync_item(
            {
                "case_id": "case-1",
                "service": "order-service",
                "failure_mode": "oom",
                "root_cause_taxonomy": "resource_exhaustion",
                "signal_pattern": "pod_restart+heap_pressure",
                "action_pattern": "restart_pods",
                "symptom": "order-service OOMKilled",
                "root_cause": "memory pressure",
                "key_evidence": ["OOMKilled"],
                "final_action": "restart_pods",
                "final_conclusion": "recovered after restart",
                "case_status": "verified",
                "human_verified": True,
                "reviewed_by": "oncall",
                "reviewed_at": "2026-04-11T12:10:00Z",
                "updated_at": "2026-04-11T12:00:00Z",
            }
        )

        self.assertEqual(payload["case_status"], "verified")
        self.assertEqual(payload["reviewed_by"], "oncall")
        self.assertEqual(payload["source_version"], "2026-04-11T12:00:00Z")
        self.assertEqual(len(payload["content_checksum"]), 64)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in payload["content_checksum"]))


class RetrievalPlannerTest(unittest.IsolatedAsyncioTestCase):
    async def test_rule_planner_generates_focused_subqueries(self) -> None:
        planner = RetrievalPlanner(Settings(llm_base_url="", llm_api_key="", llm_model=""))
        expansion = await planner.plan(
            request={
                "message": "order service 为什么总是超时，最近还有 OOMKilled",
                "service": "order-service",
            },
            rag_context={"hits": []},
            similar_cases=[],
            matched_tool_domains=["network", "k8s", "db"],
        )

        self.assertTrue(expansion.subqueries)
        queries = [item.query for item in expansion.subqueries]
        self.assertTrue(any("OOMKilled" in item or "heap" in item for item in queries))
        self.assertTrue(any("upstream" in item or "ingress" in item for item in queries))


if __name__ == "__main__":
    unittest.main()
