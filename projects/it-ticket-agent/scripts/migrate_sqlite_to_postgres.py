from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from it_ticket_agent.approval.pg_store import PostgresApprovalStoreV2
from it_ticket_agent.checkpoints.pg_store import PostgresCheckpointStoreV2
from it_ticket_agent.events.pg_store import PostgresSystemEventStore
from it_ticket_agent.execution.pg_store import PostgresExecutionStoreV2
from it_ticket_agent.interrupts.pg_store import PostgresInterruptStoreV2
from it_ticket_agent.memory.pg_store import PostgresProcessMemoryStoreV2
from it_ticket_agent.orchestration.ranker_weights import RankerWeightsManager
from it_ticket_agent.session.pg_store import PostgresSessionStore
from it_ticket_agent.storage.postgres import postgres_connection


TABLES = [
    "conversation_session",
    "conversation_turn",
    "system_event",
    "approval_request_v2",
    "approval_audit_event",
    "interrupt_request",
    "execution_checkpoint",
    "execution_plan",
    "execution_step",
    "process_memory_entry",
    "incident_case",
    "ranker_weight_snapshot",
]


def sqlite_connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def load_rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    existing = conn.execute(
        "select name from sqlite_master where type='table' and name = ?",
        (table,),
    ).fetchone()
    if existing is None:
        return []
    rows = conn.execute(f"select * from {table}").fetchall()
    return [dict(row) for row in rows]


def migrate_conversation_session(rows: list[dict], dsn: str) -> int:
    store = PostgresSessionStore(dsn)
    count = 0
    for row in rows:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into conversation_session (
                    session_id, thread_id, ticket_id, user_id, status, current_stage, current_agent,
                    incident_state_json, latest_approval_id, pending_interrupt_id, last_checkpoint_id,
                    session_memory_json, metadata_json, created_at, updated_at, last_active_at, closed_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
                on conflict (session_id) do update set
                    thread_id = excluded.thread_id,
                    ticket_id = excluded.ticket_id,
                    user_id = excluded.user_id,
                    status = excluded.status,
                    current_stage = excluded.current_stage,
                    current_agent = excluded.current_agent,
                    incident_state_json = excluded.incident_state_json,
                    latest_approval_id = excluded.latest_approval_id,
                    pending_interrupt_id = excluded.pending_interrupt_id,
                    last_checkpoint_id = excluded.last_checkpoint_id,
                    session_memory_json = excluded.session_memory_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at,
                    last_active_at = excluded.last_active_at,
                    closed_at = excluded.closed_at
                """,
                (
                    row["session_id"],
                    row["thread_id"],
                    row["ticket_id"],
                    row["user_id"],
                    row["status"],
                    row["current_stage"],
                    row.get("current_agent"),
                    row["incident_state_json"],
                    row.get("latest_approval_id"),
                    row.get("pending_interrupt_id"),
                    row.get("last_checkpoint_id"),
                    row["session_memory_json"],
                    row["metadata_json"],
                    row["created_at"],
                    row["updated_at"],
                    row["last_active_at"],
                    row.get("closed_at"),
                ),
            )
        count += 1
    return count


def migrate_conversation_turn(rows: list[dict], dsn: str) -> int:
    PostgresSessionStore(dsn)
    count = 0
    for row in rows:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into conversation_turn (
                    turn_id, session_id, role, content, structured_payload_json, created_at
                ) values (%s, %s, %s, %s, %s::jsonb, %s)
                on conflict (turn_id) do nothing
                """,
                (
                    row["turn_id"],
                    row["session_id"],
                    row["role"],
                    row["content"],
                    row["structured_payload_json"],
                    row["created_at"],
                ),
            )
        count += 1
    return count


def migrate_system_event(rows: list[dict], dsn: str) -> int:
    PostgresSystemEventStore(dsn)
    count = 0
    for row in rows:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into system_event (
                    event_id, session_id, thread_id, ticket_id, event_type, payload_json, metadata_json, created_at
                ) values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                on conflict (event_id) do nothing
                """,
                (
                    row["event_id"],
                    row["session_id"],
                    row["thread_id"],
                    row["ticket_id"],
                    row["event_type"],
                    row["payload_json"],
                    row["metadata_json"],
                    row["created_at"],
                ),
            )
        count += 1
    return count


def migrate_approval(rows_request: list[dict], rows_event: list[dict], dsn: str) -> tuple[int, int]:
    PostgresApprovalStoreV2(dsn)
    request_count = 0
    for row in rows_request:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into approval_request_v2 (
                    approval_id, ticket_id, thread_id, status, highest_risk, summary,
                    context_json, proposals_json, created_at, updated_at, decided_at, decided_by, comment
                ) values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
                on conflict (approval_id) do update set
                    status = excluded.status,
                    highest_risk = excluded.highest_risk,
                    summary = excluded.summary,
                    context_json = excluded.context_json,
                    proposals_json = excluded.proposals_json,
                    updated_at = excluded.updated_at,
                    decided_at = excluded.decided_at,
                    decided_by = excluded.decided_by,
                    comment = excluded.comment
                """,
                (
                    row["approval_id"],
                    row["ticket_id"],
                    row["thread_id"],
                    row["status"],
                    row["highest_risk"],
                    row["summary"],
                    row["context_json"],
                    row["proposals_json"],
                    row["created_at"],
                    row["updated_at"],
                    row.get("decided_at"),
                    row.get("decided_by"),
                    row.get("comment"),
                ),
            )
        request_count += 1
    event_count = 0
    for row in rows_event:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into approval_audit_event (
                    event_id, approval_id, event_type, actor_id, detail_json, created_at
                ) values (%s, %s, %s, %s, %s::jsonb, %s)
                on conflict (event_id) do nothing
                """,
                (
                    row["event_id"],
                    row["approval_id"],
                    row["event_type"],
                    row["actor_id"],
                    row["detail_json"],
                    row["created_at"],
                ),
            )
        event_count += 1
    return request_count, event_count


def migrate_interrupt(rows: list[dict], dsn: str) -> int:
    PostgresInterruptStoreV2(dsn)
    count = 0
    for row in rows:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into interrupt_request (
                    interrupt_id, session_id, ticket_id, type, source, reason, question,
                    expected_input_schema_json, status, resume_token, timeout_at, answer_payload_json,
                    metadata_json, created_at, resolved_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                on conflict (interrupt_id) do update set
                    status = excluded.status,
                    answer_payload_json = excluded.answer_payload_json,
                    metadata_json = excluded.metadata_json,
                    resolved_at = excluded.resolved_at
                """,
                (
                    row["interrupt_id"],
                    row["session_id"],
                    row["ticket_id"],
                    row["type"],
                    row["source"],
                    row["reason"],
                    row["question"],
                    row["expected_input_schema_json"],
                    row["status"],
                    row["resume_token"],
                    row.get("timeout_at"),
                    row["answer_payload_json"],
                    row["metadata_json"],
                    row["created_at"],
                    row.get("resolved_at"),
                ),
            )
        count += 1
    return count


def migrate_checkpoint(rows: list[dict], dsn: str) -> int:
    PostgresCheckpointStoreV2(dsn)
    count = 0
    for row in rows:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into execution_checkpoint (
                    checkpoint_id, session_id, thread_id, ticket_id, stage, next_action, state_snapshot_json, metadata_json, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                on conflict (checkpoint_id) do nothing
                """,
                (
                    row["checkpoint_id"],
                    row["session_id"],
                    row["thread_id"],
                    row["ticket_id"],
                    row["stage"],
                    row.get("next_action"),
                    row["state_snapshot_json"],
                    row["metadata_json"],
                    row["created_at"],
                ),
            )
        count += 1
    return count


def migrate_execution(rows_plan: list[dict], rows_step: list[dict], dsn: str) -> tuple[int, int]:
    PostgresExecutionStoreV2(dsn)
    plan_count = 0
    for row in rows_plan:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into execution_plan (
                    plan_id, session_id, thread_id, ticket_id, status, steps_json, current_step_id,
                    summary, recovery_json, metadata_json, created_at, updated_at
                ) values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                on conflict (plan_id) do update set
                    status = excluded.status,
                    steps_json = excluded.steps_json,
                    current_step_id = excluded.current_step_id,
                    summary = excluded.summary,
                    recovery_json = excluded.recovery_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    row["plan_id"],
                    row["session_id"],
                    row["thread_id"],
                    row["ticket_id"],
                    row["status"],
                    row["steps_json"],
                    row.get("current_step_id"),
                    row["summary"],
                    row["recovery_json"],
                    row["metadata_json"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        plan_count += 1
    step_count = 0
    for row in rows_step:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into execution_step (
                    step_id, plan_id, session_id, action, tool_name, params_json, sequence_no, dependencies_json,
                    retry_policy_json, compensation_json, attempt, last_error_json, status, result_summary,
                    evidence_json, metadata_json, started_at, finished_at, created_at, updated_at
                ) values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
                on conflict (step_id) do update set
                    status = excluded.status,
                    result_summary = excluded.result_summary,
                    evidence_json = excluded.evidence_json,
                    metadata_json = excluded.metadata_json,
                    attempt = excluded.attempt,
                    last_error_json = excluded.last_error_json,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    updated_at = excluded.updated_at
                """,
                (
                    row["step_id"],
                    row["plan_id"],
                    row["session_id"],
                    row["action"],
                    row["tool_name"],
                    row["params_json"],
                    row["sequence_no"],
                    row["dependencies_json"],
                    row["retry_policy_json"],
                    row.get("compensation_json"),
                    row["attempt"],
                    row["last_error_json"],
                    row["status"],
                    row["result_summary"],
                    row["evidence_json"],
                    row["metadata_json"],
                    row.get("started_at"),
                    row.get("finished_at"),
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        step_count += 1
    return plan_count, step_count


def migrate_process_memory(rows_memory: list[dict], rows_case: list[dict], dsn: str) -> tuple[int, int]:
    PostgresProcessMemoryStoreV2(dsn)
    memory_count = 0
    for row in rows_memory:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into process_memory_entry (
                    memory_id, session_id, thread_id, ticket_id, event_type, stage, source, summary, payload_json, refs_json, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                on conflict (memory_id) do nothing
                """,
                (
                    row["memory_id"],
                    row["session_id"],
                    row["thread_id"],
                    row["ticket_id"],
                    row["event_type"],
                    row["stage"],
                    row["source"],
                    row["summary"],
                    row["payload_json"],
                    row["refs_json"],
                    row["created_at"],
                ),
            )
        memory_count += 1
    case_count = 0
    for row in rows_case:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into incident_case (
                    case_id, session_id, thread_id, ticket_id, service, cluster, namespace, current_agent, symptom,
                    root_cause, key_evidence_json, final_action, approval_required, verification_passed, human_verified,
                    hypothesis_accuracy_json, actual_root_cause_hypothesis, selected_hypothesis_id, selected_ranker_features_json,
                    final_conclusion, created_at, updated_at, closed_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s, %s)
                on conflict (session_id) do update set
                    thread_id = excluded.thread_id,
                    ticket_id = excluded.ticket_id,
                    service = excluded.service,
                    cluster = excluded.cluster,
                    namespace = excluded.namespace,
                    current_agent = excluded.current_agent,
                    symptom = excluded.symptom,
                    root_cause = excluded.root_cause,
                    key_evidence_json = excluded.key_evidence_json,
                    final_action = excluded.final_action,
                    approval_required = excluded.approval_required,
                    verification_passed = excluded.verification_passed,
                    human_verified = excluded.human_verified,
                    hypothesis_accuracy_json = excluded.hypothesis_accuracy_json,
                    actual_root_cause_hypothesis = excluded.actual_root_cause_hypothesis,
                    selected_hypothesis_id = excluded.selected_hypothesis_id,
                    selected_ranker_features_json = excluded.selected_ranker_features_json,
                    final_conclusion = excluded.final_conclusion,
                    updated_at = excluded.updated_at,
                    closed_at = excluded.closed_at
                """,
                (
                    row["case_id"],
                    row["session_id"],
                    row["thread_id"],
                    row["ticket_id"],
                    row["service"],
                    row["cluster"],
                    row["namespace"],
                    row["current_agent"],
                    row["symptom"],
                    row["root_cause"],
                    row["key_evidence_json"],
                    row["final_action"],
                    bool(row["approval_required"]),
                    None if row["verification_passed"] is None else bool(row["verification_passed"]),
                    bool(row["human_verified"]),
                    row["hypothesis_accuracy_json"],
                    row["actual_root_cause_hypothesis"],
                    row["selected_hypothesis_id"],
                    row["selected_ranker_features_json"],
                    row["final_conclusion"],
                    row["created_at"],
                    row["updated_at"],
                    row.get("closed_at"),
                ),
            )
        case_count += 1
    return memory_count, case_count


def migrate_ranker_weights(rows: list[dict], db_path: str, dsn: str) -> int:
    manager = RankerWeightsManager(db_path, backend="postgres", postgres_dsn=dsn)
    count = 0
    for row in rows:
        with postgres_connection(dsn) as conn:
            conn.execute(
                """
                insert into ranker_weight_snapshot (
                    version_id, weights_json, sample_count, strategy, is_active, metadata_json, created_at
                ) values (%s, %s::jsonb, %s, %s, %s, %s::jsonb, %s)
                on conflict (version_id) do update set
                    weights_json = excluded.weights_json,
                    sample_count = excluded.sample_count,
                    strategy = excluded.strategy,
                    is_active = excluded.is_active,
                    metadata_json = excluded.metadata_json,
                    created_at = excluded.created_at
                """,
                (
                    row["version_id"],
                    row["weights_json"],
                    row["sample_count"],
                    row["strategy"],
                    bool(row["is_active"]),
                    row["metadata_json"],
                    row["created_at"],
                ),
            )
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate IT Ticket Agent state from SQLite to Postgres.")
    parser.add_argument("--sqlite-path", required=True, help="Path to the source SQLite database")
    parser.add_argument("--postgres-dsn", required=True, help="Postgres DSN")
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")

    with sqlite_connect(str(sqlite_path)) as conn:
        session_count = migrate_conversation_session(load_rows(conn, "conversation_session"), args.postgres_dsn)
        turn_count = migrate_conversation_turn(load_rows(conn, "conversation_turn"), args.postgres_dsn)
        event_count = migrate_system_event(load_rows(conn, "system_event"), args.postgres_dsn)
        approval_count, approval_event_count = migrate_approval(
            load_rows(conn, "approval_request_v2"),
            load_rows(conn, "approval_audit_event"),
            args.postgres_dsn,
        )
        interrupt_count = migrate_interrupt(load_rows(conn, "interrupt_request"), args.postgres_dsn)
        checkpoint_count = migrate_checkpoint(load_rows(conn, "execution_checkpoint"), args.postgres_dsn)
        plan_count, step_count = migrate_execution(
            load_rows(conn, "execution_plan"),
            load_rows(conn, "execution_step"),
            args.postgres_dsn,
        )
        memory_count, case_count = migrate_process_memory(
            load_rows(conn, "process_memory_entry"),
            load_rows(conn, "incident_case"),
            args.postgres_dsn,
        )
        ranker_count = migrate_ranker_weights(load_rows(conn, "ranker_weight_snapshot"), str(sqlite_path), args.postgres_dsn)

    summary = {
        "conversation_session": session_count,
        "conversation_turn": turn_count,
        "system_event": event_count,
        "approval_request_v2": approval_count,
        "approval_audit_event": approval_event_count,
        "interrupt_request": interrupt_count,
        "execution_checkpoint": checkpoint_count,
        "execution_plan": plan_count,
        "execution_step": step_count,
        "process_memory_entry": memory_count,
        "incident_case": case_count,
        "ranker_weight_snapshot": ranker_count,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
