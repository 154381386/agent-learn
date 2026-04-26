from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from it_ticket_agent.case_retrieval import infer_failure_mode, infer_root_cause_taxonomy
from it_ticket_agent.evals import (
    AgentEvalCase,
    AgentEvalDataset,
    AgentEvalGate,
    AgentEvalExpectation,
    AgentEvalCaseResult,
    AgentEvalObservation,
    AgentEvalRunner,
    ToolProfileRef,
    build_eval_report,
    build_session_flow_report,
    evaluate_agent_eval_gate,
    evaluate_session_flow_gate,
    extract_eval_observation,
    load_agent_eval_dataset,
    load_session_flow_eval_dataset,
    resolve_tool_profile_mock_responses,
    score_agent_eval_case,
    score_session_flow_step,
    serialize_report,
    SessionFlowEvalCase,
    SessionFlowEvalDataset,
    SessionFlowEvalGate,
    SessionFlowEvalReport,
    SessionFlowEvalRunner,
    SessionFlowEvalStep,
    SessionFlowStepExpectation,
    SessionFlowStepObservation,
)
from it_ticket_agent.runtime.contracts import TaskEnvelope
from it_ticket_agent.runtime.react_supervisor import ReactSupervisor
from it_ticket_agent.schemas import ConversationCreateRequest
from it_ticket_agent.settings import Settings
from it_ticket_agent.state.models import ContextSnapshot, RAGContextBundle
from it_ticket_agent.tools.db import InspectConnectionPoolTool
from it_ticket_agent.tools.runtime import build_default_tools


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MOCK_PROFILES_PATH = PROJECT_ROOT / "data" / "mock_case_profiles.json"


class ToolProfileResolutionTest(unittest.TestCase):
    def test_resolve_tool_profile_mock_responses_loads_service_profile(self) -> None:
        payload = resolve_tool_profile_mock_responses(
            ToolProfileRef(case_id="case2", service="order-service"),
            profiles_path=MOCK_PROFILES_PATH,
        )

        self.assertIn("inspect_vpc_connectivity", payload)
        self.assertIn("inspect_upstream_dependency", payload)
        self.assertEqual(payload["inspect_vpc_connectivity"]["payload"]["connectivity_status"], "blocked")
        self.assertEqual(payload["inspect_upstream_dependency"]["payload"]["dependency_status"], "degraded")

    def test_build_default_tools_registers_search_retrieval_tools(self) -> None:
        tools = build_default_tools(
            settings=Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            )
        )

        self.assertIn("search_knowledge_base", tools)
        self.assertIn("search_similar_incidents", tools)


class RetrievalTaxonomyInferenceTest(unittest.TestCase):
    def test_infer_root_cause_taxonomy_prefers_db_over_generic_timeout_tokens(self) -> None:
        text = "数据库连接池、慢查询或事务回滚导致依赖超时"

        self.assertEqual(infer_failure_mode(text), "db_pool_saturation")
        self.assertEqual(infer_root_cause_taxonomy(text), "database_degradation")

    def test_infer_root_cause_taxonomy_prefers_release_over_generic_5xx_tokens(self) -> None:
        text = "近期发布回归导致 502 和网关异常"

        self.assertEqual(infer_failure_mode(text), "deploy_regression")
        self.assertEqual(infer_root_cause_taxonomy(text), "release_regression")


class ObservationScoreTest(unittest.TestCase):
    def test_extract_and_score_eval_observation(self) -> None:
        observation = extract_eval_observation(
            {
                "status": "completed",
                "message": "更接近网络链路或上游依赖退化。",
                "diagnosis": {
                    "route": "react_tool_first",
                    "conclusion": "更接近网络链路或上游依赖退化。",
                    "tool_calls_used": 2,
                    "stop_reason": "evidence_sufficient_early_stop",
                    "react_runtime": {
                        "expanded_domains": ["db"],
                        "expansion_probe_count": 1,
                        "expansion_probe_tools": ["inspect_connection_pool"],
                        "rejected_tool_call_count": 1,
                        "rejected_tool_call_names": ["check_pod_status"],
                    },
                    "graph": {
                        "transition_notes": [
                            "react supervisor expanded domains: db",
                            "react supervisor auto expansion probe: inspect_connection_pool",
                        ]
                    },
                    "observations": [
                        {
                            "tool_name": "inspect_vpc_connectivity",
                            "result": {
                                "evidence": ["connectivity_status=blocked"],
                            },
                        },
                        {
                            "tool_name": "inspect_upstream_dependency",
                            "result": {
                                "evidence": ["dependency_status=degraded"],
                            },
                        },
                    ],
                },
            }
        )

        score = score_agent_eval_case(
            AgentEvalExpectation(
                status="completed",
                route="react_tool_first",
                stop_reason="evidence_sufficient_early_stop",
                required_tools=["inspect_vpc_connectivity", "inspect_upstream_dependency"],
                first_any_tools=["inspect_vpc_connectivity", "inspect_upstream_dependency"],
                first_any_tools_min_matches=2,
                first_any_tools_window=2,
                expanded_domains=["db"],
                expansion_probe_required=True,
                conclusion_contains=["网络链路"],
                evidence_contains=["blocked", "degraded"],
                min_tool_calls_used=2,
                max_tool_calls_used=3,
                max_rejected_tool_calls=1,
            ),
            observation,
        )

        self.assertTrue(score.passed)
        self.assertEqual(score.passed_checks, score.total_checks)
        self.assertEqual(observation.stop_reason, "evidence_sufficient_early_stop")
        self.assertEqual(observation.expanded_domains, ["db"])
        self.assertEqual(observation.expansion_probe_count, 1)
        self.assertEqual(observation.rejected_tool_call_count, 1)

    def test_extract_eval_observation_includes_rag_metrics(self) -> None:
        observation = extract_eval_observation(
            {
                "status": "completed",
                "message": "支付服务发布前需要先完成构建、审批和发布窗口确认。",
                "diagnosis": {
                    "conclusion": "支付服务发布前需要先完成构建、审批和发布窗口确认。",
                    "routing": {"intent": "direct_answer"},
                    "sources": ["支付服务发布手册 / 发布流程 / 第 1 段"],
                    "context_snapshot": {
                        "retrieval_expansion": {
                            "subqueries": [
                                {
                                    "query": "payment deploy runbook",
                                    "target": "knowledge",
                                    "root_cause_taxonomy": "release_regression",
                                    "added_rag_hits": 2,
                                    "added_case_hits": 1,
                                }
                            ],
                            "added_rag_hits": 2,
                            "added_case_hits": 1,
                            "missing_evidence": ["是否存在近期发布回归或配置变更"],
                        }
                    },
                },
            }
        )

        score = score_agent_eval_case(
            AgentEvalExpectation(
                status="completed",
                intent="direct_answer",
                min_sources_count=1,
                min_retrieval_subquery_count=1,
                min_added_rag_hits=1,
                min_added_case_hits=1,
                retrieval_query_contains=["payment deploy runbook"],
                retrieval_query_metrics=[
                    {
                        "query_contains": "payment deploy runbook",
                        "added_rag_hits": 2,
                        "added_case_hits": 1,
                    }
                ],
                missing_evidence_contains=["发布回归"],
            ),
            observation,
        )

        self.assertTrue(score.passed)
        self.assertEqual(observation.sources_count, 1)
        self.assertEqual(observation.retrieval_subquery_count, 1)
        self.assertEqual(observation.added_rag_hits, 2)
        self.assertEqual(observation.added_case_hits, 1)
        self.assertEqual(observation.retrieval_queries, ["payment deploy runbook"])
        self.assertEqual(len(observation.retrieval_query_metrics), 1)
        self.assertEqual(observation.retrieval_query_metrics[0].query, "payment deploy runbook")
        self.assertEqual(observation.retrieval_query_metrics[0].root_cause_taxonomy, "release_regression")
        self.assertEqual(observation.retrieval_query_metrics[0].added_rag_hits, 2)
        self.assertEqual(observation.retrieval_query_metrics[0].added_case_hits, 1)
        self.assertTrue(observation.retrieval_query_metrics[0].matches_primary_root_cause_taxonomy)
        self.assertEqual(observation.missing_evidence, ["是否存在近期发布回归或配置变更"])

    def test_extract_eval_observation_includes_case_memory_metrics(self) -> None:
        observation = extract_eval_observation(
            {
                "status": "completed",
                "message": "继续用 live tool 诊断。",
                "diagnosis": {
                    "route": "react_tool_first",
                    "context_snapshot": {
                        "case_recall": {
                            "auto_prefetch_enabled": True,
                            "prefetch_status": "error",
                            "prefetch_error_type": "TimeoutError",
                            "case_memory_reason": "case_memory_search_failed",
                            "prefetched_case_count": 0,
                            "tool_search_count": 1,
                            "last_tool_status": "completed",
                            "last_tool_hit_count": 0,
                            "tool_failures": [
                                {"query": "payment-service timeout", "error": "case_memory_search_failed"}
                            ],
                        }
                    },
                },
            }
        )

        self.assertEqual(observation.case_memory_state, "failed")
        self.assertEqual(observation.case_memory_reason, "case_memory_search_failed")
        self.assertEqual(observation.case_memory_prefetch_status, "error")
        self.assertEqual(observation.case_memory_tool_search_count, 1)
        self.assertEqual(observation.case_memory_last_tool_hit_count, 0)
        self.assertEqual(observation.case_memory_tool_failure_count, 1)

    def test_build_eval_report_includes_search_metrics(self) -> None:
        report = build_eval_report(
            [
                AgentEvalCaseResult(
                    case_id="c1",
                    description="",
                    passed=True,
                    score=1.0,
                    passed_checks=3,
                    total_checks=3,
                    duration_ms=120,
                    observation=AgentEvalObservation(
                        status="completed",
                        route="react_tool_first",
                        intent="diagnose",
                        stop_reason="evidence_sufficient_early_stop",
                        pending_interrupt_type="",
                        approval_required=False,
                        message="",
                        conclusion="",
                        primary_root_cause="",
                        tool_names=["inspect_vpc_connectivity", "inspect_upstream_dependency"],
                        tool_calls_used=2,
                        evidence=["blocked"],
                        expansion_probe_count=1,
                        rejected_tool_call_count=0,
                        case_memory_state="empty",
                        case_memory_reason="case_memory_empty",
                    ),
                ),
                AgentEvalCaseResult(
                    case_id="c2",
                    description="",
                    passed=False,
                    score=0.5,
                    passed_checks=1,
                    total_checks=2,
                    duration_ms=240,
                    observation=AgentEvalObservation(
                        status="completed",
                        route="react_tool_first",
                        intent="diagnose",
                        stop_reason="model_answered",
                        pending_interrupt_type="",
                        approval_required=False,
                        message="",
                        conclusion="",
                        primary_root_cause="",
                        tool_names=["check_recent_deployments"],
                        tool_calls_used=1,
                        evidence=["deploy"],
                        expansion_probe_count=0,
                        rejected_tool_call_count=2,
                        case_memory_state="failed",
                        case_memory_reason="case_memory_search_failed",
                    ),
                ),
            ]
        )

        self.assertEqual(report.expansion_probe_cases, 1)
        self.assertEqual(report.rejected_tool_call_cases, 1)
        self.assertEqual(report.rejected_tool_call_total, 2)
        self.assertEqual(report.stop_reason_counts["evidence_sufficient_early_stop"], 1)
        self.assertEqual(report.stop_reason_counts["model_answered"], 1)
        self.assertEqual(report.avg_tool_calls_used, 1.5)
        self.assertEqual(report.case_memory_state_counts, {"empty": 1, "failed": 1})
        self.assertEqual(report.case_memory_reason_counts["case_memory_search_failed"], 1)
        serialized = serialize_report(report)
        self.assertEqual(serialized["case_memory_state_counts"], {"empty": 1, "failed": 1})

    def test_evaluate_agent_eval_gate_checks_thresholds(self) -> None:
        report = build_eval_report(
            [
                AgentEvalCaseResult(
                    case_id="c1",
                    description="",
                    passed=True,
                    score=1.0,
                    passed_checks=2,
                    total_checks=2,
                    duration_ms=100,
                    observation=AgentEvalObservation(
                        status="completed",
                        route="react_tool_first",
                        intent="diagnose",
                        stop_reason="rule_based_no_llm",
                        pending_interrupt_type="",
                        approval_required=False,
                        message="",
                        conclusion="",
                        primary_root_cause="",
                        tool_names=["inspect_vpc_connectivity"],
                        tool_calls_used=1,
                        evidence=[],
                        expansion_probe_count=0,
                        rejected_tool_call_count=0,
                    ),
                ),
                AgentEvalCaseResult(
                    case_id="c2",
                    description="",
                    passed=False,
                    score=0.5,
                    passed_checks=1,
                    total_checks=2,
                    duration_ms=200,
                    observation=AgentEvalObservation(
                        status="completed",
                        route="react_tool_first",
                        intent="diagnose",
                        stop_reason="model_answered",
                        pending_interrupt_type="",
                        approval_required=False,
                        message="",
                        conclusion="",
                        primary_root_cause="",
                        tool_names=["inspect_connection_pool", "inspect_slow_queries", "inspect_db_instance_health"],
                        tool_calls_used=3,
                        evidence=[],
                        expansion_probe_count=1,
                        rejected_tool_call_count=2,
                    ),
                ),
            ]
        )

        gate = evaluate_agent_eval_gate(
            AgentEvalGate(
                min_pass_rate=0.75,
                max_avg_tool_calls_used=1.5,
                max_rejected_tool_call_total=1,
            ),
            report,
        )

        self.assertIsNotNone(gate)
        self.assertFalse(gate.passed)
        self.assertEqual(gate.total_checks, 3)
        failed_names = {check.name for check in gate.checks if not check.passed}
        self.assertEqual(failed_names, {"min_pass_rate", "max_avg_tool_calls_used", "max_rejected_tool_call_total"})

    def test_score_session_flow_step_checks_session_specific_fields(self) -> None:
        observation = SessionFlowStepObservation(
            action="resume_conversation",
            response_status="completed",
            session_status="completed",
            session_stage="finalize",
            current_agent="hypothesis_graph",
            pending_interrupt_type="",
            message="已记录人工反馈，本次诊断结果已补充到案例库。",
            conclusion="已记录人工反馈，本次诊断结果已补充到案例库。",
            route="react_tool_first",
            intent="hypothesis_graph",
            stop_reason="rule_based_no_llm",
            approval_required=False,
            primary_root_cause="当前更适合先执行低风险观测动作确认服务状态",
            tool_names=["check_service_health", "check_recent_alerts"],
            tool_calls_used=2,
            evidence=["health_status=degraded"],
            case_exists=True,
            human_verified=True,
            actual_root_cause_hypothesis="H-OBSERVE",
            current_intent_history_length=1,
            new_system_event_types=["conversation.resumed", "feedback.received"],
            new_approval_event_types=[],
        )

        score = score_session_flow_step(
            SessionFlowStepExpectation(
                response_status="completed",
                session_status="completed",
                route="react_tool_first",
                human_verified=True,
                actual_root_cause_contains=["H-OBSERVE"],
                new_system_event_types=["feedback.received"],
                min_current_intent_history_length=1,
            ),
            observation,
        )

        self.assertTrue(score.passed)
        self.assertEqual(score.passed_checks, score.total_checks)

    def test_score_session_flow_step_checks_message_event_fields(self) -> None:
        observation = SessionFlowStepObservation(
            action="post_message",
            response_status="completed",
            session_status="completed",
            session_stage="finalize",
            current_agent="hypothesis_graph",
            pending_interrupt_type="",
            message="已完成重新诊断。",
            conclusion="已完成重新诊断。",
            route="react_tool_first",
            intent="diagnose",
            stop_reason="model_answered",
            approval_required=False,
            primary_root_cause="数据库连接池、慢查询或事务回滚导致依赖超时",
            tool_names=["inspect_connection_pool", "inspect_slow_queries"],
            tool_calls_used=2,
            evidence=["pool_state=saturated", "slow_query_count=24"],
            case_exists=True,
            human_verified=False,
            actual_root_cause_hypothesis="",
            current_intent_history_length=1,
            message_event_type="supplement",
            message_event_topic_shift_detected=True,
            message_event_incremental_tool_domains=["db"],
            new_system_event_types=["message.received"],
            new_approval_event_types=[],
        )

        score = score_session_flow_step(
            SessionFlowStepExpectation(
                response_status="completed",
                message_event_type="supplement",
                message_event_topic_shift_detected=True,
                message_event_incremental_tool_domains=["db"],
                new_system_event_types=["message.received"],
            ),
            observation,
        )

        self.assertTrue(score.passed)
        self.assertEqual(score.passed_checks, score.total_checks)

    def test_evaluate_session_flow_gate_checks_thresholds(self) -> None:
        report = SessionFlowEvalReport(
            total_cases=2,
            passed_cases=1,
            failed_cases=1,
            errored_cases=0,
            pass_rate=0.5,
            total_steps=4,
            passed_steps=3,
            step_pass_rate=0.75,
            avg_duration_ms=2200.0,
        )

        gate = evaluate_session_flow_gate(
            SessionFlowEvalGate(
                min_pass_rate=1.0,
                min_step_pass_rate=1.0,
                max_avg_duration_ms=2000.0,
            ),
            report,
        )

        self.assertIsNotNone(gate)
        self.assertFalse(gate.passed)
        self.assertEqual(gate.total_checks, 3)
        failed_names = {check.name for check in gate.checks if not check.passed}
        self.assertEqual(failed_names, {"min_pass_rate", "min_step_pass_rate", "max_avg_duration_ms"})


class FakeToolCallingLLM:
    def __init__(self) -> None:
        self.enabled = True
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-vpc",
                        "function": {
                            "name": "inspect_vpc_connectivity",
                            "arguments": json.dumps({"service": "order-service"}, ensure_ascii=False),
                        },
                    },
                    {
                        "id": "call-upstream",
                        "function": {
                            "name": "inspect_upstream_dependency",
                            "arguments": json.dumps({"service": "order-service"}, ensure_ascii=False),
                        },
                    },
                ],
            }
        return {
            "content": json.dumps(
                {
                    "final_answer": "当前更接近网络链路或上游依赖退化。",
                    "confidence": 0.87,
                },
                ensure_ascii=False,
            )
        }

    @staticmethod
    def extract_json(content: str):
        return json.loads(content)


class FakeDbProfileLLM:
    def __init__(self) -> None:
        self.enabled = True
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-db-pool",
                        "function": {
                            "name": "inspect_connection_pool",
                            "arguments": json.dumps({"service": "order-service"}, ensure_ascii=False),
                        },
                    },
                    {
                        "id": "call-db-slow",
                        "function": {
                            "name": "inspect_slow_queries",
                            "arguments": json.dumps({"service": "order-service"}, ensure_ascii=False),
                        },
                    },
                ],
            }
        return {
            "content": json.dumps(
                {
                    "final_answer": "当前更接近数据库连接池饱和。",
                    "confidence": 0.91,
                },
                ensure_ascii=False,
            )
        }

    @staticmethod
    def extract_json(content: str):
        return json.loads(content)


class FakeNoToolAnswerLLM:
    def __init__(self) -> None:
        self.enabled = True
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        return {
            "content": json.dumps(
                {
                    "final_answer": "我先直接判断为网络链路问题。",
                    "confidence": 0.9,
                },
                ensure_ascii=False,
            )
        }

    @staticmethod
    def extract_json(content: str):
        return json.loads(content)


class FakeKnowledgeFirstLLM:
    def __init__(self) -> None:
        self.enabled = True
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            tool_names = {item["function"]["name"] for item in list(tools or [])}
            tool_calls = []
            if "search_knowledge_base" in tool_names:
                tool_calls.append(
                    {
                        "id": "call-knowledge",
                        "function": {
                            "name": "search_knowledge_base",
                            "arguments": json.dumps(
                                {
                                    "query": "order-service 灰度发布后一直 502，你先查下有没有相关手册或已知回归模式，再结合实时信号判断",
                                    "service": "order-service",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                )
            if "check_recent_deployments" in tool_names:
                tool_calls.append(
                    {
                        "id": "call-deploy",
                        "function": {
                            "name": "check_recent_deployments",
                            "arguments": json.dumps({"service": "order-service"}, ensure_ascii=False),
                        },
                    }
                )
            return {"content": "", "tool_calls": tool_calls}
        return {
            "content": json.dumps(
                {
                    "final_answer": "结合知识库和最近部署信号，当前更接近发布回归。",
                    "confidence": 0.84,
                },
                ensure_ascii=False,
            )
        }

    @staticmethod
    def extract_json(content: str):
        return json.loads(content)


class AgentEvalRunnerIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_runner_uses_mock_rag_context_with_disabled_llm(self) -> None:
        dataset = AgentEvalDataset(
            cases=[
                AgentEvalCase(
                    case_id="rag_direct_answer",
                    description="mock rag direct answer eval",
                    request=ConversationCreateRequest(
                        user_id="eval-rag-direct",
                        message="支付服务发布流程是什么？",
                        service="payment-service",
                        environment="prod",
                    ),
                    setup={
                        "llm_mode": "disabled",
                        "mock_rag_context": {
                            "query": "支付服务发布流程是什么？",
                            "query_type": "search",
                            "should_respond_directly": True,
                            "direct_answer": "支付服务发布前需要先完成构建、审批和发布窗口确认。",
                            "hits": [
                                {
                                    "chunk_id": "rag-hit-test",
                                    "title": "支付服务发布手册",
                                    "section": "发布流程 / 第 1 段",
                                    "path": "runbooks/payment-deploy.md",
                                    "category": "runbook",
                                    "score": 0.95,
                                    "snippet": "发布前先确认构建成功、审批通过和发布窗口。",
                                }
                            ],
                            "context": [
                                {
                                    "chunk_id": "rag-hit-test",
                                    "title": "支付服务发布手册",
                                    "section": "发布流程 / 第 1 段",
                                    "path": "runbooks/payment-deploy.md",
                                    "category": "runbook",
                                    "score": 0.95,
                                    "snippet": "发布前先确认构建成功、审批通过和发布窗口。",
                                }
                            ],
                            "citations": ["支付服务发布手册 / 发布流程 / 第 1 段 / runbooks/payment-deploy.md"],
                        },
                    },
                    expect={
                        "status": "completed",
                        "intent": "direct_answer",
                        "message_contains": ["构建", "审批"],
                        "max_tool_calls_used": 0,
                        "min_sources_count": 1,
                    },
                )
            ]
        )
        runner = AgentEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=False,
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertTrue(report.results[0].observation is not None)
        self.assertEqual(report.results[0].observation.intent, "direct_answer")
        self.assertEqual(report.results[0].observation.sources_count, 1)

    async def test_runner_uses_tool_profile_mocks_with_fake_llm(self) -> None:
        dataset = AgentEvalDataset(
            cases=[
                AgentEvalCase(
                    case_id="network_profile",
                    description="fake llm network eval",
                    request=ConversationCreateRequest(
                        user_id="eval-network",
                        message="order service为什么总是超时",
                        service="order-service",
                        environment="prod",
                    ),
                    setup={
                        "tool_profile": {
                            "case_id": "case2",
                            "service": "order-service",
                        }
                    },
                    expect={
                        "status": "completed",
                        "route": "react_tool_first",
                        "required_tools": [
                            "inspect_vpc_connectivity",
                            "inspect_upstream_dependency",
                        ],
                        "conclusion_contains": ["网络链路"],
                        "min_tool_calls_used": 2,
                        "max_tool_calls_used": 2,
                    },
                )
            ]
        )
        runner = AgentEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=True,
            configure_orchestrator=lambda orchestrator: setattr(
                orchestrator.react_supervisor,
                "llm",
                FakeToolCallingLLM(),
            ),
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertTrue(report.results[0].observation is not None)
        self.assertIn("inspect_vpc_connectivity", report.results[0].observation.tool_names)
        self.assertIn("inspect_upstream_dependency", report.results[0].observation.tool_names)

    async def test_runner_forces_initial_probe_when_llm_answers_without_tools(self) -> None:
        dataset = AgentEvalDataset(
            cases=[
                AgentEvalCase(
                    case_id="forced-initial-probe",
                    description="tool-first runtime should collect live evidence before accepting a direct model answer",
                    request=ConversationCreateRequest(
                        user_id="eval-forced-probe",
                        message="order-service 为什么一直 timeout，gateway 偶尔 502",
                        service="order-service",
                        environment="prod",
                    ),
                    setup={"tool_profile": {"case_id": "case2", "service": "order-service"}},
                    expect={
                        "status": "completed",
                        "route": "react_tool_first",
                        "required_any_tools": [
                            "inspect_ingress_route",
                            "inspect_vpc_connectivity",
                            "inspect_upstream_dependency",
                        ],
                        "required_any_tools_min_matches": 2,
                        "min_tool_calls_used": 2,
                        "max_tool_calls_used": 3,
                    },
                )
            ]
        )
        runner = AgentEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=True,
            configure_orchestrator=lambda orchestrator: setattr(
                orchestrator.react_supervisor,
                "llm",
                FakeNoToolAnswerLLM(),
            ),
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertTrue(report.results[0].observation is not None)
        self.assertGreaterEqual(report.results[0].observation.tool_calls_used, 2)

    async def test_runner_uses_db_case_profile_with_fake_llm(self) -> None:
        dataset = AgentEvalDataset(
            cases=[
                AgentEvalCase(
                    case_id="profile-db",
                    description="fake llm case-profile eval",
                    request=ConversationCreateRequest(
                        user_id="eval-profile-db",
                        message="order-service 数据库连接池看起来有问题",
                        service="order-service",
                        environment="prod",
                    ),
                    setup={"tool_profile": {"case_id": "case4_db_pool_saturation", "service": "order-service"}},
                    expect={
                        "status": "completed",
                        "route": "react_tool_first",
                        "required_tools": [
                            "inspect_connection_pool",
                            "inspect_slow_queries",
                        ],
                        "evidence_contains": ["数据库连接池状态为 saturated", "慢查询数量为 24"],
                        "min_tool_calls_used": 2,
                        "max_tool_calls_used": 2,
                    },
                )
            ]
        )
        runner = AgentEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=True,
            configure_orchestrator=lambda orchestrator: setattr(
                orchestrator.react_supervisor,
                "llm",
                FakeDbProfileLLM(),
            ),
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertTrue(report.results[0].observation is not None)
        self.assertEqual(report.results[0].observation.tool_names[:2], ["inspect_connection_pool", "inspect_slow_queries"])

    async def test_runner_supports_similar_cases_by_query_for_retrieval_expansion(self) -> None:
        dataset = AgentEvalDataset(
            cases=[
                AgentEvalCase(
                    case_id="case-recall-expansion",
                    description="retrieval expansion should add case hits from subquery recall",
                    request=ConversationCreateRequest(
                        user_id="eval-case-recall",
                        message="checkout-service 发布后偶发 502",
                        service="checkout-service",
                        environment="prod",
                    ),
                    setup={
                        "llm_mode": "disabled",
                        "mock_tool_responses": {
                            "check_recent_deployments": {
                                "summary": "最近 15 分钟有发布。",
                                "payload": {"service": "checkout-service", "has_recent_deploy": True},
                                "evidence": ["has_recent_deploy=true"],
                            },
                            "check_pipeline_status": {
                                "summary": "流水线失败。",
                                "payload": {"service": "checkout-service", "pipeline_status": "failed"},
                                "evidence": ["pipeline_status=failed"],
                            },
                            "get_change_records": {
                                "summary": "最近变更集中在网关配置。",
                                "payload": {"service": "checkout-service"},
                                "evidence": ["gateway_config_changed=true"],
                            },
                        },
                        "mock_rag_context": {
                            "query": "checkout-service 发布后偶发 502",
                            "query_type": "search",
                            "should_respond_directly": False,
                            "hits": [
                                {
                                    "chunk_id": "rag-hit-base",
                                    "title": "发布回归排查总览",
                                    "section": "基础思路",
                                    "path": "runbooks/release-regression.md",
                                    "category": "runbook",
                                    "score": 0.72,
                                    "snippet": "发布后 5xx 需要先确认最近发布、流水线和配置变更。",
                                }
                            ],
                            "context": [
                                {
                                    "chunk_id": "rag-hit-base",
                                    "title": "发布回归排查总览",
                                    "section": "基础思路",
                                    "path": "runbooks/release-regression.md",
                                    "category": "runbook",
                                    "score": 0.72,
                                    "snippet": "发布后 5xx 需要先确认最近发布、流水线和配置变更。",
                                }
                            ],
                            "citations": ["发布回归排查总览 / 基础思路 / runbooks/release-regression.md"],
                        },
                        "mock_retrieval_expansion": {
                            "subqueries": [
                                {
                                    "query": "checkout-service release regression 502 rollback",
                                    "target": "both",
                                    "reason": "补充发布回归知识和历史案例",
                                    "failure_mode": "deploy_regression",
                                    "root_cause_taxonomy": "release_regression",
                                }
                            ]
                        },
                        "mock_rag_context_by_query": {
                            "checkout-service release regression 502 rollback": {
                                "query": "checkout-service release regression 502 rollback",
                                "query_type": "search",
                                "hits": [
                                    {
                                        "chunk_id": "rag-hit-extra",
                                        "title": "发布后 502 回滚手册",
                                        "section": "回滚判断",
                                        "path": "runbooks/post-release-502.md",
                                        "category": "runbook",
                                        "score": 0.88,
                                        "snippet": "若最近发布与网关配置变更同时出现，应优先核对发布回归。",
                                    }
                                ],
                                "context": [
                                    {
                                        "chunk_id": "rag-hit-extra",
                                        "title": "发布后 502 回滚手册",
                                        "section": "回滚判断",
                                        "path": "runbooks/post-release-502.md",
                                        "category": "runbook",
                                        "score": 0.88,
                                        "snippet": "若最近发布与网关配置变更同时出现，应优先核对发布回归。",
                                    }
                                ],
                                "citations": ["发布后 502 回滚手册 / 回滚判断 / runbooks/post-release-502.md"],
                            }
                        },
                        "mock_similar_cases_by_query": {
                            "checkout-service release regression 502 rollback": [
                                {
                                    "case_id": "case-release-502",
                                    "service": "checkout-service",
                                    "failure_mode": "deploy_regression",
                                    "root_cause_taxonomy": "release_regression",
                                    "summary": "历史上曾因发布后网关配置错误导致 502。",
                                    "root_cause": "发布回归引入错误网关配置",
                                    "final_action": "rollback release",
                                    "recall_source": "case_memory",
                                    "recall_score": 0.92,
                                }
                            ]
                        },
                    },
                    expect={
                        "status": "completed",
                        "route": "react_tool_first",
                        "min_retrieval_subquery_count": 1,
                        "min_added_rag_hits": 1,
                        "min_added_case_hits": 1,
                        "min_sources_count": 2,
                    },
                )
            ]
        )
        runner = AgentEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=False,
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertTrue(report.results[0].observation is not None)
        self.assertEqual(report.results[0].observation.added_case_hits, 1)
        self.assertEqual(report.results[0].observation.added_rag_hits, 1)

    async def test_runner_reports_knowledge_gap_when_recall_stays_empty(self) -> None:
        dataset = AgentEvalDataset(
            cases=[
                AgentEvalCase(
                    case_id="knowledge-gap-empty-recall",
                    description="rules-based retrieval planning should expose missing evidence when both rag and case recall stay empty",
                    request=ConversationCreateRequest(
                        user_id="eval-knowledge-gap",
                        message="order-service 为什么总是 timeout",
                        service="order-service",
                        environment="prod",
                    ),
                    setup={
                        "llm_mode": "disabled",
                        "retrieval_planner_llm_mode": "disabled",
                        "tool_profile": {"case_id": "case2", "service": "order-service"},
                        "mock_rag_context": {
                            "query": "order-service 为什么总是 timeout",
                            "query_type": "search",
                            "should_respond_directly": False,
                            "hits": [],
                            "context": [],
                            "citations": [],
                            "index_info": {"ready": True},
                        },
                        "mock_rag_context_by_query": {
                            "order-service upstream dependency timeout ingress gateway jitter": {
                                "query": "order-service upstream dependency timeout ingress gateway jitter",
                                "query_type": "search",
                                "hits": [],
                                "context": [],
                                "citations": [],
                                "index_info": {"ready": True},
                            }
                        },
                        "mock_similar_cases_by_query": {
                            "order-service upstream dependency timeout ingress gateway jitter": []
                        },
                    },
                    expect={
                        "status": "completed",
                        "route": "react_tool_first",
                        "min_retrieval_subquery_count": 1,
                        "max_retrieval_subquery_count": 1,
                        "retrieval_query_contains": ["upstream dependency timeout ingress gateway jitter"],
                        "missing_evidence_contains": ["上游依赖", "网络链路抖动"],
                        "max_added_rag_hits": 0,
                        "max_added_case_hits": 0,
                        "max_sources_count": 0,
                    },
                )
            ]
        )
        runner = AgentEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=False,
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertTrue(report.results[0].observation is not None)
        self.assertEqual(report.results[0].observation.added_case_hits, 0)
        self.assertEqual(report.results[0].observation.added_rag_hits, 0)
        self.assertIn("order-service upstream dependency timeout ingress gateway jitter", report.results[0].observation.retrieval_queries)
        self.assertTrue(any("网络链路抖动" in item for item in report.results[0].observation.missing_evidence))

    async def test_runner_rules_based_retrieval_planning_can_rewrite_to_better_queries(self) -> None:
        dataset = AgentEvalDataset(
            cases=[
                AgentEvalCase(
                    case_id="rules-based-query-rewrite",
                    description="rules-based retrieval planner should emit focused network and db subqueries that add new hits",
                    request=ConversationCreateRequest(
                        user_id="eval-query-rewrite",
                        message="payment-service 数据库连接池看起来有问题，而且接口一直 timeout",
                        service="payment-service",
                        environment="prod",
                    ),
                    setup={
                        "llm_mode": "disabled",
                        "retrieval_planner_llm_mode": "disabled",
                        "mock_tool_responses": {
                            "inspect_connection_pool": {
                                "summary": "连接池接近打满。",
                                "payload": {"service": "payment-service", "pool_state": "saturated"},
                                "evidence": ["pool_state=saturated"],
                            },
                            "inspect_slow_queries": {
                                "summary": "慢查询显著升高。",
                                "payload": {"service": "payment-service", "slow_query_count": 17},
                                "evidence": ["slow_query_count=17"],
                            },
                        },
                        "mock_rag_context": {
                            "query": "payment-service 数据库连接池看起来有问题，而且接口一直 timeout",
                            "query_type": "search",
                            "should_respond_directly": False,
                            "hits": [
                                {
                                    "chunk_id": "rag-hit-generic",
                                    "title": "服务超时排查总览",
                                    "section": "总览",
                                    "path": "runbooks/timeout-overview.md",
                                    "category": "runbook",
                                    "score": 0.51,
                                    "snippet": "超时问题通常需要结合网络、依赖和数据库共同判断。",
                                }
                            ],
                            "context": [
                                {
                                    "chunk_id": "rag-hit-generic",
                                    "title": "服务超时排查总览",
                                    "section": "总览",
                                    "path": "runbooks/timeout-overview.md",
                                    "category": "runbook",
                                    "score": 0.51,
                                    "snippet": "超时问题通常需要结合网络、依赖和数据库共同判断。",
                                }
                            ],
                            "citations": ["服务超时排查总览 / 总览 / runbooks/timeout-overview.md"],
                            "index_info": {"ready": True},
                        },
                        "mock_rag_context_by_query": {
                            "payment-service upstream dependency timeout ingress gateway jitter": {
                                "query": "payment-service upstream dependency timeout ingress gateway jitter",
                                "query_type": "search",
                                "hits": [],
                                "context": [],
                                "citations": [],
                                "index_info": {"ready": True},
                            },
                            "payment-service db pool saturation slow query timeout": {
                                "query": "payment-service db pool saturation slow query timeout",
                                "query_type": "search",
                                "hits": [
                                    {
                                        "chunk_id": "rag-hit-db",
                                        "title": "数据库超时处置指南",
                                        "section": "连接池与慢查询",
                                        "path": "runbooks/db-timeout.md",
                                        "category": "runbook",
                                        "score": 0.87,
                                        "snippet": "timeout 与连接池饱和、慢查询堆积常同时出现。",
                                    }
                                ],
                                "context": [
                                    {
                                        "chunk_id": "rag-hit-db",
                                        "title": "数据库超时处置指南",
                                        "section": "连接池与慢查询",
                                        "path": "runbooks/db-timeout.md",
                                        "category": "runbook",
                                        "score": 0.87,
                                        "snippet": "timeout 与连接池饱和、慢查询堆积常同时出现。",
                                    }
                                ],
                                "citations": ["数据库超时处置指南 / 连接池与慢查询 / runbooks/db-timeout.md"],
                                "index_info": {"ready": True},
                            },
                        },
                        "mock_similar_cases_by_query": {
                            "payment-service upstream dependency timeout ingress gateway jitter": [],
                            "payment-service db pool saturation slow query timeout": [
                                {
                                    "case_id": "case-db-timeout-1",
                                    "service": "payment-service",
                                    "failure_mode": "db_pool_saturation",
                                    "root_cause_taxonomy": "database_degradation",
                                    "summary": "历史上曾因连接池饱和叠加慢查询导致 timeout。",
                                    "root_cause": "数据库连接池饱和并伴随慢查询放大",
                                    "final_action": "limit db traffic and optimize slow queries",
                                    "recall_source": "case_memory",
                                    "recall_score": 0.95,
                                }
                            ],
                        },
                    },
                    expect={
                        "status": "completed",
                        "route": "react_tool_first",
                        "min_retrieval_subquery_count": 2,
                        "retrieval_query_contains": [
                            "payment-service upstream dependency timeout ingress gateway jitter",
                            "payment-service db pool saturation slow query timeout",
                        ],
                        "missing_evidence_contains": ["上游依赖", "数据库连接池"],
                        "min_added_rag_hits": 1,
                        "min_added_case_hits": 1,
                        "min_sources_count": 2,
                    },
                )
            ]
        )
        runner = AgentEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=False,
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertTrue(report.results[0].observation is not None)
        self.assertGreaterEqual(report.results[0].observation.added_case_hits, 1)
        self.assertGreaterEqual(report.results[0].observation.added_rag_hits, 1)
        self.assertIn("payment-service db pool saturation slow query timeout", report.results[0].observation.retrieval_queries)

    async def test_runner_reports_query_rewrite_without_incremental_gain(self) -> None:
        dataset = AgentEvalDataset(
            cases=[
                AgentEvalCase(
                    case_id="rules-based-query-rewrite-no-gain",
                    description="rules-based retrieval planner should keep the knowledge gap explicit when rewrite brings back only duplicate generic hits",
                    request=ConversationCreateRequest(
                        user_id="eval-query-rewrite-no-gain",
                        message="inventory-service 发布后偶发 502",
                        service="inventory-service",
                        environment="prod",
                    ),
                    setup={
                        "llm_mode": "disabled",
                        "retrieval_planner_llm_mode": "disabled",
                        "mock_tool_responses": {
                            "check_recent_deployments": {
                                "summary": "最近 15 分钟有发布。",
                                "payload": {"service": "inventory-service", "has_recent_deploy": True},
                                "evidence": ["has_recent_deploy=true"],
                            },
                            "check_pipeline_status": {
                                "summary": "流水线无明显失败。",
                                "payload": {"service": "inventory-service", "pipeline_status": "passed"},
                                "evidence": ["pipeline_status=passed"],
                            },
                            "get_change_records": {
                                "summary": "变更记录只显示常规配置同步。",
                                "payload": {"service": "inventory-service"},
                                "evidence": ["config_sync_changed=true"],
                            },
                        },
                        "mock_rag_context": {
                            "query": "inventory-service 发布后偶发 502",
                            "query_type": "search",
                            "should_respond_directly": False,
                            "hits": [
                                {
                                    "chunk_id": "rag-hit-release-generic",
                                    "title": "发布回归排查总览",
                                    "section": "基础思路",
                                    "path": "runbooks/release-regression.md",
                                    "category": "runbook",
                                    "score": 0.72,
                                    "snippet": "发布后 5xx 需要先确认最近发布、流水线和配置变更。",
                                }
                            ],
                            "context": [
                                {
                                    "chunk_id": "rag-hit-release-generic",
                                    "title": "发布回归排查总览",
                                    "section": "基础思路",
                                    "path": "runbooks/release-regression.md",
                                    "category": "runbook",
                                    "score": 0.72,
                                    "snippet": "发布后 5xx 需要先确认最近发布、流水线和配置变更。",
                                }
                            ],
                            "citations": ["发布回归排查总览 / 基础思路 / runbooks/release-regression.md"],
                            "index_info": {"ready": True},
                        },
                        "mock_rag_context_by_query": {
                            "inventory-service release regression deploy rollback 5xx": {
                                "query": "inventory-service release regression deploy rollback 5xx",
                                "query_type": "search",
                                "hits": [
                                    {
                                        "chunk_id": "rag-hit-release-generic",
                                        "title": "发布回归排查总览",
                                        "section": "基础思路",
                                        "path": "runbooks/release-regression.md",
                                        "category": "runbook",
                                        "score": 0.72,
                                        "snippet": "发布后 5xx 需要先确认最近发布、流水线和配置变更。",
                                    }
                                ],
                                "context": [
                                    {
                                        "chunk_id": "rag-hit-release-generic",
                                        "title": "发布回归排查总览",
                                        "section": "基础思路",
                                        "path": "runbooks/release-regression.md",
                                        "category": "runbook",
                                        "score": 0.72,
                                        "snippet": "发布后 5xx 需要先确认最近发布、流水线和配置变更。",
                                    }
                                ],
                                "citations": ["发布回归排查总览 / 基础思路 / runbooks/release-regression.md"],
                                "index_info": {"ready": True},
                            }
                        },
                        "mock_similar_cases_by_query": {
                            "inventory-service release regression deploy rollback 5xx": []
                        },
                    },
                    expect={
                        "status": "completed",
                        "route": "react_tool_first",
                        "min_retrieval_subquery_count": 1,
                        "max_retrieval_subquery_count": 1,
                        "retrieval_query_contains": ["inventory-service release regression deploy rollback 5xx"],
                        "missing_evidence_contains": ["近期发布回归", "配置变更"],
                        "max_added_rag_hits": 0,
                        "max_added_case_hits": 0,
                        "min_sources_count": 1,
                        "max_sources_count": 1,
                    },
                )
            ]
        )
        runner = AgentEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=False,
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertTrue(report.results[0].observation is not None)
        self.assertEqual(report.results[0].observation.added_rag_hits, 0)
        self.assertEqual(report.results[0].observation.added_case_hits, 0)
        self.assertEqual(report.results[0].observation.sources_count, 1)
        self.assertIn("inventory-service release regression deploy rollback 5xx", report.results[0].observation.retrieval_queries)
        self.assertTrue(any("配置变更" in item for item in report.results[0].observation.missing_evidence))

    async def test_runner_reports_partial_query_rewrite_gain(self) -> None:
        dataset = AgentEvalDataset(
            cases=[
                AgentEvalCase(
                    case_id="rules-based-query-rewrite-partial-gain",
                    description="rules-based retrieval planner should preserve partial-gain signal when only one rewritten query adds new knowledge and history",
                    request=ConversationCreateRequest(
                        user_id="eval-query-rewrite-partial-gain",
                        message="payment-service db 连接池告警，而且接口一直 timeout",
                        service="payment-service",
                        environment="prod",
                    ),
                    setup={
                        "llm_mode": "disabled",
                        "retrieval_planner_llm_mode": "disabled",
                        "mock_tool_responses": {
                            "inspect_connection_pool": {
                                "summary": "连接池接近打满。",
                                "payload": {"service": "payment-service", "pool_state": "saturated"},
                                "evidence": ["pool_state=saturated"],
                            },
                            "inspect_slow_queries": {
                                "summary": "慢查询显著升高。",
                                "payload": {"service": "payment-service", "slow_query_count": 19},
                                "evidence": ["slow_query_count=19"],
                            },
                        },
                        "mock_rag_context": {
                            "query": "payment-service db 连接池告警，而且接口一直 timeout",
                            "query_type": "search",
                            "should_respond_directly": False,
                            "hits": [
                                {
                                    "chunk_id": "rag-hit-generic-partial",
                                    "title": "服务超时排查总览",
                                    "section": "总览",
                                    "path": "runbooks/timeout-overview.md",
                                    "category": "runbook",
                                    "score": 0.56,
                                    "snippet": "超时问题通常需要结合网络、依赖和数据库共同判断。",
                                }
                            ],
                            "context": [
                                {
                                    "chunk_id": "rag-hit-generic-partial",
                                    "title": "服务超时排查总览",
                                    "section": "总览",
                                    "path": "runbooks/timeout-overview.md",
                                    "category": "runbook",
                                    "score": 0.56,
                                    "snippet": "超时问题通常需要结合网络、依赖和数据库共同判断。",
                                }
                            ],
                            "citations": ["服务超时排查总览 / 总览 / runbooks/timeout-overview.md"],
                            "index_info": {"ready": True},
                        },
                        "mock_rag_context_by_query": {
                            "payment-service upstream dependency timeout ingress gateway jitter": {
                                "query": "payment-service upstream dependency timeout ingress gateway jitter",
                                "query_type": "search",
                                "hits": [
                                    {
                                        "chunk_id": "rag-hit-generic-partial",
                                        "title": "服务超时排查总览",
                                        "section": "总览",
                                        "path": "runbooks/timeout-overview.md",
                                        "category": "runbook",
                                        "score": 0.56,
                                        "snippet": "超时问题通常需要结合网络、依赖和数据库共同判断。",
                                    }
                                ],
                                "context": [
                                    {
                                        "chunk_id": "rag-hit-generic-partial",
                                        "title": "服务超时排查总览",
                                        "section": "总览",
                                        "path": "runbooks/timeout-overview.md",
                                        "category": "runbook",
                                        "score": 0.56,
                                        "snippet": "超时问题通常需要结合网络、依赖和数据库共同判断。",
                                    }
                                ],
                                "citations": ["服务超时排查总览 / 总览 / runbooks/timeout-overview.md"],
                                "index_info": {"ready": True},
                            },
                            "payment-service db pool saturation slow query timeout": {
                                "query": "payment-service db pool saturation slow query timeout",
                                "query_type": "search",
                                "hits": [
                                    {
                                        "chunk_id": "rag-hit-db-partial",
                                        "title": "数据库超时处置指南",
                                        "section": "连接池与慢查询",
                                        "path": "runbooks/db-timeout.md",
                                        "category": "runbook",
                                        "score": 0.89,
                                        "snippet": "timeout 与连接池饱和、慢查询堆积常同时出现。",
                                    }
                                ],
                                "context": [
                                    {
                                        "chunk_id": "rag-hit-db-partial",
                                        "title": "数据库超时处置指南",
                                        "section": "连接池与慢查询",
                                        "path": "runbooks/db-timeout.md",
                                        "category": "runbook",
                                        "score": 0.89,
                                        "snippet": "timeout 与连接池饱和、慢查询堆积常同时出现。",
                                    }
                                ],
                                "citations": ["数据库超时处置指南 / 连接池与慢查询 / runbooks/db-timeout.md"],
                                "index_info": {"ready": True},
                            },
                        },
                        "mock_similar_cases_by_query": {
                            "payment-service upstream dependency timeout ingress gateway jitter": [],
                            "payment-service db pool saturation slow query timeout": [
                                {
                                    "case_id": "case-db-timeout-partial-gain",
                                    "service": "payment-service",
                                    "failure_mode": "db_pool_saturation",
                                    "root_cause_taxonomy": "database_degradation",
                                    "summary": "历史上曾因连接池饱和叠加慢查询导致 timeout。",
                                    "root_cause": "数据库连接池饱和并伴随慢查询放大",
                                    "final_action": "limit db traffic and optimize slow queries",
                                    "recall_source": "case_memory",
                                    "recall_score": 0.93,
                                }
                            ],
                        },
                    },
                    expect={
                        "status": "completed",
                        "route": "react_tool_first",
                        "min_retrieval_subquery_count": 2,
                        "max_retrieval_subquery_count": 2,
                        "retrieval_query_contains": [
                            "payment-service upstream dependency timeout ingress gateway jitter",
                            "payment-service db pool saturation slow query timeout",
                        ],
                        "missing_evidence_contains": ["上游依赖", "数据库连接池"],
                        "min_added_rag_hits": 1,
                        "max_added_rag_hits": 1,
                        "min_added_case_hits": 1,
                        "max_added_case_hits": 1,
                        "retrieval_query_metrics": [
                            {
                                "query_contains": "payment-service upstream dependency timeout ingress gateway jitter",
                                "added_rag_hits": 0,
                                "added_case_hits": 0,
                                "root_cause_taxonomy": "network_path_instability",
                                "matches_primary_root_cause_taxonomy": False,
                            },
                            {
                                "query_contains": "payment-service db pool saturation slow query timeout",
                                "added_rag_hits": 1,
                                "added_case_hits": 1,
                                "root_cause_taxonomy": "database_degradation",
                                "matches_primary_root_cause_taxonomy": True,
                            },
                        ],
                        "min_sources_count": 2,
                    },
                )
            ]
        )
        runner = AgentEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=False,
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertTrue(report.results[0].observation is not None)
        self.assertEqual(report.results[0].observation.retrieval_subquery_count, 2)
        self.assertEqual(report.results[0].observation.added_case_hits, 1)
        self.assertEqual(report.results[0].observation.added_rag_hits, 1)
        self.assertIn("payment-service upstream dependency timeout ingress gateway jitter", report.results[0].observation.retrieval_queries)
        self.assertIn("payment-service db pool saturation slow query timeout", report.results[0].observation.retrieval_queries)
        self.assertEqual(report.results[0].observation.primary_root_cause_taxonomy, "database_degradation")
        query_metrics = {
            item.query: (
                item.added_rag_hits,
                item.added_case_hits,
                item.root_cause_taxonomy,
                item.matches_primary_root_cause_taxonomy,
            )
            for item in report.results[0].observation.retrieval_query_metrics
        }
        self.assertEqual(
            query_metrics["payment-service upstream dependency timeout ingress gateway jitter"],
            (0, 0, "network_path_instability", False),
        )
        self.assertEqual(
            query_metrics["payment-service db pool saturation slow query timeout"],
            (1, 1, "database_degradation", True),
        )

    async def test_runner_exposes_search_knowledge_base_when_rag_is_sparse(self) -> None:
        dataset = AgentEvalDataset(
            cases=[
                AgentEvalCase(
                    case_id="knowledge-tool-helper",
                    description="fake llm should see and call search_knowledge_base before live checks",
                    request=ConversationCreateRequest(
                        user_id="eval-knowledge-tool",
                        message="order-service 灰度发布后一直 502，你先查下有没有相关手册或已知回归模式，再结合实时信号判断",
                        service="order-service",
                        environment="prod",
                    ),
                    setup={
                        "tool_profile": {"case_id": "case8_canary_release_regression", "service": "order-service"},
                        "mock_rag_context": {
                            "query": "order-service 灰度发布后一直 502，你先查下有没有相关手册或已知回归模式，再结合实时信号判断",
                            "query_type": "search",
                            "should_respond_directly": False,
                            "hits": [
                                {
                                    "chunk_id": "tool-hit-1",
                                    "title": "发布回归处置手册",
                                    "section": "发布后 502",
                                    "path": "runbooks/release-502.md",
                                    "category": "runbook",
                                    "score": 0.91,
                                    "snippet": "发布后持续 502 时，应先核对最近发布与已知回归模式。",
                                }
                            ],
                            "context": [
                                {
                                    "chunk_id": "tool-hit-1",
                                    "title": "发布回归处置手册",
                                    "section": "发布后 502",
                                    "path": "runbooks/release-502.md",
                                    "category": "runbook",
                                    "score": 0.91,
                                    "snippet": "发布后持续 502 时，应先核对最近发布与已知回归模式。",
                                }
                            ],
                            "citations": ["发布回归处置手册 / 发布后 502 / runbooks/release-502.md"],
                            "index_info": {"ready": True},
                        },
                    },
                    expect={
                        "status": "completed",
                        "route": "react_tool_first",
                        "required_tools": ["search_knowledge_base"],
                        "first_any_tools": ["search_knowledge_base"],
                        "required_any_tools": ["check_recent_deployments"],
                        "evidence_contains": ["知识库命中：发布回归处置手册", "故障窗口附近存在发布"],
                        "min_sources_count": 1,
                        "min_tool_calls_used": 2,
                        "max_tool_calls_used": 2,
                    },
                )
            ]
        )
        runner = AgentEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=True,
            configure_orchestrator=lambda orchestrator: setattr(
                orchestrator.react_supervisor,
                "llm",
                FakeKnowledgeFirstLLM(),
            ),
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertTrue(report.results[0].observation is not None)
        self.assertIn("search_knowledge_base", report.results[0].observation.tool_names)
        self.assertGreaterEqual(report.results[0].observation.sources_count, 1)

    async def test_session_flow_runner_supports_feedback_resume_with_disabled_llm(self) -> None:
        dataset = SessionFlowEvalDataset(
            cases=[
                SessionFlowEvalCase(
                    case_id="feedback_flow",
                    description="feedback flow eval",
                    setup={"llm_mode": "disabled"},
                    steps=[
                        SessionFlowEvalStep(
                            step_id="start",
                            action="start_conversation",
                            request={
                                "user_id": "eval-feedback",
                                "message": "checkout-service 需要一个低风险自动修复动作",
                                "service": "checkout-service",
                                "environment": "prod",
                            },
                            expect={
                                "response_status": "completed",
                                "pending_interrupt_type": "feedback",
                                "case_exists": True,
                            },
                        ),
                        SessionFlowEvalStep(
                            step_id="feedback",
                            action="resume_conversation",
                            request={
                                "answer_payload": {
                                    "human_verified": True,
                                    "actual_root_cause_hypothesis": "H-OBSERVE",
                                    "hypothesis_accuracy": {"H-OBSERVE": 1.0},
                                }
                            },
                            expect={
                                "response_status": "completed",
                                "human_verified": True,
                                "actual_root_cause_contains": ["H-OBSERVE"],
                                "new_system_event_types": ["feedback.received"],
                            },
                        ),
                    ],
                )
            ]
        )
        runner = SessionFlowEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=True,
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertEqual(report.total_steps, 2)
        self.assertEqual(report.passed_steps, 2)

    async def test_session_flow_runner_supports_topic_shift_supersede(self) -> None:
        dataset = SessionFlowEvalDataset(
            cases=[
                SessionFlowEvalCase(
                    case_id="topic_shift_flow",
                    description="approval superseded by topic shift",
                    setup={"llm_mode": "disabled"},
                    steps=[
                        SessionFlowEvalStep(
                            step_id="start",
                            action="start_conversation",
                            request={
                                "user_id": "eval-topic-shift",
                                "message": "checkout-service 发布失败，需要排查最近变更",
                                "service": "checkout-service",
                                "environment": "prod",
                            },
                            expect={
                                "response_status": "awaiting_approval",
                                "pending_interrupt_type": "approval",
                            },
                        ),
                        SessionFlowEvalStep(
                            step_id="shift",
                            action="post_message",
                            request={
                                "message": "现在更像数据库连接池耗尽和慢查询，不做回滚了",
                            },
                            expect={
                                "response_status": "completed",
                                "pending_interrupt_type": "",
                                "new_system_event_types": ["approval.superseded", "interrupt.superseded"],
                                "new_approval_event_types": ["cancelled"],
                                "min_current_intent_history_length": 1,
                            },
                        ),
                    ],
                )
            ]
        )
        runner = SessionFlowEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=True,
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)

    async def test_session_flow_runner_supports_approval_expire(self) -> None:
        dataset = SessionFlowEvalDataset(
            cases=[
                SessionFlowEvalCase(
                    case_id="approval_expire_flow",
                    description="pending approval expires into terminal state",
                    setup={"llm_mode": "disabled"},
                    steps=[
                        SessionFlowEvalStep(
                            step_id="start",
                            action="start_conversation",
                            request={
                                "user_id": "eval-approval-expire",
                                "message": "checkout-service 发布失败，需要排查最近变更",
                                "service": "checkout-service",
                                "environment": "prod",
                            },
                            expect={
                                "response_status": "awaiting_approval",
                                "pending_interrupt_type": "approval",
                            },
                        ),
                        SessionFlowEvalStep(
                            step_id="expire",
                            action="expire_approval",
                            request={
                                "actor_id": "system-timeout",
                                "comment": "审批超时",
                            },
                            expect={
                                "response_status": "completed",
                                "session_status": "completed",
                                "pending_interrupt_type": "",
                                "message_contains": ["审批已超时"],
                                "new_approval_event_types": ["expired", "resumed"],
                            },
                        ),
                    ],
                )
            ]
        )
        runner = SessionFlowEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=True,
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)

    async def test_session_flow_runner_supports_execution_failure_recovery_lookup(self) -> None:
        dataset = SessionFlowEvalDataset(
            cases=[
                SessionFlowEvalCase(
                    case_id="execution_failure_recovery_flow",
                    description="approval tamper triggers execution safety failure and recovery lookup",
                    setup={"llm_mode": "disabled"},
                    steps=[
                        SessionFlowEvalStep(
                            step_id="start",
                            action="start_conversation",
                            request={
                                "user_id": "eval-execution-recovery",
                                "message": "checkout-service 发布失败，需要排查最近变更",
                                "service": "checkout-service",
                                "environment": "prod",
                            },
                            expect={
                                "response_status": "awaiting_approval",
                                "pending_interrupt_type": "approval",
                            },
                        ),
                        SessionFlowEvalStep(
                            step_id="tamper",
                            action="tamper_latest_approval",
                            request={
                                "proposal_patch": {
                                    "params": {
                                        "service": "tampered-service",
                                    }
                                }
                            },
                            expect={},
                        ),
                        SessionFlowEvalStep(
                            step_id="approve",
                            action="resume_conversation",
                            request={
                                "approved": True,
                                "approver_id": "ops-admin",
                                "comment": "继续执行被篡改的审批单",
                            },
                            expect={
                                "response_status": "failed",
                                "session_status": "failed",
                                "message_contains": ["snapshot mismatch"],
                                "recovery_action": "manual_intervention",
                                "execution_plan_status": "failed",
                                "failed_step_exists": True,
                                "resume_from_step_exists": True,
                                "recovery_reason_contains": ["执行前校验失败"],
                            },
                        ),
                        SessionFlowEvalStep(
                            step_id="recovery",
                            action="get_execution_recovery",
                            request={},
                            expect={
                                "response_status": "completed",
                                "session_status": "failed",
                                "recovery_action": "manual_intervention",
                                "execution_plan_status": "failed",
                                "failed_step_exists": True,
                                "resume_from_step_exists": True,
                                "recovery_reason_contains": ["执行前校验失败"],
                            },
                        ),
                    ],
                )
            ]
        )
        runner = SessionFlowEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=True,
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertEqual(report.total_steps, 4)
        self.assertEqual(report.passed_steps, 4)

    async def test_session_flow_runner_supports_manual_intervention_recovery_lookup(self) -> None:
        dataset = SessionFlowEvalDataset(
            cases=[
                SessionFlowEvalCase(
                    case_id="execution_retry_recovery_flow",
                    description="approved action tool failure should produce manual_intervention recovery",
                    setup={"llm_mode": "disabled"},
                    steps=[
                        SessionFlowEvalStep(
                            step_id="start",
                            action="start_conversation",
                            request={
                                "user_id": "eval-execution-retry",
                                "message": "checkout-service 发布失败，需要排查最近变更",
                                "service": "checkout-service",
                                "environment": "prod",
                            },
                            expect={
                                "response_status": "awaiting_approval",
                                "pending_interrupt_type": "approval",
                            },
                        ),
                        SessionFlowEvalStep(
                            step_id="approve",
                            action="resume_conversation",
                            runtime_patch={
                                "execution_error": "rollback tool failed",
                            },
                            request={
                                "approved": True,
                                "approver_id": "ops-admin",
                                "comment": "执行失败人工介入场景",
                            },
                            expect={
                                "response_status": "failed",
                                "session_status": "failed",
                                "message_contains": ["rollback tool failed"],
                                "recovery_action": "manual_intervention",
                                "execution_plan_status": "failed",
                                "failed_step_exists": True,
                                "resume_from_step_exists": True,
                                "recovery_reason_contains": ["主动作执行失败"],
                                "recovery_hint_contains": ["人工", "retry_policy"],
                            },
                        ),
                        SessionFlowEvalStep(
                            step_id="recovery",
                            action="get_execution_recovery",
                            request={},
                            expect={
                                "response_status": "completed",
                                "session_status": "failed",
                                "recovery_action": "manual_intervention",
                                "execution_plan_status": "failed",
                                "failed_step_exists": True,
                                "resume_from_step_exists": True,
                                "recovery_reason_contains": ["主动作执行失败"],
                                "recovery_hint_contains": ["人工", "retry_policy"],
                            },
                        ),
                    ],
                )
            ]
        )
        runner = SessionFlowEvalRunner(
            Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
            profiles_path=MOCK_PROFILES_PATH,
            require_llm_enabled=True,
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertEqual(report.total_steps, 3)
        self.assertEqual(report.passed_steps, 3)

    async def test_inline_mock_response_overrides_case_profile(self) -> None:
        tool = InspectConnectionPoolTool()
        result = await tool.run(
            TaskEnvelope(
                task_id="t1",
                ticket_id="ticket-1",
                goal="test",
                shared_context={
                    "service": "order-service",
                    "mock_case": "case4_db_pool_saturation",
                    "mock_tool_responses": {
                        "inspect_connection_pool": {
                            "summary": "forced override",
                            "payload": {
                                "service": "order-service",
                                "pool_state": "healthy",
                                "active_connections": 12,
                                "max_connections": 120,
                            },
                            "evidence": ["forced override evidence"],
                        }
                    },
                },
            )
        )

        self.assertEqual(result.summary, "")
        self.assertEqual(result.payload["pool_state"], "healthy")
        self.assertEqual(result.evidence, [])


class SessionFlowDatasetLoadTest(unittest.TestCase):
    def test_tool_mock_eval_dataset_uses_full_mock_world_profiles(self) -> None:
        dataset = load_agent_eval_dataset(PROJECT_ROOT / "data" / "evals" / "tool_mock_cases.json")

        self.assertEqual(len(dataset.cases), 15)
        self.assertTrue(all(case.setup.tool_profile is not None for case in dataset.cases))
        self.assertTrue(all(not case.setup.mock_tool_responses for case in dataset.cases))
        self.assertIn(
            "case8_canary_release_regression",
            {case.setup.tool_profile.case_id for case in dataset.cases if case.setup.tool_profile},
        )
        network_case = next(case for case in dataset.cases if case.case_id == "network_profile_prefers_network_tools")
        self.assertEqual(network_case.expect.status, "completed")
        self.assertEqual(network_case.expect.route, "react_tool_first")
        self.assertEqual(
            network_case.expect.required_any_tools,
            ["inspect_ingress_route", "inspect_vpc_connectivity", "inspect_upstream_dependency"],
        )

    def test_world_eval_dataset_uses_mock_case_profiles(self) -> None:
        dataset = load_agent_eval_dataset(PROJECT_ROOT / "data" / "evals" / "world_cases.json")

        self.assertEqual(len(dataset.cases), 7)
        self.assertTrue(all(case.setup.tool_profile is not None for case in dataset.cases))
        self.assertTrue(all(not case.setup.mock_tool_responses for case in dataset.cases))
        self.assertIn(
            "case6_cpu_thread_saturation",
            {case.setup.tool_profile.case_id for case in dataset.cases if case.setup.tool_profile},
        )
        quota_case = next(case for case in dataset.cases if case.case_id == "world_quota_exhaustion_single_domain")
        self.assertEqual(quota_case.expect.required_tools, ["get_quota_status"])
        self.assertEqual(quota_case.expect.first_any_tools, ["get_quota_status"])
        db_noise_case = next(case for case in dataset.cases if case.case_id == "world_timeout_db_pool_with_network_noise")
        self.assertEqual(
            db_noise_case.expect.required_any_tools,
            ["inspect_connection_pool", "inspect_slow_queries", "inspect_db_instance_health"],
        )
        self.assertEqual(db_noise_case.expect.expanded_domains, ["db"])

    def test_rag_eval_keeps_retrieval_mocks_but_uses_world_profiles_for_diagnosis(self) -> None:
        dataset = load_agent_eval_dataset(PROJECT_ROOT / "data" / "evals" / "rag_cases.json")

        diagnostic_cases = [case for case in dataset.cases if case.expect.route == "react_tool_first"]
        self.assertTrue(diagnostic_cases)
        self.assertTrue(all(case.setup.tool_profile is not None for case in diagnostic_cases))
        self.assertTrue(all(not case.setup.mock_tool_responses for case in dataset.cases))

    def test_live_session_flow_eval_uses_world_profiles_without_inline_overrides(self) -> None:
        dataset = load_session_flow_eval_dataset(PROJECT_ROOT / "data" / "evals" / "session_flow_live_cases.json")

        self.assertEqual(len(dataset.cases), 4)
        self.assertTrue(all(case.setup.tool_profile is not None for case in dataset.cases))
        self.assertTrue(all(not case.setup.mock_tool_responses for case in dataset.cases))

    def test_load_rag_eval_dataset_from_file(self) -> None:
        dataset = load_agent_eval_dataset(PROJECT_ROOT / "data" / "evals" / "rag_cases.json")

        self.assertGreaterEqual(dataset.schema_version, 1)
        self.assertEqual(len(dataset.cases), 10)
        self.assertEqual(dataset.cases[0].request.message, "支付服务发布流程是什么？")
        self.assertEqual(dataset.gate.min_pass_rate, 1.0)
        self.assertEqual(dataset.gate.max_avg_tool_calls_used, 4.5)

    def test_load_session_flow_dataset_from_file(self) -> None:
        dataset = load_session_flow_eval_dataset(PROJECT_ROOT / "data" / "evals" / "session_flow_cases.json")

        self.assertGreaterEqual(dataset.schema_version, 1)
        self.assertEqual(len(dataset.cases), 9)
        self.assertEqual(dataset.cases[0].steps[0].action, "start_conversation")
        self.assertEqual(dataset.cases[0].setup.llm_mode, "disabled")
        self.assertEqual(dataset.gate.min_pass_rate, 1.0)
        self.assertEqual(dataset.gate.min_step_pass_rate, 1.0)

    def test_load_live_session_flow_dataset_from_file(self) -> None:
        dataset = load_session_flow_eval_dataset(PROJECT_ROOT / "data" / "evals" / "session_flow_live_cases.json")

        self.assertGreaterEqual(dataset.schema_version, 1)
        self.assertEqual(len(dataset.cases), 4)
        self.assertEqual(dataset.cases[0].steps[0].action, "start_conversation")
        self.assertEqual(dataset.cases[0].setup.llm_mode, "live")
        self.assertEqual(dataset.gate.min_pass_rate, 1.0)
        self.assertEqual(dataset.gate.min_step_pass_rate, 1.0)


class CandidateExpansionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.supervisor = ReactSupervisor(
            object(),
            settings=Settings(
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                rag_enabled=False,
            ),
        )

    def test_network_weak_evidence_expands_to_db_domain(self) -> None:
        request = ConversationCreateRequest(
            user_id="u-db-expand",
            message="order service为什么总是 timeout",
            service="order-service",
            environment="prod",
        )
        observations = [
            {
                "tool_name": "inspect_vpc_connectivity",
                "result": {"payload": {"connectivity_status": "healthy"}, "evidence": ["east-west network healthy"]},
            },
            {
                "tool_name": "inspect_upstream_dependency",
                "result": {"payload": {"dependency_status": "healthy", "timeout_ratio": 0.0}, "evidence": ["upstream healthy"]},
            },
        ]

        candidate_domains = self.supervisor._resolve_candidate_domains(
            request=request,
            context_snapshot=ContextSnapshot(matched_tool_domains=["network"]),
            observations=observations,
        )
        candidate_tools = self.supervisor._select_candidate_tool_names(
            observations=observations,
            candidate_domain_plan=candidate_domains,
        )

        self.assertIn("db", candidate_domains["expanded_domains"])
        self.assertIn("inspect_connection_pool", candidate_tools)
        self.assertIn("inspect_slow_queries", candidate_tools)

    def test_sparse_rag_context_exposes_search_knowledge_base(self) -> None:
        request = ConversationCreateRequest(
            user_id="u-knowledge-helper",
            message="checkout-service 发布后一直 502，先看下有没有已知回归模式",
            service="checkout-service",
            environment="prod",
        )
        context_snapshot = ContextSnapshot(
            matched_tool_domains=["cicd", "network"],
            rag_context=RAGContextBundle(
                query=request.message,
                query_type="search",
                should_respond_directly=False,
            ),
        )

        candidate_domains = self.supervisor._resolve_candidate_domains(
            request=request,
            context_snapshot=context_snapshot,
            observations=[],
        )
        candidate_tools = self.supervisor._select_candidate_tool_names(
            observations=[],
            candidate_domain_plan=candidate_domains,
            context_snapshot=context_snapshot,
        )

        self.assertIn("search_knowledge_base", candidate_tools[:2])

    def test_initial_iteration_does_not_expose_similar_case_tool_without_extra_context(self) -> None:
        request = ConversationCreateRequest(
            user_id="u-history-hint-initial",
            message="checkout-service 出问题了，帮我排查一下",
            service="checkout-service",
            environment="prod",
        )
        context_snapshot = ContextSnapshot(
            request=request.model_dump(),
            matched_tool_domains=["k8s", "network"],
            case_recall={"auto_prefetch_enabled": False, "prefetch_reason": "query_too_generic"},
        )

        candidate_domains = self.supervisor._resolve_candidate_domains(
            request=request,
            context_snapshot=context_snapshot,
            observations=[],
        )
        candidate_tools = self.supervisor._select_candidate_tool_names(
            observations=[],
            candidate_domain_plan=candidate_domains,
            context_snapshot=context_snapshot,
        )

        self.assertNotIn("search_similar_incidents", candidate_tools[:3])

    def test_followup_live_evidence_exposes_similar_case_tool(self) -> None:
        request = ConversationCreateRequest(
            user_id="u-history-hint-followup",
            message="checkout-service 出问题了，帮我排查一下",
            service="checkout-service",
            environment="prod",
        )
        context_snapshot = ContextSnapshot(
            request=request.model_dump(),
            matched_tool_domains=["network"],
            case_recall={"auto_prefetch_enabled": False, "prefetch_reason": "query_too_generic"},
        )
        observations = [
            {
                "tool_name": "inspect_upstream_dependency",
                "result": {
                    "payload": {"dependency_status": "degraded", "timeout_ratio": 0.42},
                    "evidence": ["upstream timeout ratio elevated"],
                },
            }
        ]

        candidate_domains = self.supervisor._resolve_candidate_domains(
            request=request,
            context_snapshot=context_snapshot,
            observations=observations,
        )
        candidate_tools = self.supervisor._select_candidate_tool_names(
            observations=observations,
            candidate_domain_plan=candidate_domains,
            context_snapshot=context_snapshot,
        )

        self.assertIn("search_similar_incidents", candidate_tools)

    def test_similar_case_tool_observation_merges_back_into_context_snapshot(self) -> None:
        context_snapshot = ContextSnapshot(
            request={
                "message": "payment-service timeout 并出现 502",
                "service": "payment-service",
            },
            matched_tool_domains=["network"],
            case_recall={"auto_prefetch_enabled": False, "prefetch_reason": "query_too_generic"},
        )
        incident_state = SimpleNamespace(context_snapshot=context_snapshot)
        next_state = {"context_snapshot": context_snapshot}

        self.supervisor._merge_search_similar_case_observation(
            next_state=next_state,
            incident_state=incident_state,
            observation={
                "tool_name": "search_similar_incidents",
                "result": {
                    "payload": {
                        "query": "payment-service timeout 502 upstream",
                        "cases": [
                            {
                                "case_id": "case-payment-timeout",
                                "service": "payment-service",
                                "failure_mode": "dependency_timeout",
                                "root_cause_taxonomy": "network_path_instability",
                                "signal_pattern": "timeout+gateway_unhealthy",
                                "action_pattern": "observe_service",
                                "symptom": "payment timeout and 502",
                                "root_cause": "upstream dependency unstable",
                                "final_action": "observe_service",
                                "summary": "历史上曾因上游依赖抖动导致 payment timeout",
                                "recall_source": "semantic_hybrid",
                                "score": 0.81,
                            }
                        ],
                    }
                },
            },
        )

        merged_snapshot = next_state["context_snapshot"]
        self.assertEqual(len(merged_snapshot.similar_cases), 1)
        self.assertEqual(merged_snapshot.similar_cases[0].case_id, "case-payment-timeout")
        self.assertEqual(merged_snapshot.case_recall["tool_search_count"], 1)
        self.assertEqual(merged_snapshot.case_recall["tool_added_case_hits"], 1)

    def test_similar_case_tool_empty_observation_records_search_attempt(self) -> None:
        context_snapshot = ContextSnapshot(
            request={
                "message": "payment-service timeout 并出现 502",
                "service": "payment-service",
            },
            matched_tool_domains=["network"],
            case_recall={"auto_prefetch_enabled": False, "prefetch_reason": "query_too_generic"},
        )
        incident_state = SimpleNamespace(context_snapshot=context_snapshot)
        next_state = {"context_snapshot": context_snapshot}

        self.supervisor._merge_search_similar_case_observation(
            next_state=next_state,
            incident_state=incident_state,
            observation={
                "tool_name": "search_similar_incidents",
                "result": {
                    "status": "completed",
                    "payload": {
                        "query": "payment-service timeout 502 upstream",
                        "cases": [],
                        "index_info": {},
                    },
                },
            },
        )

        merged_snapshot = next_state["context_snapshot"]
        self.assertEqual(merged_snapshot.similar_cases, [])
        self.assertEqual(merged_snapshot.case_recall["tool_search_count"], 1)
        self.assertEqual(merged_snapshot.case_recall["last_tool_hit_count"], 0)
        self.assertEqual(merged_snapshot.case_recall["last_tool_status"], "completed")

    def test_similar_case_tool_failure_records_failure_metadata(self) -> None:
        context_snapshot = ContextSnapshot(
            request={"message": "payment-service timeout", "service": "payment-service"},
            matched_tool_domains=["network"],
            case_recall={"auto_prefetch_enabled": False, "prefetch_reason": "query_too_generic"},
        )
        incident_state = SimpleNamespace(context_snapshot=context_snapshot)
        next_state = {"context_snapshot": context_snapshot}

        self.supervisor._merge_search_similar_case_observation(
            next_state=next_state,
            incident_state=incident_state,
            observation={
                "tool_name": "search_similar_incidents",
                "result": {
                    "status": "completed",
                    "payload": {
                        "query": "payment-service timeout",
                        "cases": [],
                        "index_info": {"error": "case_memory_search_failed"},
                    },
                },
            },
        )

        merged_snapshot = next_state["context_snapshot"]
        self.assertEqual(merged_snapshot.case_recall["tool_search_count"], 1)
        self.assertEqual(merged_snapshot.case_recall["tool_failures"][0]["error"], "case_memory_search_failed")

    def test_k8s_weak_evidence_expands_to_cicd_domain(self) -> None:
        request = ConversationCreateRequest(
            user_id="u-cicd-expand",
            message="checkout-service pod 看着正常，但服务从刚才开始一直报错",
            service="checkout-service",
            environment="prod",
        )
        observations = [
            {
                "tool_name": "check_pod_status",
                "result": {"payload": {"ready_replicas": 2, "desired_replicas": 2}, "evidence": ["ready 2/2"]},
            },
            {
                "tool_name": "inspect_pod_logs",
                "result": {"payload": {"error_pattern": "none"}, "evidence": ["log pattern normal"]},
            },
        ]

        candidate_domains = self.supervisor._resolve_candidate_domains(
            request=request,
            context_snapshot=ContextSnapshot(matched_tool_domains=["k8s"]),
            observations=observations,
        )
        candidate_tools = self.supervisor._select_candidate_tool_names(
            observations=observations,
            candidate_domain_plan=candidate_domains,
        )

        self.assertIn("cicd", candidate_domains["expanded_domains"])
        self.assertIn("check_recent_deployments", candidate_tools)

    def test_explicit_primary_domain_prefers_adjacency_before_matched_noise(self) -> None:
        request = ConversationCreateRequest(
            user_id="u-noisy-match",
            message="order service为什么总是 timeout，接口偶尔卡死",
            service="order-service",
            environment="prod",
        )
        observations = [
            {
                "tool_name": "inspect_ingress_route",
                "result": {"payload": {"route_status": "healthy"}, "evidence": ["route healthy"]},
            },
            {
                "tool_name": "inspect_vpc_connectivity",
                "result": {"payload": {"connectivity_status": "healthy"}, "evidence": ["east-west network healthy"]},
            },
        ]

        candidate_domains = self.supervisor._resolve_candidate_domains(
            request=request,
            context_snapshot=ContextSnapshot(matched_tool_domains=["network", "k8s", "db"]),
            observations=observations,
        )

        self.assertEqual(candidate_domains["expanded_domains"][:2], ["db", "cicd"])
