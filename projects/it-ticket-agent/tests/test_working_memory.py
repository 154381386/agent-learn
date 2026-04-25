from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest

from it_ticket_agent.context.assembler import ContextAssembler
from it_ticket_agent.memory.working_memory import (
    WorkingMemoryCompactionPolicy,
    build_initial_working_memory,
    compact_working_memory,
    compact_working_memory_with_llm,
    merge_working_memory,
    working_memory_compaction_trigger,
)


class WorkingMemoryTest(unittest.TestCase):
    def test_initial_working_memory_promotes_entities_to_confirmed_facts(self) -> None:
        memory = build_initial_working_memory(
            original_user_message="checkout-service 5xx 升高",
            key_entities={"service": "checkout-service", "environment": "prod", "cluster": "prod-shanghai-1"},
            current_stage="ingest",
        )

        self.assertEqual(memory["task_focus"]["original_user_message"], "checkout-service 5xx 升高")
        facts = {item["key"]: item for item in memory["confirmed_facts"]}
        self.assertEqual(facts["entity.service"]["value"], "checkout-service")
        self.assertEqual(facts["entity.environment"]["value"], "prod")
        self.assertEqual(facts["entity.environment"]["source_type"], "system_state")
        self.assertEqual(facts["entity.environment"]["confidence"], 1.0)
        self.assertIn("当前问题：checkout-service 5xx 升高", memory["narrative_summary"])
        self.assertEqual(memory["decision_state"]["current_stage"], "ingest")

    def test_clarification_merge_records_confirmed_facts_and_clears_open_question(self) -> None:
        memory = merge_working_memory(
            None,
            current_stage="awaiting_clarification",
            pending_interrupt={
                "interrupt_id": "int-1",
                "type": "clarification",
                "question": "请补充环境",
                "reason": "missing_required_fields",
            },
        )

        memory = merge_working_memory(
            memory,
            current_stage="routing",
            pending_interrupt=None,
            clarification_answers={
                "int-1": {
                    "raw_answer": {"environment": "prod"},
                    "normalized_answers": {"environment": "prod"},
                }
            },
        )

        facts = {item["key"]: item for item in memory["confirmed_facts"]}
        self.assertEqual(facts["clarification.environment"]["value"], "prod")
        self.assertEqual(facts["clarification.environment"]["source_type"], "user_confirmed")
        self.assertEqual(memory["open_questions"], [])
        self.assertNotIn("pending_interrupt", memory["decision_state"])
        self.assertTrue(any(item.get("ref_type") == "interrupt_id" for item in memory["source_refs"]))
        self.assertIn("用户已澄清：environment=prod", memory["narrative_summary"])

    def test_current_message_updates_without_resetting_original_focus(self) -> None:
        memory = build_initial_working_memory(
            original_user_message="订单服务超时",
            key_entities={"service": "order-service"},
            current_stage="routing",
        )

        memory = merge_working_memory(
            memory,
            current_user_message="补充：只有 prod 环境受影响",
            current_stage="routing",
        )

        self.assertEqual(memory["task_focus"]["original_user_message"], "订单服务超时")
        self.assertEqual(memory["task_focus"]["current_user_message"], "补充：只有 prod 环境受影响")

    def test_session_event_queue_records_user_corrections(self) -> None:
        memory = merge_working_memory(
            None,
            original_user_message="旧问题",
            session_event_queue=[
                {
                    "event_id": "evt-1",
                    "source": "feedback",
                    "event_type": "correction",
                    "message": "真实根因是数据库连接池耗尽",
                    "metadata": {"topic_shift_detected": True, "reason_tags": ["feedback_reopen"]},
                }
            ],
            reset=True,
        )

        self.assertEqual(memory["user_corrections"][-1]["message"], "真实根因是数据库连接池耗尽")
        self.assertEqual(memory["user_corrections"][-1]["source_type"], "user_correction")
        self.assertTrue(memory["user_corrections"][-1]["topic_shift_detected"])
        self.assertIn("用户纠错：真实根因是数据库连接池耗尽", memory["narrative_summary"])
        self.assertTrue(any(item.get("ref_id") == "evt-1" for item in memory["source_refs"]))


    def test_explicit_updates_track_summary_sources_and_ruled_out_hypotheses(self) -> None:
        memory = merge_working_memory(
            None,
            updates={
                "narrative_summary": "发布后 5xx 升高，OOM 初步排除",
                "key_evidence": [
                    {
                        "evidence": "Pod 没有重启",
                        "source": "verification",
                        "source_type": "tool_observed",
                        "confidence": 0.9,
                        "refs": {"observation_id": "obs-1"},
                    }
                ],
                "ruled_out_hypotheses": [
                    {
                        "hypothesis_id": "hyp-oom",
                        "root_cause": "JVM OOM",
                        "reason": "未发现重启和 OOM 日志",
                        "source": "verification",
                        "source_type": "tool_observed",
                        "confidence": 0.85,
                        "refs": {"hypothesis_id": "hyp-oom"},
                    }
                ],
            },
        )

        self.assertIn("发布后 5xx 升高", memory["narrative_summary"])
        self.assertEqual(memory["key_evidence"][-1]["source_type"], "tool_observed")
        self.assertEqual(memory["ruled_out_hypotheses"][-1]["hypothesis_id"], "hyp-oom")
        self.assertTrue(any(item.get("ref_id") == "obs-1" for item in memory["source_refs"]))

    def test_priority_trimming_keeps_high_confidence_user_signal(self) -> None:
        low_value_evidence = [
            {
                "evidence": f"低价值运行时摘要 {index}",
                "source": "diagnosis",
                "source_type": "llm_inferred",
                "confidence": 0.1,
            }
            for index in range(12)
        ]
        memory = merge_working_memory(
            None,
            updates={
                "key_evidence": [
                    {
                        "evidence": "用户确认只有 prod 受影响",
                        "source": "user_message",
                        "source_type": "user_reported",
                        "confidence": 1.0,
                    },
                    *low_value_evidence,
                ]
            },
        )

        evidence_text = [item["evidence"] for item in memory["key_evidence"]]
        self.assertEqual(len(evidence_text), 12)
        self.assertIn("用户确认只有 prod 受影响", evidence_text)

    def test_working_memory_compaction_preserves_protected_signals(self) -> None:
        memory = build_initial_working_memory(
            original_user_message="checkout-service 5xx 升高",
            key_entities={"service": "checkout-service", "environment": "prod"},
            current_stage="diagnosing",
        )
        memory["narrative_summary"] = "；".join(f"历史线索 {index}" for index in range(80))
        memory["key_evidence"] = [
            {
                "evidence": "用户确认只有 prod 受影响",
                "source": "user_message",
                "source_type": "user_reported",
                "confidence": 1.0,
                "refs": {"observation_id": "obs-user"},
            },
            *[
                {
                    "evidence": f"低置信运行时线索 {index}",
                    "source": "diagnosis",
                    "source_type": "llm_inferred",
                    "confidence": 0.1,
                    "refs": {"observation_id": f"obs-low-{index}"},
                }
                for index in range(16)
            ],
        ]
        memory["user_corrections"] = [
            {
                "message": "真实根因不是发布，是数据库连接池耗尽",
                "event_type": "correction",
                "source": "feedback",
                "source_type": "user_correction",
                "confidence": 1.0,
                "refs": {"event_id": "evt-correction"},
            }
        ]
        memory["source_refs"] = [
            {"ref_type": "observation_id", "ref_id": "obs-user", "field": "key_evidence", "source_type": "user_reported"},
            {"ref_type": "event_id", "ref_id": "evt-correction", "field": "user_corrections", "source_type": "user_correction"},
            *[
                {"ref_type": "observation_id", "ref_id": f"obs-low-{index}", "field": "key_evidence", "source_type": "llm_inferred"}
                for index in range(16)
            ],
        ]

        trigger = working_memory_compaction_trigger(
            memory,
            policy=WorkingMemoryCompactionPolicy(max_approx_tokens=10_000, max_narrative_summary_chars=10_000, max_total_items=10),
        )
        compacted = compact_working_memory(memory, trigger=trigger or "unit_test", source="deterministic_priority")

        self.assertEqual(compacted["compaction"]["strategy"], "structured_working_memory")
        self.assertEqual(compacted["compaction"]["source"], "deterministic_priority")
        self.assertLessEqual(len(compacted["key_evidence"]), 8)
        evidence_text = [item["evidence"] for item in compacted["key_evidence"]]
        correction_text = [item["message"] for item in compacted["user_corrections"]]
        self.assertIn("用户确认只有 prod 受影响", evidence_text)
        self.assertIn("真实根因不是发布，是数据库连接池耗尽", correction_text)
        self.assertTrue(any(item.get("ref_id") == "obs-user" for item in compacted["source_refs"]))
        self.assertTrue(any(item.get("ref_id") == "evt-correction" for item in compacted["source_refs"]))

    def test_llm_compaction_keeps_fallback_protected_items(self) -> None:
        class FakeLLM:
            enabled = True

            async def chat(self, messages, tools=None):
                return {
                    "content": json.dumps(
                        {
                            "narrative_summary": "LLM 压缩摘要：prod 仅 checkout-service 受影响，连接池方向优先。",
                            "key_evidence": [
                                {
                                    "evidence": "连接池已接近耗尽",
                                    "source": "diagnosis",
                                    "source_type": "llm_inferred",
                                    "confidence": 0.75,
                                }
                            ],
                            "source_refs": [
                                {"ref_type": "observation_id", "ref_id": "not-from-input", "field": "key_evidence"}
                            ],
                        },
                        ensure_ascii=False,
                    )
                }

        memory = build_initial_working_memory(
            original_user_message="checkout-service 5xx 升高",
            key_entities={"service": "checkout-service", "environment": "prod"},
            current_stage="diagnosing",
        )
        memory["user_corrections"] = [
            {
                "message": "用户确认不是发布导致",
                "event_type": "correction",
                "source": "feedback",
                "source_type": "user_correction",
                "confidence": 1.0,
                "refs": {"event_id": "evt-1"},
            }
        ]
        memory["source_refs"] = [
            {"ref_type": "event_id", "ref_id": "evt-1", "field": "user_corrections", "source_type": "user_correction"}
        ]

        compacted = asyncio.run(compact_working_memory_with_llm(memory, FakeLLM(), trigger="unit_test"))

        self.assertTrue(compacted["compaction"]["llm_used"])
        self.assertEqual(compacted["compaction"]["source"], "llm_structured")
        self.assertIn("LLM 压缩摘要", compacted["narrative_summary"])
        self.assertTrue(any(item.get("message") == "用户确认不是发布导致" for item in compacted["user_corrections"]))
        self.assertFalse(any(item.get("ref_id") == "not-from-input" for item in compacted["source_refs"]))

    def test_context_assembler_prioritizes_working_memory(self) -> None:
        working_memory = build_initial_working_memory(
            original_user_message="订单服务超时",
            key_entities={"service": "order-service"},
            current_stage="routing",
        )
        context = ContextAssembler().assemble(
            request=SimpleNamespace(
                ticket_id="T-1",
                user_id="u-1",
                message="订单服务超时",
                service="order-service",
                cluster="prod-shanghai-1",
                namespace="default",
                channel="feishu",
            ),
            session={
                "session_id": "s-1",
                "thread_id": "th-1",
                "status": "active",
                "current_stage": "routing",
                "incident_state": {"service": "order-service", "status": "running"},
                "session_memory": {"working_memory": working_memory, "session_event_queue": []},
            },
            process_memory_summary={"recent_entries": []},
            incident_case_summary=[{"case_id": "case-1"}],
            entrypoint="ticket_message",
        )

        self.assertEqual(list(context.memory_summary.keys())[0], "working_memory")
        self.assertEqual(
            context.memory_summary["working_memory"]["task_focus"]["original_user_message"],
            "订单服务超时",
        )
        self.assertIn("current_incident_state", context.memory_summary)
        self.assertIn("incident_cases", context.memory_summary)


if __name__ == "__main__":
    unittest.main()
