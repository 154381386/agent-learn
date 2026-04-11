from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from it_ticket_agent.approval.pg_store import PostgresApprovalStoreV2
from it_ticket_agent.approval.models import ApprovalProposal, ApprovalRequest
from it_ticket_agent.checkpoints.models import ExecutionCheckpoint
from it_ticket_agent.checkpoints.pg_store import PostgresCheckpointStoreV2
from it_ticket_agent.events.models import SystemEvent
from it_ticket_agent.events.pg_store import PostgresSystemEventStore
from it_ticket_agent.execution.models import ExecutionPlan, ExecutionStep
from it_ticket_agent.execution.pg_store import PostgresExecutionStoreV2
from it_ticket_agent.interrupts.models import InterruptRequest
from it_ticket_agent.interrupts.pg_store import PostgresInterruptStoreV2
from it_ticket_agent.memory.models import IncidentCase, ProcessMemoryEntry
from it_ticket_agent.memory.pg_store import PostgresProcessMemoryStoreV2
from it_ticket_agent.orchestration.ranker_weights import RankerWeightsManager
from it_ticket_agent.session.models import ConversationSession, ConversationTurn
from it_ticket_agent.session.pg_store import PostgresSessionStore
from it_ticket_agent.state.incident_state import IncidentState


class PostgresStorageSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn = os.getenv("POSTGRES_TEST_DSN", "").strip()
        if not cls.dsn:
            raise unittest.SkipTest("POSTGRES_TEST_DSN is not configured")

    def test_postgres_stores_roundtrip(self) -> None:
        session_store = PostgresSessionStore(self.dsn)
        event_store = PostgresSystemEventStore(self.dsn)
        approval_store = PostgresApprovalStoreV2(self.dsn)
        interrupt_store = PostgresInterruptStoreV2(self.dsn)
        checkpoint_store = PostgresCheckpointStoreV2(self.dsn)
        execution_store = PostgresExecutionStoreV2(self.dsn)
        memory_store = PostgresProcessMemoryStoreV2(self.dsn)

        session = session_store.create_session(
            ConversationSession(
                session_id="pg-smoke-session",
                thread_id="pg-smoke-thread",
                ticket_id="pg-smoke-ticket",
                user_id="u1",
                incident_state=IncidentState(
                    ticket_id="pg-smoke-ticket",
                    user_id="u1",
                    message="postgres smoke test",
                ),
            )
        )
        self.assertEqual(session_store.get_session(session.session_id).session_id, session.session_id)

        turn = session_store.append_conversation_turn(
            ConversationTurn(
                session_id=session.session_id,
                role="user",
                content="hello postgres",
            )
        )
        self.assertEqual(session_store.list_conversation_turns(session.session_id)[0].turn_id, turn.turn_id)

        event = event_store.create_event(
            SystemEvent(
                session_id=session.session_id,
                thread_id=session.thread_id,
                ticket_id=session.ticket_id,
                event_type="pg.smoke",
            )
        )
        self.assertEqual(event_store.list_events(session.session_id)[0].event_id, event.event_id)

        approval = approval_store.create_request(
            ApprovalRequest(
                approval_id="pg-approval-1",
                ticket_id=session.ticket_id,
                thread_id=session.thread_id,
                proposals=[
                    ApprovalProposal(
                        proposal_id="proposal-1",
                        agent="ranker",
                        action="observe_service",
                        reason="smoke",
                    )
                ],
            )
        )
        self.assertEqual(approval_store.get_request(approval.approval_id).approval_id, approval.approval_id)

        interrupt = interrupt_store.create_interrupt(
            InterruptRequest(
                interrupt_id="pg-interrupt-1",
                session_id=session.session_id,
                ticket_id=session.ticket_id,
                type="feedback",
                source="feedback",
                reason="smoke",
                question="ok?",
                expected_input_schema={},
                resume_token="token-1",
            )
        )
        self.assertEqual(interrupt_store.get_interrupt(interrupt.interrupt_id).interrupt_id, interrupt.interrupt_id)

        checkpoint = checkpoint_store.create_checkpoint(
            ExecutionCheckpoint(
                session_id=session.session_id,
                thread_id=session.thread_id,
                ticket_id=session.ticket_id,
                stage="finalize",
            )
        )
        self.assertEqual(checkpoint_store.get_checkpoint(checkpoint.checkpoint_id).checkpoint_id, checkpoint.checkpoint_id)

        plan = execution_store.create_plan(
            ExecutionPlan(
                session_id=session.session_id,
                thread_id=session.thread_id,
                ticket_id=session.ticket_id,
            )
        )
        step = execution_store.create_step(
            ExecutionStep(
                plan_id=plan.plan_id,
                session_id=session.session_id,
                action="observe_service",
                tool_name="observe_service",
            )
        )
        self.assertEqual(execution_store.get_step(step.step_id).step_id, step.step_id)

        memory = memory_store.append_entry(
            ProcessMemoryEntry(
                session_id=session.session_id,
                thread_id=session.thread_id,
                ticket_id=session.ticket_id,
                event_type="run_summary",
                stage="finalize",
                source="test",
                summary="pg memory",
            )
        )
        self.assertEqual(memory_store.list_entries(session.session_id)[0].memory_id, memory.memory_id)

        case = memory_store.upsert_case(
            IncidentCase(
                session_id=session.session_id,
                thread_id=session.thread_id,
                ticket_id=session.ticket_id,
                service="smoke-service",
                current_agent="ranker",
                symptom="smoke",
                root_cause="smoke",
                final_conclusion="done",
            )
        )
        self.assertEqual(memory_store.get_case_by_session_id(session.session_id).case_id, case.case_id)

    def test_ranker_weights_manager_works_in_postgres_mode(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            manager = RankerWeightsManager(
                str(Path(tmp_dir) / "unused-sqlite.db"),
                backend="postgres",
                postgres_dsn=self.dsn,
                auto_activate_threshold=1,
            )
            weights = manager.resolve_weights(
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
                    }
                ]
            )
            active = manager.get_active_snapshot()
            self.assertIsNotNone(active)
            self.assertEqual(active["weights"], weights)


if __name__ == "__main__":
    unittest.main()
