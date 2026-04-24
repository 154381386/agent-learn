from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from it_ticket_agent.bad_case_store import BadCaseCandidateStore
from it_ticket_agent.evals import (
    build_bad_case_export_payload,
    classify_bad_case_candidate,
    export_bad_case_candidates,
    merge_curated_bad_case_files,
    validate_curated_eval_skeleton,
)


class BadCaseCandidateStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "bad-cases.db")
        self.store = BadCaseCandidateStore(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_store_roundtrip_and_export_status_update(self) -> None:
        created = self.store.create(
            {
                "session_id": "sess-1",
                "thread_id": "thread-1",
                "ticket_id": "ticket-1",
                "source": "runtime_completion",
                "reason_codes": ["retrieval_expansion_no_gain"],
                "severity": "low",
                "request_payload": {"message": "payment-service timeout", "service": "payment-service"},
                "response_payload": {"status": "completed", "message": "阶段性结论"},
                "retrieval_expansion": {
                    "subqueries": [
                        {
                            "query": "payment-service network timeout retry",
                            "target": "both",
                            "root_cause_taxonomy": "network_dependency",
                            "added_rag_hits": 0,
                            "added_case_hits": 0,
                        }
                    ],
                    "added_rag_hits": 0,
                    "added_case_hits": 0,
                },
            }
        )
        fetched = self.store.get(created["candidate_id"])
        assert fetched is not None
        self.assertEqual(fetched["source"], "runtime_completion")
        self.assertEqual(fetched["export_status"], "pending")

        updated = self.store.update_export_status(
            created["candidate_id"],
            export_status="exported",
            export_metadata={"output_path": "/tmp/example.json"},
        )
        assert updated is not None
        self.assertEqual(updated["export_status"], "exported")
        self.assertEqual(updated["export_metadata"]["output_path"], "/tmp/example.json")


class BadCaseExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "bad-cases.db")
        self.output_dir = Path(self.temp_dir.name) / "generated"
        self.store = BadCaseCandidateStore(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_export_bad_case_candidates_writes_files_and_marks_exported(self) -> None:
        created = self.store.create(
            {
                "session_id": "sess-rag-1",
                "thread_id": "thread-rag-1",
                "ticket_id": "ticket-rag-1",
                "source": "runtime_completion",
                "reason_codes": [
                    "retrieval_expansion_no_gain",
                    "retrieval_misaligned_with_primary_root_cause",
                ],
                "severity": "medium",
                "request_payload": {
                    "user_id": "u1",
                    "message": "payment-service timeout 并且数据库告警",
                    "service": "payment-service",
                    "environment": "prod",
                    "cluster": "prod-shanghai-1",
                    "namespace": "default",
                },
                "response_payload": {
                    "status": "completed",
                    "message": "当前更像数据库退化。",
                    "diagnosis": {
                        "route": "react_tool_first",
                        "observations": [
                            {"tool_name": "inspect_upstream_dependency", "result": {"summary": "upstream 抖动"}},
                            {"tool_name": "inspect_connection_pool", "result": {"summary": "连接池打满"}},
                        ],
                    },
                },
                "retrieval_expansion": {
                    "subqueries": [
                        {
                            "query": "payment-service network timeout retry",
                            "target": "both",
                            "reason": "先补充 timeout 背景",
                            "root_cause_taxonomy": "network_dependency",
                            "added_rag_hits": 1,
                            "added_case_hits": 0,
                        },
                        {
                            "query": "payment-service db pool saturation slow query timeout",
                            "target": "both",
                            "reason": "补充数据库退化背景",
                            "root_cause_taxonomy": "database_degradation",
                            "added_rag_hits": 1,
                            "added_case_hits": 1,
                        },
                    ],
                    "added_rag_hits": 2,
                    "added_case_hits": 1,
                },
            }
        )

        results = export_bad_case_candidates(
            self.store,
            output_dir=str(self.output_dir),
            candidate_ids=[created["candidate_id"]],
            mark_exported=True,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["target_dataset"], "rag")
        output_path = Path(results[0]["output_path"])
        self.assertTrue(output_path.exists())
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["candidate_id"], created["candidate_id"])
        self.assertEqual(payload["target_dataset"], "rag")
        self.assertIn("payment-service db pool saturation slow query timeout", payload["mock_boundary_suggestions"]["retrieval_queries"])
        updated = self.store.get(created["candidate_id"])
        assert updated is not None
        self.assertEqual(updated["export_status"], "exported")

    def test_build_bad_case_export_payload_classifies_session_flow_feedback(self) -> None:
        candidate = {
            "candidate_id": "candidate-session-flow",
            "source": "feedback_reopen",
            "severity": "high",
            "reason_codes": ["feedback_reopen", "human_feedback_negative"],
            "request_payload": {
                "user_id": "u2",
                "message": "checkout-service 需要一个低风险自动修复动作",
                "service": "checkout-service",
                "environment": "prod",
            },
            "response_payload": {
                "status": "completed",
                "message": "建议先执行低风险动作。",
                "diagnosis": {"route": "react_tool_first"},
            },
            "human_feedback": {
                "human_verified": False,
                "actual_root_cause_hypothesis": "真实根因更像数据库连接池耗尽",
                "comment": "补充：只有 prod 受影响",
            },
            "conversation_turns": [
                {"role": "user", "content": "checkout-service 需要一个低风险自动修复动作"},
                {"role": "assistant", "content": "建议先执行低风险动作。"},
            ],
            "system_events": [
                {"event_type": "feedback.reopened"},
            ],
        }
        self.assertEqual(classify_bad_case_candidate(candidate), "session_flow")
        payload = build_bad_case_export_payload(candidate)
        self.assertEqual(payload["target_dataset"], "session_flow")
        self.assertEqual(payload["eval_skeleton"]["steps"][1]["action"], "resume_conversation")


class BadCaseCuratedMergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.db_path = str(self.temp_root / "bad-cases.db")
        self.generated_dir = self.temp_root / "generated"
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        self.store = BadCaseCandidateStore(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_validate_curated_eval_skeleton_rejects_placeholders(self) -> None:
        skeleton = {
            "case_id": "todo_demo_case",
            "description": "TODO: fill this",
            "expect": {"_todo": ["fill later"]},
        }
        errors = validate_curated_eval_skeleton(skeleton)
        self.assertTrue(errors)
        self.assertTrue(any("case_id still placeholder" in error for error in errors))
        self.assertTrue(any("description still contains TODO" in error for error in errors))
        self.assertTrue(any("._todo" in error for error in errors))

    def test_merge_curated_bad_case_file_updates_dataset_and_candidate_status(self) -> None:
        candidate = self.store.create(
            {
                "session_id": "sess-merge-1",
                "thread_id": "thread-merge-1",
                "ticket_id": "ticket-merge-1",
                "source": "runtime_completion",
                "reason_codes": ["retrieval_expansion_no_gain"],
                "severity": "medium",
                "request_payload": {
                    "user_id": "u-merge",
                    "message": "payment-service timeout 并且数据库告警",
                    "service": "payment-service",
                    "environment": "prod",
                },
                "response_payload": {
                    "status": "completed",
                    "message": "当前更像数据库退化。",
                    "diagnosis": {
                        "route": "react_tool_first",
                        "observations": [
                            {"tool_name": "inspect_connection_pool", "result": {"summary": "连接池打满"}},
                        ],
                    },
                },
                "retrieval_expansion": {
                    "subqueries": [
                        {
                            "query": "payment-service db pool saturation slow query timeout",
                            "target": "both",
                            "reason": "补充数据库退化背景",
                            "root_cause_taxonomy": "database_degradation",
                            "added_rag_hits": 1,
                            "added_case_hits": 1,
                        }
                    ],
                    "added_rag_hits": 1,
                    "added_case_hits": 1,
                },
                "export_status": "exported",
            }
        )
        payload = build_bad_case_export_payload(candidate)
        payload["eval_skeleton"]["case_id"] = "curated_payment_db_pool_regression"
        payload["eval_skeleton"]["description"] = "数据库连接池退化样本应保持主因收敛到 database_degradation。"
        payload["eval_skeleton"]["expect"].pop("_todo", None)
        payload["todo"] = []

        generated_file = self.generated_dir / "rag_curated.json"
        generated_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        dataset_path = self.temp_root / "rag_cases.json"
        dataset_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "description": "temp rag dataset",
                    "gate": {"min_pass_rate": 1.0},
                    "cases": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        results = merge_curated_bad_case_files(
            input_paths=[generated_file],
            project_root=self.temp_root,
            store=self.store,
            dataset_paths={"rag": dataset_path},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "appended")
        merged_dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
        self.assertEqual(len(merged_dataset["cases"]), 1)
        self.assertEqual(merged_dataset["cases"][0]["case_id"], "curated_payment_db_pool_regression")
        updated = self.store.get(candidate["candidate_id"])
        assert updated is not None
        self.assertEqual(updated["export_status"], "merged")
        self.assertEqual(updated["export_metadata"]["merged_case_id"], "curated_payment_db_pool_regression")


if __name__ == "__main__":
    unittest.main()
