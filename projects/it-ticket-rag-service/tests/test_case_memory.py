from __future__ import annotations

import unittest
from types import SimpleNamespace

from it_ticket_rag_service.case_memory import CaseMemoryService
from it_ticket_rag_service.settings import Settings


class CaseMemorySyncDedupTest(unittest.IsolatedAsyncioTestCase):
    async def test_sync_skips_same_checksum_and_version(self) -> None:
        service = CaseMemoryService(Settings())
        service.store = SimpleNamespace(
            enabled=True,
            get_case_meta=self._get_case_meta,
            upsert_case=self._upsert_case,
            count=self._count,
        )
        service.embedding_client = SimpleNamespace(
            enabled=True,
            embed_texts=self._embed_texts,
        )

        result = await service.sync_cases(
            [
                {
                    "case_id": "case-1",
                    "service": "order-service",
                    "content_checksum": "same-checksum",
                    "source_version": "v1",
                }
            ]
        )

        self.assertEqual(result["indexed_cases"], 0)
        self.assertEqual(result["skipped_cases"], 1)
        self.assertEqual(self.embed_calls, 0)
        self.assertEqual(self.upsert_calls, 0)

    async def test_sync_reindexes_when_checksum_changes(self) -> None:
        service = CaseMemoryService(Settings())
        service.store = SimpleNamespace(
            enabled=True,
            get_case_meta=self._get_case_meta,
            upsert_case=self._upsert_case,
            count=self._count,
        )
        service.embedding_client = SimpleNamespace(
            enabled=True,
            embed_texts=self._embed_texts,
        )

        result = await service.sync_cases(
            [
                {
                    "case_id": "case-1",
                    "service": "order-service",
                    "content_checksum": "new-checksum",
                    "source_version": "v2",
                }
            ]
        )

        self.assertEqual(result["indexed_cases"], 1)
        self.assertEqual(result["skipped_cases"], 0)
        self.assertEqual(self.embed_calls, 1)
        self.assertEqual(self.upsert_calls, 1)

    async def asyncSetUp(self) -> None:
        self.embed_calls = 0
        self.upsert_calls = 0

    async def _get_case_meta(self, case_id: str):
        return {
            "case_id": case_id,
            "content_checksum": "same-checksum",
            "source_version": "v1",
            "embedding_model": Settings().embedding_model,
        }

    async def _upsert_case(self, **kwargs):
        self.upsert_calls += 1

    async def _count(self):
        return 1

    async def _embed_texts(self, texts):
        self.embed_calls += len(texts)
        return [[0.1, 0.2] for _ in texts]


class CaseMemorySameServiceRankingTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_service_same_failure_mode_beats_cross_service_semantic_merge(self) -> None:
        service = CaseMemoryService(Settings())
        rows = [
            {
                "case_id": "case-order-oom-new",
                "service": "order-service",
                "cluster": "prod-shanghai-1",
                "namespace": "default",
                "failure_mode": "oom",
                "root_cause_taxonomy": "resource_exhaustion",
                "signal_pattern": "pod_restart+heap_pressure",
                "action_pattern": "restart_pods",
                "symptom": "order-service OOMKilled Java heap space ready 1/2",
                "root_cause": "JVM heap OOM",
                "final_action": "restart_pods",
                "final_conclusion": "order-service OOM",
                "human_verified": True,
                "document_text": "order-service OOMKilled Java heap space ready 1/2 resource_exhaustion",
            },
            {
                "case_id": "case-payment-oom",
                "service": "payment-service",
                "cluster": "prod-shanghai-1",
                "namespace": "default",
                "failure_mode": "oom",
                "root_cause_taxonomy": "resource_exhaustion",
                "signal_pattern": "pod_restart+heap_pressure",
                "action_pattern": "restart_pods",
                "symptom": "payment-service OOMKilled heap pressure",
                "root_cause": "resource exhaustion",
                "final_action": "restart_pods",
                "final_conclusion": "payment OOM",
                "human_verified": True,
                "document_text": "payment-service OOMKilled heap pressure resource_exhaustion",
            },
        ]
        service.store = SimpleNamespace(
            enabled=True,
            list_cases=self._list_cases_factory(rows),
            semantic_search=self._semantic_search,
            count=self._count,
        )
        service.embedding_client = SimpleNamespace(enabled=True, embed_texts=self._embed)

        result = await service.search(
            query="order-service OOMKilled Java heap space ready 1/2",
            service="order-service",
            cluster="prod-shanghai-1",
            namespace="default",
            failure_mode="oom",
            root_cause_taxonomy="resource_exhaustion",
            top_k=3,
        )

        self.assertEqual(result["hits"][0]["case_id"], "case-order-oom-new")

    def _list_cases_factory(self, rows):
        async def _list_cases(*args, **kwargs):
            return rows
        return _list_cases

    async def _semantic_search(self, **kwargs):
        return [{"case_id": "case-payment-oom", "similarity": 0.98}]

    async def _count(self):
        return 2

    async def _embed(self, texts):
        return [[0.2, 0.8] for _ in texts]


class CaseMemoryRankingTest(unittest.IsolatedAsyncioTestCase):
    async def test_pattern_and_semantic_can_beat_same_service_noise(self) -> None:
        service = CaseMemoryService(Settings())
        rows = [
            {
                "case_id": "case-order-network",
                "service": "order-service",
                "cluster": "prod-shanghai-1",
                "namespace": "default",
                "failure_mode": "dependency_timeout",
                "root_cause_taxonomy": "network_path_instability",
                "signal_pattern": "upstream_jitter",
                "action_pattern": "observe_service",
                "symptom": "upstream jitter causes latency spikes",
                "root_cause": "network jitter",
                "final_action": "observe_service",
                "final_conclusion": "network jitter",
                "human_verified": True,
                "document_text": "service order-service network jitter latency spikes upstream jitter",
            },
            {
                "case_id": "case-payment-oom",
                "service": "payment-service",
                "cluster": "prod-shanghai-1",
                "namespace": "default",
                "failure_mode": "oom",
                "root_cause_taxonomy": "resource_exhaustion",
                "signal_pattern": "heap_pressure",
                "action_pattern": "restart_pods",
                "symptom": "oomkilled and heap pressure",
                "root_cause": "resource exhaustion",
                "final_action": "restart_pods",
                "final_conclusion": "resource exhaustion",
                "human_verified": True,
                "document_text": "payment-service oomkilled heap pressure resource exhaustion restart_pods",
            },
        ]
        service.store = SimpleNamespace(
            enabled=True,
            list_cases=self._list_cases_factory(rows),
            semantic_search=self._semantic_search,
            count=self._count,
        )
        service.embedding_client = SimpleNamespace(
            enabled=True,
            embed_texts=self._embed,
        )

        result = await service.search(
            query="order service 为什么总是超时，最近还有 OOMKilled",
            service="order-service",
            cluster="prod-shanghai-1",
            namespace="default",
            failure_mode="oom",
            root_cause_taxonomy="resource_exhaustion",
            top_k=4,
        )

        self.assertEqual(result["hits"][0]["case_id"], "case-payment-oom")
        self.assertIn("pattern", result["hits"][0]["recall_source"])

    def _list_cases_factory(self, rows):
        async def _list_cases(*args, **kwargs):
            return rows
        return _list_cases

    async def _semantic_search(self, **kwargs):
        return [{"case_id": "case-payment-oom", "similarity": 0.92}]

    async def _count(self):
        return 2

    async def _embed(self, texts):
        return [[0.2, 0.8] for _ in texts]


if __name__ == "__main__":
    unittest.main()
