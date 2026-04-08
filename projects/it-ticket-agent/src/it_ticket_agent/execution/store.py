from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

from ..session.models import utc_now
from .models import (
    ExecutionCompensationPolicy,
    ExecutionPlan,
    ExecutionRecoveryMetadata,
    ExecutionRetryPolicy,
    ExecutionStep,
)


class ExecutionStoreV2:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        folder = os.path.dirname(db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists execution_plan (
                    plan_id text primary key,
                    session_id text not null,
                    thread_id text not null,
                    ticket_id text not null,
                    status text not null,
                    steps_json text not null,
                    current_step_id text,
                    summary text not null,
                    recovery_json text not null,
                    metadata_json text not null,
                    created_at text not null,
                    updated_at text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists execution_step (
                    step_id text primary key,
                    plan_id text not null,
                    session_id text not null,
                    action text not null,
                    tool_name text not null,
                    params_json text not null,
                    sequence_no integer not null default 0,
                    dependencies_json text not null default '[]',
                    retry_policy_json text not null default '{}',
                    compensation_json text,
                    attempt integer not null default 0,
                    last_error_json text not null default '{}',
                    status text not null,
                    result_summary text not null,
                    evidence_json text not null,
                    metadata_json text not null,
                    started_at text,
                    finished_at text,
                    created_at text not null,
                    updated_at text not null,
                    foreign key (plan_id) references execution_plan (plan_id)
                )
                """
            )

            plan_columns = {row["name"] for row in conn.execute("pragma table_info(execution_plan)").fetchall()}
            if "current_step_id" not in plan_columns:
                conn.execute("alter table execution_plan add column current_step_id text")
            if "recovery_json" not in plan_columns:
                conn.execute("alter table execution_plan add column recovery_json text not null default '{}'")

            step_columns = {row["name"] for row in conn.execute("pragma table_info(execution_step)").fetchall()}
            if "sequence_no" not in step_columns:
                conn.execute("alter table execution_step add column sequence_no integer not null default 0")
            if "dependencies_json" not in step_columns:
                conn.execute("alter table execution_step add column dependencies_json text not null default '[]'")
            if "retry_policy_json" not in step_columns:
                conn.execute("alter table execution_step add column retry_policy_json text not null default '{}'")
            if "compensation_json" not in step_columns:
                conn.execute("alter table execution_step add column compensation_json text")
            if "attempt" not in step_columns:
                conn.execute("alter table execution_step add column attempt integer not null default 0")
            if "last_error_json" not in step_columns:
                conn.execute("alter table execution_step add column last_error_json text not null default '{}'")

            conn.execute(
                """
                create index if not exists idx_execution_plan_session_created_at
                on execution_plan (session_id, created_at, plan_id)
                """
            )
            conn.execute(
                """
                create index if not exists idx_execution_step_plan_created_at
                on execution_step (plan_id, sequence_no, created_at, step_id)
                """
            )
            conn.commit()

    def create_plan(self, plan: ExecutionPlan) -> ExecutionPlan:
        payload = plan.model_copy(update={"created_at": utc_now(), "updated_at": utc_now()})
        with self._connect() as conn:
            conn.execute(
                """
                insert into execution_plan (
                    plan_id, session_id, thread_id, ticket_id, status, steps_json,
                    current_step_id, summary, recovery_json, metadata_json, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.plan_id,
                    payload.session_id,
                    payload.thread_id,
                    payload.ticket_id,
                    payload.status,
                    json.dumps(payload.steps, ensure_ascii=False),
                    payload.current_step_id,
                    payload.summary,
                    json.dumps(payload.recovery.model_dump(), ensure_ascii=False),
                    json.dumps(payload.metadata, ensure_ascii=False),
                    payload.created_at,
                    payload.updated_at,
                ),
            )
            conn.commit()
        return payload

    def update_plan(
        self,
        plan_id: str,
        *,
        status: str | None = None,
        steps: list[str] | None = None,
        current_step_id: str | None = None,
        summary: str | None = None,
        recovery: ExecutionRecoveryMetadata | dict | None = None,
        metadata: dict | None = None,
    ) -> Optional[ExecutionPlan]:
        existing = self.get_plan(plan_id)
        if existing is None:
            return None
        next_recovery = existing.recovery if recovery is None else (
            recovery if isinstance(recovery, ExecutionRecoveryMetadata) else ExecutionRecoveryMetadata.model_validate(recovery)
        )
        next_plan = existing.model_copy(
            update={
                "status": status or existing.status,
                "steps": list(steps) if steps is not None else list(existing.steps),
                "current_step_id": existing.current_step_id if current_step_id is None else current_step_id,
                "summary": existing.summary if summary is None else summary,
                "recovery": next_recovery,
                "metadata": dict(existing.metadata) if metadata is None else dict(metadata),
                "updated_at": utc_now(),
            }
        )
        with self._connect() as conn:
            conn.execute(
                """
                update execution_plan
                set status = ?, steps_json = ?, current_step_id = ?, summary = ?, recovery_json = ?, metadata_json = ?, updated_at = ?
                where plan_id = ?
                """,
                (
                    next_plan.status,
                    json.dumps(next_plan.steps, ensure_ascii=False),
                    next_plan.current_step_id,
                    next_plan.summary,
                    json.dumps(next_plan.recovery.model_dump(), ensure_ascii=False),
                    json.dumps(next_plan.metadata, ensure_ascii=False),
                    next_plan.updated_at,
                    plan_id,
                ),
            )
            conn.commit()
        return next_plan

    def get_plan(self, plan_id: str) -> Optional[ExecutionPlan]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select plan_id, session_id, thread_id, ticket_id, status, steps_json,
                       current_step_id, summary, recovery_json, metadata_json, created_at, updated_at
                from execution_plan
                where plan_id = ?
                """,
                (plan_id,),
            ).fetchone()
        return self._row_to_plan(row)

    def list_plans(self, session_id: str) -> list[ExecutionPlan]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select plan_id, session_id, thread_id, ticket_id, status, steps_json,
                       current_step_id, summary, recovery_json, metadata_json, created_at, updated_at
                from execution_plan
                where session_id = ?
                order by created_at asc, plan_id asc
                """,
                (session_id,),
            ).fetchall()
        return [plan for row in rows if (plan := self._row_to_plan(row)) is not None]

    def create_step(self, step: ExecutionStep) -> ExecutionStep:
        payload = step.model_copy(update={"created_at": utc_now(), "updated_at": utc_now()})
        with self._connect() as conn:
            conn.execute(
                """
                insert into execution_step (
                    step_id, plan_id, session_id, action, tool_name, params_json, sequence_no,
                    dependencies_json, retry_policy_json, compensation_json, attempt, last_error_json,
                    status, result_summary, evidence_json, metadata_json, started_at,
                    finished_at, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.step_id,
                    payload.plan_id,
                    payload.session_id,
                    payload.action,
                    payload.tool_name,
                    json.dumps(payload.params, ensure_ascii=False),
                    payload.sequence,
                    json.dumps(payload.dependencies, ensure_ascii=False),
                    json.dumps(payload.retry_policy.model_dump(), ensure_ascii=False),
                    json.dumps(payload.compensation.model_dump(), ensure_ascii=False) if payload.compensation is not None else None,
                    payload.attempt,
                    json.dumps(payload.last_error, ensure_ascii=False),
                    payload.status,
                    payload.result_summary,
                    json.dumps(payload.evidence, ensure_ascii=False),
                    json.dumps(payload.metadata, ensure_ascii=False),
                    payload.started_at,
                    payload.finished_at,
                    payload.created_at,
                    payload.updated_at,
                ),
            )
            conn.commit()
        return payload

    def update_step(
        self,
        step_id: str,
        *,
        status: str | None = None,
        result_summary: str | None = None,
        evidence: list[str] | None = None,
        metadata: dict | None = None,
        attempt: int | None = None,
        last_error: dict | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> Optional[ExecutionStep]:
        existing = self.get_step(step_id)
        if existing is None:
            return None
        next_step = existing.model_copy(
            update={
                "status": status or existing.status,
                "result_summary": existing.result_summary if result_summary is None else result_summary,
                "evidence": list(existing.evidence) if evidence is None else list(evidence),
                "metadata": dict(existing.metadata) if metadata is None else dict(metadata),
                "attempt": existing.attempt if attempt is None else attempt,
                "last_error": dict(existing.last_error) if last_error is None else dict(last_error),
                "started_at": existing.started_at if started_at is None else started_at,
                "finished_at": existing.finished_at if finished_at is None else finished_at,
                "updated_at": utc_now(),
            }
        )
        with self._connect() as conn:
            conn.execute(
                """
                update execution_step
                set status = ?, result_summary = ?, evidence_json = ?, metadata_json = ?,
                    attempt = ?, last_error_json = ?, started_at = ?, finished_at = ?, updated_at = ?
                where step_id = ?
                """,
                (
                    next_step.status,
                    next_step.result_summary,
                    json.dumps(next_step.evidence, ensure_ascii=False),
                    json.dumps(next_step.metadata, ensure_ascii=False),
                    next_step.attempt,
                    json.dumps(next_step.last_error, ensure_ascii=False),
                    next_step.started_at,
                    next_step.finished_at,
                    next_step.updated_at,
                    step_id,
                ),
            )
            conn.commit()
        return next_step

    def get_step(self, step_id: str) -> Optional[ExecutionStep]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select step_id, plan_id, session_id, action, tool_name, params_json, sequence_no,
                       dependencies_json, retry_policy_json, compensation_json, attempt, last_error_json, status,
                       result_summary, evidence_json, metadata_json, started_at, finished_at,
                       created_at, updated_at
                from execution_step
                where step_id = ?
                """,
                (step_id,),
            ).fetchone()
        return self._row_to_step(row)

    def list_steps(self, plan_id: str) -> list[ExecutionStep]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select step_id, plan_id, session_id, action, tool_name, params_json, sequence_no,
                       dependencies_json, retry_policy_json, compensation_json, attempt, last_error_json, status,
                       result_summary, evidence_json, metadata_json, started_at, finished_at,
                       created_at, updated_at
                from execution_step
                where plan_id = ?
                order by sequence_no asc, created_at asc, step_id asc
                """,
                (plan_id,),
            ).fetchall()
        return [step for row in rows if (step := self._row_to_step(row)) is not None]

    @staticmethod
    def _row_to_plan(row: sqlite3.Row | None) -> Optional[ExecutionPlan]:
        if row is None:
            return None
        return ExecutionPlan(
            plan_id=row["plan_id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            ticket_id=row["ticket_id"],
            status=row["status"],
            steps=json.loads(row["steps_json"]),
            current_step_id=row["current_step_id"],
            summary=row["summary"],
            recovery=ExecutionRecoveryMetadata.model_validate(json.loads(row["recovery_json"] or "{}")),
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_step(row: sqlite3.Row | None) -> Optional[ExecutionStep]:
        if row is None:
            return None
        return ExecutionStep(
            step_id=row["step_id"],
            plan_id=row["plan_id"],
            session_id=row["session_id"],
            action=row["action"],
            tool_name=row["tool_name"],
            params=json.loads(row["params_json"]),
            sequence=int(row["sequence_no"] or 0),
            dependencies=json.loads(row["dependencies_json"] or "[]"),
            retry_policy=ExecutionRetryPolicy.model_validate(json.loads(row["retry_policy_json"] or "{}")),
            compensation=(
                ExecutionCompensationPolicy.model_validate(json.loads(row["compensation_json"]))
                if row["compensation_json"] not in (None, "")
                else None
            ),
            attempt=int(row["attempt"] or 0),
            last_error=json.loads(row["last_error_json"] or "{}"),
            status=row["status"],
            result_summary=row["result_summary"],
            evidence=json.loads(row["evidence_json"]),
            metadata=json.loads(row["metadata_json"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
