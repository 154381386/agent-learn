from __future__ import annotations

import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from it_ticket_agent.runtime.contracts import TaskEnvelope
from it_ticket_agent.tools import (
    CheckCanaryStatusTool,
    CheckPodStatusTool,
    CheckRecentAlertsTool,
    CheckServiceHealthTool,
    GetChangeRecordsTool,
    GetGitCommitHistoryTool,
    GetRollbackHistoryTool,
    InspectBuildFailureLogsTool,
)


class CICDToolsTest(unittest.IsolatedAsyncioTestCase):
    def build_task(
        self,
        *,
        message: str,
        service: str = "order-service",
        mock_tool_responses: dict | None = None,
        mock_scenario: str | None = None,
        mock_scenarios: dict | None = None,
    ) -> TaskEnvelope:
        return TaskEnvelope(
            task_id="task-1",
            ticket_id="ticket-1",
            goal="诊断并给出下一步建议",
            shared_context={
                "message": message,
                "service": service,
                "cluster": "prod-shanghai-1",
                "namespace": "default",
                "mock_tool_responses": mock_tool_responses or {},
                "mock_scenario": mock_scenario,
                "mock_scenarios": mock_scenarios or {},
            },
        )

    async def test_service_health_supports_shared_mock_response(self) -> None:
        task = self.build_task(
            message="order-service 发布后大量报错",
            mock_tool_responses={
                "check_service_health": {
                    "summary": "mock health",
                    "payload": {"health_status": "unhealthy"},
                    "evidence": ["mock evidence"],
                }
            },
        )

        result = await CheckServiceHealthTool().run(task)

        self.assertEqual(result.summary, "mock health")
        self.assertEqual(result.payload["health_status"], "unhealthy")
        self.assertEqual(result.evidence, ["mock evidence"])

    async def test_recent_alerts_returns_structured_fallback(self) -> None:
        task = self.build_task(message="发布后错误率和延迟都升高，还有告警")

        result = await CheckRecentAlertsTool().run(task)

        self.assertEqual(result.status, "completed")
        self.assertGreaterEqual(result.payload["alert_count"], 1)
        self.assertIn("alerts", result.payload)
        self.assertIn("highest_severity", result.payload)

    async def test_canary_and_build_tools_generate_mock_facts(self) -> None:
        task = self.build_task(message="灰度发布失败，流水线部署阶段 readiness probe failed")

        canary = await CheckCanaryStatusTool().run(task)
        build = await InspectBuildFailureLogsTool().run(task)

        self.assertIn(canary.payload["canary_status"], {"running", "rollback_pending", "not_in_progress"})
        self.assertIn("traffic_weight_percent", canary.payload)
        self.assertEqual(build.payload["failed_stage"], "deploy")
        self.assertIn("suspected_error", build.payload)

    async def test_rollback_history_marks_release_failure_as_recommendable(self) -> None:
        task = self.build_task(message="这次发布失败了，想确认是否可以回滚")

        result = await GetRollbackHistoryTool().run(task)

        self.assertTrue(result.payload["rollback_recommended"])
        self.assertIn("last_known_stable_revision", result.payload)
        self.assertGreaterEqual(len(result.payload["recent_rollbacks"]), 1)

    async def test_service_specific_profile_mock_uses_shared_context_scenario(self) -> None:
        task = self.build_task(
            message="帮我看车云服务是否异常",
            service="车云服务",
            mock_scenario="error",
        )

        commits = await GetGitCommitHistoryTool().run(task)
        pods = await CheckPodStatusTool().run(task)

        self.assertEqual(commits.payload["service"], "车云服务")
        self.assertEqual(commits.payload["commits"][0]["sha"], "a1b2c3d")
        self.assertEqual(pods.payload["pods"][0]["name"], "car-cloud-6fd79")
        self.assertEqual(pods.payload["ready_replicas"], 1)

    async def test_env_driven_mock_scenarios_support_different_services(self) -> None:
        with patch.dict(
            os.environ,
            {
                "IT_TICKET_AGENT_MOCK_SCENARIOS": '{"车云服务":"health","支付服务":"error"}',
                "IT_TICKET_AGENT_MOCK_SCENARIO": "",
            },
            clear=False,
        ):
            car_task = self.build_task(message="查看车云服务", service="车云服务")
            payment_task = self.build_task(message="查看支付服务", service="支付服务")

            car_health = await CheckServiceHealthTool().run(car_task)
            payment_changes = await GetChangeRecordsTool().run(payment_task)

            self.assertEqual(car_health.payload["health_status"], "healthy")
            self.assertEqual(payment_changes.payload["changes"][0]["change_id"], "CHG-240408-31")

    async def test_global_env_mock_scenario_can_switch_payment_to_health(self) -> None:
        with patch.dict(os.environ, {"IT_TICKET_AGENT_MOCK_SCENARIO": "health"}, clear=False):
            task = self.build_task(message="支付服务当前状态", service="支付服务")

            result = await CheckServiceHealthTool().run(task)

            self.assertEqual(result.payload["service"], "支付服务")
            self.assertEqual(result.payload["health_status"], "healthy")

    async def test_mock_profiles_can_be_overridden_by_json_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            profile_path = Path(tmp_dir) / "profiles.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "车云服务": {
                            "error": {
                                "check_service_health": {
                                    "summary": "json override",
                                    "payload": {"service": "车云服务", "health_status": "degraded"},
                                    "evidence": ["from json file"]
                                }
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "IT_TICKET_AGENT_MOCK_PROFILES_PATH": str(profile_path),
                    "IT_TICKET_AGENT_MOCK_SCENARIO": "error",
                },
                clear=False,
            ):
                task = self.build_task(message="查看车云服务", service="车云服务")
                result = await CheckServiceHealthTool().run(task)

            self.assertEqual(result.summary, "json override")
            self.assertEqual(result.payload["health_status"], "degraded")
            self.assertEqual(result.evidence, ["from json file"])


if __name__ == "__main__":
    unittest.main()
