from __future__ import annotations

import json
import os
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
        mock_case: str | None = None,
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
                "mock_case": mock_case,
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

        self.assertEqual(result.summary, "")
        self.assertEqual(result.payload["health_status"], "unhealthy")
        self.assertEqual(result.evidence, [])

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

    async def test_case_profile_mock_uses_shared_context_case(self) -> None:
        task = self.build_task(
            message="order-service Pod OOMKilled，帮我看最近状态",
            service="order-service",
            mock_case="case1",
        )

        changes = await GetChangeRecordsTool().run(task)
        pods = await CheckPodStatusTool().run(task)

        self.assertEqual(changes.payload["service"], "order-service")
        self.assertEqual(changes.payload["change_count"], 0)
        self.assertEqual(pods.payload["pods"][1]["last_reason"], "OOMKilled")
        self.assertEqual(pods.payload["ready_replicas"], 1)

    async def test_env_driven_case_profile_selects_mock_world(self) -> None:
        with patch.dict(os.environ, {"IT_TICKET_AGENT_CASE": "case3"}, clear=False):
            task = self.build_task(message="查看 order-service 最近变更", service="order-service")

            changes = await GetChangeRecordsTool().run(task)

            self.assertEqual(changes.payload["changes"][0]["change_id"], "MR-8421")
            self.assertEqual(changes.payload["changes"][0]["commit_id"], "8f31c2a")

    async def test_env_case_map_supports_service_specific_world_selection(self) -> None:
        with patch.dict(
            os.environ,
            {"IT_TICKET_AGENT_CASES": '{"order-service":"case2"}', "IT_TICKET_AGENT_CASE": ""},
            clear=False,
        ):
            task = self.build_task(message="查看 order-service 网络超时", service="order-service")
            result = await CheckServiceHealthTool().run(task)

        self.assertEqual(result.payload["service"], "order-service")
        self.assertEqual(result.payload["health_status"], "degraded")

    async def test_case_profiles_can_be_overridden_by_json_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            profile_path = Path(tmp_dir) / "case_profiles.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "custom_degraded": {
                            "services": {
                                "order-service": {
                                    "check_service_health": {
                                        "summary": "json override",
                                        "payload": {"service": "order-service", "health_status": "degraded"},
                                        "evidence": ["from case profile file"],
                                    }
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
                    "IT_TICKET_AGENT_CASE_PROFILES_PATH": str(profile_path),
                    "IT_TICKET_AGENT_CASE": "custom_degraded",
                },
                clear=False,
            ):
                task = self.build_task(message="查看 order-service", service="order-service")
                result = await CheckServiceHealthTool().run(task)

            self.assertEqual(result.summary, "")
            self.assertEqual(result.payload["health_status"], "degraded")
            self.assertEqual(result.evidence, [])


if __name__ == "__main__":
    unittest.main()
