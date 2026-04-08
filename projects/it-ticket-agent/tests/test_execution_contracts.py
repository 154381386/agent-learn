from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from it_ticket_agent.execution import (
    ExecutionPlan,
    ExecutionRecoveryMetadata,
    ExecutionStep,
    default_compensation_policy,
    default_retry_policy,
)
from it_ticket_agent.execution.store import ExecutionStoreV2


class ExecutionContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "execution-contracts.db")
        self.store = ExecutionStoreV2(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_execution_store_roundtrips_recovery_retry_and_dependency_contracts(self) -> None:
        plan = self.store.create_plan(
            ExecutionPlan(
                session_id="session-1",
                thread_id="thread-1",
                ticket_id="ticket-1",
                status="running",
                steps=[],
                current_step_id="step-2",
                summary="执行计划测试",
                recovery=ExecutionRecoveryMetadata(
                    can_resume=True,
                    recovery_action="retry_execution_step",
                    recovery_reason="主动作失败，可重试。",
                    resume_from_step_id="step-2",
                    failed_step_id="step-2",
                    last_completed_step_id="step-1",
                    suggested_retry_count=1,
                    hints=["先确认目标资源状态。"],
                ),
                metadata={"source": "unit-test"},
            )
        )
        step = self.store.create_step(
            ExecutionStep(
                step_id="step-2",
                plan_id=plan.plan_id,
                session_id="session-1",
                action="cicd.rollback_release",
                tool_name="cicd.rollback_release",
                params={"service": "checkout-service"},
                sequence=20,
                dependencies=["step-1"],
                retry_policy=default_retry_policy("cicd.rollback_release", risk="high", step_kind="tool"),
                compensation=default_compensation_policy("cicd.rollback_release", risk="high"),
                attempt=1,
                last_error={"error_type": "RuntimeError"},
                status="failed",
                result_summary="执行失败",
                evidence=["rollback tool failed"],
                metadata={"step_kind": "primary_action"},
            )
        )

        loaded_plan = self.store.get_plan(plan.plan_id)
        loaded_step = self.store.get_step(step.step_id)

        self.assertIsNotNone(loaded_plan)
        self.assertIsNotNone(loaded_step)
        assert loaded_plan is not None
        assert loaded_step is not None
        self.assertEqual(loaded_plan.recovery.failed_step_id, "step-2")
        self.assertEqual(loaded_plan.recovery.recovery_action, "retry_execution_step")
        self.assertEqual(loaded_step.dependencies, ["step-1"])
        self.assertEqual(loaded_step.retry_policy.max_attempts, 2)
        self.assertIsNotNone(loaded_step.compensation)
        self.assertEqual(loaded_step.compensation.mode, "manual")
        self.assertEqual(loaded_step.attempt, 1)
        self.assertEqual(loaded_step.last_error["error_type"], "RuntimeError")
