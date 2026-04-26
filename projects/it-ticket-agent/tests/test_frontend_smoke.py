from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from it_ticket_agent.bad_cases.models import BadCaseCandidate
from it_ticket_agent.memory.models import IncidentCase
from it_ticket_agent.session.models import ConversationSession, ConversationTurn
from it_ticket_agent.state.incident_state import IncidentState


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "src" / "it_ticket_agent" / "static"


class FrontendConsoleSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "frontend-smoke.db")
        self.env_patcher = patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "STORAGE_BACKEND": "sqlite",
                "POSTGRES_DSN": "",
                "APPROVAL_DB_PATH": self.db_path,
                "MCP_CONNECTIONS_PATH": str(PROJECT_ROOT / "mcp_connections.yaml"),
                "LLM_BASE_URL": "",
                "LLM_API_KEY": "",
                "LLM_MODEL": "",
                "RAG_ENABLED": "false",
                "LANGFUSE_PUBLIC_KEY": "",
                "LANGFUSE_SECRET_KEY": "",
            },
            clear=False,
        )
        self.env_patcher.start()

        importlib.invalidate_caches()
        if "it_ticket_agent.settings" in sys.modules:
            importlib.reload(sys.modules["it_ticket_agent.settings"])
        if "it_ticket_agent.main" in sys.modules:
            self.main_module = importlib.reload(sys.modules["it_ticket_agent.main"])
        else:
            self.main_module = importlib.import_module("it_ticket_agent.main")

        self.client = TestClient(self.main_module.app)
        self.client.__enter__()
        self.app = self.client.app
        self.session_store = self.app.state.session_store
        self.approval_store = self.app.state.approval_store
        self.interrupt_store = self.app.state.interrupt_store
        self.execution_store = self.app.state.execution_store
        self.checkpoint_store = self.app.state.checkpoint_store
        self.incident_case_store = self.app.state.incident_case_store
        self.bad_case_candidate_store = self.app.state.bad_case_candidate_store
        self.playbook_store = self.app.state.playbook_store

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _build_incident_state(
        *,
        session_id: str,
        ticket_id: str,
        status: str,
        service: str = "checkout-service",
        message: str = "checkout-service 发布失败，需要排查最近变更",
    ) -> IncidentState:
        return IncidentState(
            ticket_id=ticket_id,
            user_id="frontend-user",
            message=message,
            thread_id=session_id,
            service=service,
            environment="prod",
            cluster="prod-shanghai-1",
            namespace="default",
            channel="feishu",
            status=status,
            metadata={},
        )

    def _create_session(
        self,
        *,
        session_id: str,
        ticket_id: str,
        status: str,
        current_stage: str,
        current_agent: str = "supervisor",
        pending_interrupt_id: str | None = None,
        latest_approval_id: str | None = None,
        last_checkpoint_id: str | None = None,
        incident_state: IncidentState | None = None,
    ) -> dict:
        state = incident_state or self._build_incident_state(
            session_id=session_id,
            ticket_id=ticket_id,
            status=status,
        )
        return self.session_store.create(
            ConversationSession(
                session_id=session_id,
                thread_id=session_id,
                ticket_id=ticket_id,
                user_id="frontend-user",
                status=status,
                current_stage=current_stage,
                current_agent=current_agent,
                latest_approval_id=latest_approval_id,
                pending_interrupt_id=pending_interrupt_id,
                last_checkpoint_id=last_checkpoint_id,
                incident_state=state,
                session_memory={
                    "original_user_message": state.message,
                    "current_stage": current_stage,
                    "pending_interrupt": (
                        {"interrupt_id": pending_interrupt_id}
                        if pending_interrupt_id
                        else None
                    ),
                },
            )
        )

    def _append_assistant_turn(
        self,
        session_id: str,
        *,
        content: str,
        status: str = "completed",
        diagnosis: dict | None = None,
        approval_request: dict | None = None,
    ) -> dict:
        return self.session_store.append_turn(
            ConversationTurn(
                session_id=session_id,
                role="assistant",
                content=content,
                structured_payload={
                    "status": status,
                    "diagnosis": diagnosis or {},
                    "approval_request": approval_request,
                },
            )
        )

    def test_console_page_and_static_assets_expose_new_manual_intervention_controls(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('id="pageNav"', html)
        self.assertIn('id="chatPage"', html)
        self.assertIn('id="workspacePage"', html)
        self.assertIn('id="sessionSummary"', html)
        self.assertIn('id="recentSessions"', html)
        self.assertIn('id="agentActivityPanel"', html)
        self.assertIn('id="agentActivityLog"', html)
        self.assertIn('class="ghost-btn new-session-action"', html)
        self.assertIn('id="interruptSummary"', html)
        self.assertIn('id="clarificationForm"', html)
        self.assertIn('id="feedbackForm"', html)
        self.assertIn('id="messageModeSupplement"', html)
        self.assertIn('id="environmentName"', html)
        self.assertIn('id="mockWorldSelect"', html)
        self.assertIn('id="mockWorldSummary"', html)
        self.assertIn('class="composer ticket-composer"', html)
        self.assertIn('class="ticket-form-grid"', html)
        self.assertIn('id="feedbackResolution"', html)
        self.assertIn('id="executionRecoveryPanel"', html)
        self.assertIn('id="workspaceTabs"', html)
        self.assertIn('id="sessionInspectorPanel"', html)
        self.assertIn('id="playbookWorkbench"', html)
        self.assertIn('id="caseReviewWorkbench"', html)
        self.assertIn('id="caseExtractPlaybookBtn"', html)
        self.assertIn('id="badCaseWorkbench"', html)

        app_js = self.client.get("/static/app.js")
        self.assertEqual(app_js.status_code, 200)
        self.assertIn("restoreConversationFromStorage", app_js.text)
        self.assertIn("setPageView", app_js.text)
        self.assertIn("startNewConversation", app_js.text)
        self.assertIn("loadRecentSessions", app_js.text)
        self.assertIn("startAgentActivity", app_js.text)
        self.assertIn("formatAgentActivityFromEvent", app_js.text)
        self.assertIn("diagnosis?.display_mode === 'user_report'", app_js.text)
        self.assertIn("buildDiagnosisReportCard", app_js.text)
        self.assertIn("addTicketMessage", app_js.text)
        self.assertIn("environmentNameInput", app_js.text)
        self.assertIn("loadMockWorlds", app_js.text)
        self.assertIn("selectedMockWorldPayload", app_js.text)
        self.assertIn("shouldRenderAssistantAsPlainChat", app_js.text)
        self.assertIn("syncMockWorldFromDetail", app_js.text)
        self.assertIn("renderAgentActivityEvents", app_js.text)
        self.assertIn("isNewConversation ? '/api/v1/conversations'", app_js.text)
        self.assertIn("tool.started", app_js.text)
        self.assertIn("submitFeedback", app_js.text)
        self.assertIn("setComposerMode", app_js.text)
        self.assertIn("refreshExecutionRecovery", app_js.text)
        self.assertIn("resumeClarificationAnswer", app_js.text)
        self.assertIn("currentPendingInterrupt?.type === 'clarification'", app_js.text)
        self.assertIn("renderSessionInspector", app_js.text)
        self.assertIn("loadPlaybooks", app_js.text)
        self.assertIn("reviewCase", app_js.text)
        self.assertIn("extractPlaybookFromSelectedCase", app_js.text)
        self.assertIn("appendFormSection", app_js.text)
        self.assertIn("appendCardList", app_js.text)
        self.assertIn("loadBadCaseCandidates", app_js.text)
        self.assertIn("exportBadCaseEvalSkeleton", app_js.text)
        self.assertIn("export-eval-skeleton", app_js.text)

        styles = self.client.get("/static/styles.css")
        self.assertEqual(styles.status_code, 200)
        self.assertIn(".sidebar-card", styles.text)
        self.assertIn(".page-nav", styles.text)
        self.assertIn(".page-view", styles.text)
        self.assertIn(".agent-activity", styles.text)
        self.assertIn(".activity-log", styles.text)
        self.assertIn(".session-list", styles.text)
        self.assertIn(".interrupt-form", styles.text)
        self.assertIn(".mode-chip", styles.text)
        self.assertIn(".ticket-form-grid", styles.text)
        self.assertIn(".diagnosis-report-card", styles.text)
        self.assertIn(".mock-world-summary", styles.text)
        self.assertIn(".mock-world-mode", styles.text)
        self.assertIn(".report-field", styles.text)
        self.assertIn(".status-pill", styles.text)
        self.assertIn(".workspace-shell", styles.text)
        self.assertIn(".data-table", styles.text)
        self.assertIn(".timeline-item", styles.text)
        self.assertIn(".inspector-panel", styles.text)
        self.assertIn(".form-section", styles.text)
        self.assertIn(".form-field", styles.text)
        self.assertIn(".readable-card", styles.text)

        syntax_check = subprocess.run(
            ["node", "--check", str(STATIC_DIR / "app.js")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            syntax_check.returncode,
            0,
            msg=f"node --check failed: {syntax_check.stderr}",
        )

    def test_mock_worlds_api_exposes_tool_profiles_for_frontend_sandbox(self) -> None:
        response = self.client.get("/api/v1/mock-worlds")
        self.assertEqual(response.status_code, 200)
        worlds = response.json()
        self.assertTrue(worlds)
        network_world = next((item for item in worlds if item["world_id"] == "case2::order-service"), None)
        self.assertIsNotNone(network_world)
        self.assertEqual(network_world["case_id"], "case2")
        self.assertEqual(network_world["service"], "order-service")
        self.assertIn("inspect_vpc_connectivity", network_world["tool_names"])
        self.assertEqual(
            network_world["mock_tool_responses"]["inspect_vpc_connectivity"]["payload"]["connectivity_status"],
            "blocked",
        )
        self.assertEqual(
            network_world["mock_tool_responses"]["inspect_upstream_dependency"]["payload"]["dependency_status"],
            "degraded",
        )


    def test_sessions_list_api_supports_frontend_session_management(self) -> None:
        first = self._create_session(
            session_id="frontend-session-list-1",
            ticket_id="FRONTEND-SESSION-1",
            status="active",
            current_stage="routing",
        )
        second = self._create_session(
            session_id="frontend-session-list-2",
            ticket_id="FRONTEND-SESSION-2",
            status="completed",
            current_stage="finalize",
        )

        response = self.client.get("/api/v1/sessions?user_id=frontend-user&limit=10")
        self.assertEqual(response.status_code, 200)
        sessions = response.json()
        session_ids = {item["session_id"] for item in sessions}
        self.assertIn(first["session_id"], session_ids)
        self.assertIn(second["session_id"], session_ids)
        self.assertTrue(all(item["user_id"] == "frontend-user" for item in sessions))

        completed_response = self.client.get("/api/v1/sessions?status=completed")
        self.assertEqual(completed_response.status_code, 200)
        self.assertTrue(any(item["session_id"] == second["session_id"] for item in completed_response.json()))

    def test_conversation_detail_exposes_approval_interrupt_for_frontend_restore(self) -> None:
        session_id = "frontend-approval-session"
        ticket_id = "FRONTEND-APPROVAL-1"
        approval = self.approval_store.create(
            {
                "approval_id": "approval-frontend-1",
                "ticket_id": ticket_id,
                "thread_id": session_id,
                "action": "cicd.rollback_release",
                "risk": "high",
                "reason": "发布后故障，需要审批回滚",
                "params": {
                    "service": "checkout-service",
                    "cluster": "prod-shanghai-1",
                    "namespace": "default",
                },
            }
        )
        interrupt = self.interrupt_store.create_approval_interrupt(
            session_id=session_id,
            ticket_id=ticket_id,
            reason="需要审批后继续执行回滚。",
            question="是否批准执行生产回滚？",
            expected_input_schema={"type": "object"},
            metadata={"approval_id": approval["approval_id"]},
        )
        approval_payload = {**approval, "interrupt_id": interrupt["interrupt_id"]}

        self._create_session(
            session_id=session_id,
            ticket_id=ticket_id,
            status="awaiting_approval",
            current_stage="awaiting_approval",
            current_agent="cicd_agent",
            pending_interrupt_id=interrupt["interrupt_id"],
            latest_approval_id=approval["approval_id"],
            incident_state=self._build_incident_state(
                session_id=session_id,
                ticket_id=ticket_id,
                status="awaiting_approval",
            ),
        )
        self._append_assistant_turn(
            session_id,
            content="检测到高风险动作，需要人工审批。",
            status="awaiting_approval",
            diagnosis={"summary": "需要审批后再执行回滚。"},
            approval_request=approval_payload,
        )

        response = self.client.get(f"/api/v1/conversations/{session_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["session"]["status"], "awaiting_approval")
        self.assertEqual(payload["pending_interrupt"]["type"], "approval")
        self.assertEqual(payload["pending_interrupt"]["interrupt_id"], interrupt["interrupt_id"])
        self.assertTrue(payload["turns"])
        last_turn = payload["turns"][-1]
        self.assertEqual(last_turn["structured_payload"]["approval_request"]["action"], "cicd.rollback_release")
        self.assertEqual(
            last_turn["structured_payload"]["approval_request"]["interrupt_id"],
            interrupt["interrupt_id"],
        )

    def test_workbench_review_and_bad_case_candidate_api_contract(self) -> None:
        saved_case = self.incident_case_store.upsert(
            IncidentCase(
                session_id="frontend-case-review-session",
                thread_id="frontend-case-review-session",
                ticket_id="FRONTEND-CASE-REVIEW-1",
                service="checkout-service",
                cluster="prod-shanghai-1",
                namespace="default",
                current_agent="diagnosis_agent",
                failure_mode="release_regression",
                root_cause_taxonomy="deploy_change",
                signal_pattern="deploy window overlaps error spike",
                action_pattern="rollback release",
                symptom="发布后错误率升高",
                root_cause="版本变更导致连接池异常",
                key_evidence=["发布窗口吻合", "连接池指标恶化"],
                final_action="建议回滚",
                selected_hypothesis_id="hypothesis-1",
                final_conclusion="高概率是发布引入的连接池问题。",
            )
        )

        review_response = self.client.post(
            f"/api/v1/cases/{saved_case['case_id']}/review",
            json={
                "human_verified": True,
                "hypothesis_accuracy": {"hypothesis-1": 0.95},
                "reviewed_by": "frontend-reviewer",
                "review_note": "值班确认根因准确",
            },
        )
        self.assertEqual(review_response.status_code, 200)
        review_payload = review_response.json()
        reviewed_case = review_payload["incident_case"]
        self.assertEqual(reviewed_case["case_status"], "verified")
        self.assertTrue(reviewed_case["human_verified"])
        self.assertEqual(reviewed_case["failure_mode"], "release_regression")
        self.assertEqual(reviewed_case["reviewed_by"], "frontend-reviewer")
        self.assertFalse(review_payload["playbook_extraction"]["extracted"])
        self.assertIn("同类已确认案例不足", review_payload["playbook_extraction"]["reason"])

        extraction_response = self.client.post(
            f"/api/v1/cases/{saved_case['case_id']}/extract-playbook",
            json={"allow_single_case": True, "min_cases": 1},
        )
        self.assertEqual(extraction_response.status_code, 200)
        extraction_payload = extraction_response.json()
        self.assertTrue(extraction_payload["extracted"])
        playbook_candidate = extraction_payload["playbook_candidate"]
        self.assertIsNotNone(playbook_candidate)
        self.assertEqual(playbook_candidate["status"], "pending_review")
        self.assertFalse(playbook_candidate["human_verified"])
        self.assertIn(saved_case["case_id"], playbook_candidate["source_case_ids"])

        playbook_review_response = self.client.post(
            f"/api/v1/playbooks/{playbook_candidate['playbook_id']}/review",
            json={
                "human_verified": True,
                "status": "verified",
                "reviewed_by": "frontend-reviewer",
                "review_note": "单案例 mock 世界演示通过，启用前仍需人工确认。",
            },
        )
        self.assertEqual(playbook_review_response.status_code, 200)
        reviewed_playbook = playbook_review_response.json()
        self.assertEqual(reviewed_playbook["status"], "verified")
        self.assertTrue(reviewed_playbook["human_verified"])

        candidate = self.bad_case_candidate_store.create(
            BadCaseCandidate(
                session_id="frontend-bad-case-session",
                thread_id="frontend-bad-case-session",
                ticket_id="FRONTEND-BAD-CASE-1",
                source="runtime_completion",
                reason_codes=["human_feedback_negative", "retrieval_expansion_no_gain"],
                severity="high",
                request_payload={"message": "checkout-service 发布后失败"},
                response_payload={"diagnosis": {"summary": "误判为网络问题"}},
                context_snapshot={"case_recall": {"state": "missed"}},
                human_feedback={"human_verified": False, "comment": "真实根因是发布变更"},
            )
        )

        list_response = self.client.get("/api/v1/bad-case-candidates?export_status=pending")
        self.assertEqual(list_response.status_code, 200)
        self.assertTrue(any(item["candidate_id"] == candidate["candidate_id"] for item in list_response.json()))

        detail_response = self.client.get(f"/api/v1/bad-case-candidates/{candidate['candidate_id']}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["reason_codes"], ["human_feedback_negative", "retrieval_expansion_no_gain"])

        status_response = self.client.post(
            f"/api/v1/bad-case-candidates/{candidate['candidate_id']}/export-status",
            json={
                "export_status": "ignored",
                "export_metadata": {"updated_by": "frontend-reviewer"},
            },
        )
        self.assertEqual(status_response.status_code, 200)
        ignored = status_response.json()
        self.assertEqual(ignored["export_status"], "ignored")

        output_dir = Path(self.temp_dir.name) / "generated"
        export_response = self.client.post(
            f"/api/v1/bad-case-candidates/{candidate['candidate_id']}/export-eval-skeleton",
            json={"output_dir": str(output_dir), "mark_exported": True},
        )
        self.assertEqual(export_response.status_code, 200)
        exported = export_response.json()
        self.assertEqual(exported["candidate_id"], candidate["candidate_id"])
        self.assertEqual(exported["target_dataset"], "tool_mock")
        output_path = Path(exported["output_path"])
        self.assertTrue(output_path.exists())
        exported_payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(exported_payload["candidate_id"], candidate["candidate_id"])
        self.assertEqual(exported["candidate"]["export_status"], "exported")
        self.assertEqual(exported["candidate"]["export_metadata"]["export_format"], "eval_skeleton")

        exported_payload["eval_skeleton"]["case_id"] = "curated_frontend_bad_case"
        exported_payload["eval_skeleton"]["description"] = "前端导出的 bad case skeleton 可被 dry-run merge 校验。"
        exported_payload["eval_skeleton"].get("expect", {}).pop("_todo", None)
        curated_path = output_dir / "tool_mock__curated_frontend_bad_case.json"
        curated_path.write_text(json.dumps(exported_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        merge_response = self.client.post(
            "/api/v1/bad-case-candidates/merge-curated-eval-skeletons",
            json={
                "input_paths": [str(curated_path)],
                "dry_run": True,
                "mark_merged": False,
            },
        )
        self.assertEqual(merge_response.status_code, 200)
        merged = merge_response.json()
        self.assertEqual(merged["count"], 1)
        self.assertEqual(merged["results"][0]["case_id"], "curated_frontend_bad_case")

    def test_feedback_resume_api_matches_frontend_form_contract(self) -> None:
        session_id = "frontend-feedback-session"
        ticket_id = "FRONTEND-FEEDBACK-1"
        interrupt = self.interrupt_store.create_feedback_interrupt(
            session_id=session_id,
            ticket_id=ticket_id,
            reason="诊断已完成，需要人工确认根因与建议动作是否准确。",
            question="请确认本次根因判断是否正确；如不正确，可补充真实根因假设和各假设准确度。",
            expected_input_schema={"type": "object"},
            metadata={"selected_hypothesis_id": "hypothesis-1"},
        )

        self._create_session(
            session_id=session_id,
            ticket_id=ticket_id,
            status="completed",
            current_stage="finalize",
            current_agent="diagnosis_agent",
            pending_interrupt_id=interrupt["interrupt_id"],
            incident_state=self._build_incident_state(
                session_id=session_id,
                ticket_id=ticket_id,
                status="completed",
            ),
        )
        self.incident_case_store.upsert(
            IncidentCase(
                session_id=session_id,
                thread_id=session_id,
                ticket_id=ticket_id,
                service="checkout-service",
                cluster="prod-shanghai-1",
                namespace="default",
                current_agent="diagnosis_agent",
                symptom="发布后故障",
                root_cause="版本变更导致连接池异常",
                key_evidence=["发布窗口吻合", "连接池指标恶化"],
                final_action="建议回滚",
                approval_required=True,
                selected_hypothesis_id="hypothesis-1",
                final_conclusion="高概率是发布引入的连接池问题。",
            )
        )

        response = self.client.post(
            f"/api/v1/conversations/{session_id}/resume",
            json={
                "interrupt_id": interrupt["interrupt_id"],
                "answer_payload": {
                    "human_verified": False,
                    "actual_root_cause_hypothesis": "真实根因是数据库只读切换",
                    "hypothesis_accuracy": {"hypothesis-1": 0.25, "hypothesis-2": 0.8},
                    "comment": "人工修正诊断结论",
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["status"], "completed")
        self.assertFalse(payload["diagnosis"]["feedback"]["human_verified"])
        self.assertTrue(payload["diagnosis"]["feedback_reopened"])
        self.assertEqual(
            payload["diagnosis"]["feedback"]["actual_root_cause_hypothesis"],
            "真实根因是数据库只读切换",
        )
        session = self.client.get(f"/api/v1/sessions/{session_id}").json()
        if payload["pending_interrupt"] is not None:
            self.assertEqual(session["pending_interrupt_id"], payload["pending_interrupt"]["interrupt_id"])
        else:
            self.assertIsNone(session["pending_interrupt_id"])

    def test_execution_recovery_endpoint_exposes_manual_intervention_payload_for_frontend_panel(self) -> None:
        session_id = "frontend-recovery-session"
        ticket_id = "FRONTEND-RECOVERY-1"
        incident_state = self._build_incident_state(
            session_id=session_id,
            ticket_id=ticket_id,
            status="failed",
        )

        plan = self.execution_store.create_plan(
            {
                "plan_id": "plan-frontend-recovery",
                "session_id": session_id,
                "thread_id": session_id,
                "ticket_id": ticket_id,
                "status": "failed",
                "steps": [],
                "current_step_id": "step-2",
                "summary": "执行回滚失败",
                "recovery": {
                    "can_resume": False,
                    "recovery_action": "manual_intervention",
                    "recovery_reason": "主动作执行失败，当前阶段统一转人工处理。",
                    "resume_from_step_id": "step-2",
                    "failed_step_id": "step-2",
                    "last_completed_step_id": "step-1",
                    "suggested_retry_count": 1,
                    "hints": [
                        "先人工确认目标资源状态。",
                        "结合 retry_policy 与补偿策略决定后续动作。",
                    ],
                },
                "metadata": {"approval_id": "approval-frontend-recovery"},
            }
        )
        self.execution_store.create_step(
            {
                "step_id": "step-1",
                "plan_id": plan["plan_id"],
                "session_id": session_id,
                "action": "execution.precheck_binding",
                "tool_name": "execution.precheck_binding",
                "params": {},
                "sequence": 10,
                "dependencies": [],
                "retry_policy": {},
                "compensation": None,
                "attempt": 1,
                "last_error": {},
                "status": "completed",
                "result_summary": "审批快照校验通过",
                "evidence": [],
                "metadata": {},
            }
        )
        self.execution_store.create_step(
            {
                "step_id": "step-2",
                "plan_id": plan["plan_id"],
                "session_id": session_id,
                "action": "cicd.rollback_release",
                "tool_name": "cicd.rollback_release",
                "params": {"service": "checkout-service"},
                "sequence": 20,
                "dependencies": ["step-1"],
                "retry_policy": {"max_attempts": 2},
                "compensation": None,
                "attempt": 1,
                "last_error": {"error_type": "RuntimeError"},
                "status": "failed",
                "result_summary": "rollback tool failed",
                "evidence": ["rollback tool failed", "job timeout"],
                "metadata": {},
            }
        )
        checkpoint = self.checkpoint_store.create(
            {
                "session_id": session_id,
                "thread_id": session_id,
                "ticket_id": ticket_id,
                "stage": "execution_failed",
                "next_action": "manual_intervention",
                "state_snapshot": incident_state.model_dump(),
                "metadata": {
                    "plan_id": plan["plan_id"],
                    "step_id": "step-2",
                    "failed_step_id": "step-2",
                    "response_status": "failed",
                    "step_status": "failed",
                    "recovery_action": "manual_intervention",
                },
            }
        )
        self._create_session(
            session_id=session_id,
            ticket_id=ticket_id,
            status="failed",
            current_stage="finalize",
            current_agent="cicd_agent",
            last_checkpoint_id=checkpoint["checkpoint_id"],
            incident_state=incident_state,
        )

        response = self.client.get(f"/api/v1/sessions/{session_id}/execution-recovery")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["recovery_action"], "manual_intervention")
        self.assertEqual(payload["failed_step_id"], "step-2")
        self.assertEqual(payload["resume_from_step_id"], "step-2")
        self.assertEqual(payload["execution_plan"]["status"], "failed")
        self.assertEqual(payload["latest_checkpoint"]["stage"], "execution_failed")
        self.assertIn("先人工确认目标资源状态。", payload["recovery_hints"])


if __name__ == "__main__":
    unittest.main()
