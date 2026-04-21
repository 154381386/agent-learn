from __future__ import annotations

import json
import unittest
from pathlib import Path

from it_ticket_agent.evals import (
    AgentEvalCase,
    AgentEvalDataset,
    AgentEvalExpectation,
    AgentEvalCaseResult,
    AgentEvalObservation,
    AgentEvalRunner,
    ToolProfileRef,
    build_eval_report,
    extract_eval_observation,
    resolve_tool_profile_mock_responses,
    score_agent_eval_case,
)
from it_ticket_agent.runtime.contracts import TaskEnvelope
from it_ticket_agent.runtime.react_supervisor import ReactSupervisor
from it_ticket_agent.schemas import ConversationCreateRequest
from it_ticket_agent.settings import Settings
from it_ticket_agent.state.models import ContextSnapshot
from it_ticket_agent.tools.db import InspectConnectionPoolTool


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


class FakeWorldStateLLM:
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
                            "arguments": json.dumps({"service": "payment-service"}, ensure_ascii=False),
                        },
                    },
                    {
                        "id": "call-db-slow",
                        "function": {
                            "name": "inspect_slow_queries",
                            "arguments": json.dumps({"service": "payment-service"}, ensure_ascii=False),
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


class AgentEvalRunnerIntegrationTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_runner_uses_world_state_with_fake_llm(self) -> None:
        dataset = AgentEvalDataset(
            cases=[
                AgentEvalCase(
                    case_id="world-db",
                    description="fake llm world eval",
                    request=ConversationCreateRequest(
                        user_id="eval-world-db",
                        message="payment-service 数据库连接池看起来有问题",
                        service="payment-service",
                        environment="prod",
                    ),
                    setup={
                        "world_state": {
                            "service": "payment-service",
                            "signals": {
                                "db": {
                                    "pool_state": "saturated",
                                    "active_connections": 118,
                                    "max_connections": 120,
                                    "slow_query_count": 12,
                                    "max_latency_ms": 4200,
                                    "db_health": "degraded",
                                }
                            },
                        }
                    },
                    expect={
                        "status": "completed",
                        "route": "react_tool_first",
                        "required_tools": [
                            "inspect_connection_pool",
                            "inspect_slow_queries",
                        ],
                        "evidence_contains": ["pool_state=saturated", "slow_query_count=12"],
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
                FakeWorldStateLLM(),
            ),
        )

        report = await runner.run_dataset(dataset)

        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(report.errored_cases, 0)
        self.assertTrue(report.results[0].observation is not None)
        self.assertEqual(report.results[0].observation.tool_names[:2], ["inspect_connection_pool", "inspect_slow_queries"])

    async def test_inline_mock_response_overrides_world_state(self) -> None:
        tool = InspectConnectionPoolTool()
        result = await tool.run(
            TaskEnvelope(
                task_id="t1",
                ticket_id="ticket-1",
                goal="test",
                shared_context={
                    "service": "payment-service",
                    "mock_world_state": {
                        "service": "payment-service",
                        "signals": {
                            "db": {
                                "pool_state": "saturated",
                                "active_connections": 118,
                                "max_connections": 120,
                            }
                        },
                    },
                    "mock_tool_responses": {
                        "inspect_connection_pool": {
                            "summary": "forced override",
                            "payload": {
                                "service": "payment-service",
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

        self.assertEqual(result.summary, "forced override")
        self.assertEqual(result.payload["pool_state"], "healthy")
        self.assertEqual(result.evidence, ["forced override evidence"])


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
