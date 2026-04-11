from __future__ import annotations

import json
from typing import Optional

from ..session.models import utc_now
from ..storage.postgres import postgres_connection
from .models import (
    ExecutionCompensationPolicy,
    ExecutionPlan,
    ExecutionRecoveryMetadata,
    ExecutionStep,
)


class PostgresExecutionStoreV2:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._init_db()

    def _init_db(self) -> None:
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                create table if not exists execution_plan (
                    plan_id text primary key,
                    session_id text not null,
                    thread_id text not null,
                    ticket_id text not null,
                    status text not null,
                    steps_json jsonb not null,
                    current_step_id text,
                    summary text not null,
                    recovery_json jsonb not null,
                    metadata_json jsonb not null,
                    created_at timestamptz not null,
                    updated_at timestamptz not null
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
                    params_json jsonb not null,
                    sequence_no integer not null default 0,
                    dependencies_json jsonb not null default '[]'::jsonb,
                    retry_policy_json jsonb not null default '{}'::jsonb,
                    compensation_json jsonb,
                    attempt integer not null default 0,
                    last_error_json jsonb not null default '{}'::jsonb,
                    status text not null,
                    result_summary text not null,
                    evidence_json jsonb not null,
                    metadata_json jsonb not null,
                    started_at timestamptz,
                    finished_at timestamptz,
                    created_at timestamptz not null,
                    updated_at timestamptz not null
                )
                """
            )
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

    def create_plan(self, plan: ExecutionPlan) -> ExecutionPlan:
        payload = plan.model_copy(update={"created_at": utc_now(), "updated_at": utc_now()})
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                insert into execution_plan (
                    plan_id, session_id, thread_id, ticket_id, status, steps_json,
                    current_step_id, summary, recovery_json, metadata_json, created_at, updated_at
                ) values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
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
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                update execution_plan
                set status = %s, steps_json = %s::jsonb, current_step_id = %s, summary = %s,
                    recovery_json = %s::jsonb, metadata_json = %s::jsonb, updated_at = %s
                where plan_id = %s
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
        return next_plan

    def get_plan(self, plan_id: str) -> Optional[ExecutionPlan]:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                """
                select plan_id, session_id, thread_id, ticket_id, status, steps_json,
                       current_step_id, summary, recovery_json, metadata_json, created_at, updated_at
                from execution_plan
                where plan_id = %s
                """,
                (plan_id,),
            ).fetchone()
        return self._row_to_plan(row)

    def list_plans(self, session_id: str) -> list[ExecutionPlan]:
        with postgres_connection(self.dsn) as conn:
            rows = conn.execute(
                """
                select plan_id, session_id, thread_id, ticket_id, status, steps_json,
                       current_step_id, summary, recovery_json, metadata_json, created_at, updated_at
                from execution_plan
                where session_id = %s
                order by created_at asc, plan_id asc
                """,
                (session_id,),
            ).fetchall()
        return [plan for row in rows if (plan := self._row_to_plan(row)) is not None]

    def create_step(self, step: ExecutionStep) -> ExecutionStep:
        payload = step.model_copy(update={"created_at": utc_now(), "updated_at": utc_now()})
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                insert into execution_step (
                    step_id, plan_id, session_id, action, tool_name, params_json, sequence_no,
                    dependencies_json, retry_policy_json, compensation_json, attempt, last_error_json,
                    status, result_summary, evidence_json, metadata_json, started_at, finished_at, created_at, updated_at
                ) values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
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
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                update execution_step
                set status = %s, result_summary = %s, evidence_json = %s::jsonb, metadata_json = %s::jsonb,
                    attempt = %s, last_error_json = %s::jsonb, started_at = %s, finished_at = %s, updated_at = %s
                where step_id = %s
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
        return next_step

    def get_step(self, step_id: str) -> Optional[ExecutionStep]:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                """
                select step_id, plan_id, session_id, action, tool_name, params_json, sequence_no,
                       dependencies_json, retry_policy_json, compensation_json, attempt, last_error_json, status,
                       result_summary, evidence_json, metadata_json, started_at, finished_at, created_at, updated_at
                from execution_step
                where step_id = %s
                """,
                (step_id,),
            ).fetchone()
        return self._row_to_step(row)

    def list_steps(self, plan_id: str) -> list[ExecutionStep]:
        with postgres_connection(self.dsn) as conn:
            rows = conn.execute(
                """
                select step_id, plan_id, session_id, action, tool_name, params_json, sequence_no,
                       dependencies_json, retry_policy_json, compensation_json, attempt, last_error_json, status,
                       result_summary, evidence_json, metadata_json, started_at, finished_at, created_at, updated_at
                from execution_step
                where plan_id = %s
                order by sequence_no asc, created_at asc, step_id asc
                """,
                (plan_id,),
            ).fetchall()
        return [step for row in rows if (step := self._row_to_step(row)) is not None]

    @staticmethod
    def _row_to_plan(row: dict | None) -> Optional[ExecutionPlan]:
        if row is None:
            return None
        return ExecutionPlan(
            plan_id=row["plan_id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            ticket_id=row["ticket_id"],
            status=row["status"],
            steps=row["steps_json"],
            current_step_id=row["current_step_id"],
            summary=row["summary"],
            recovery=ExecutionRecoveryMetadata.model_validate(row["recovery_json"] or {}),
            metadata=row["metadata_json"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_step(row: dict | None) -> Optional[ExecutionStep]:
        if row is None:
            return None
        compensation = row["compensation_json"]
        return ExecutionStep(
            step_id=row["step_id"],
            plan_id=row["plan_id"],
            session_id=row["session_id"],
            action=row["action"],
            tool_name=row["tool_name"],
            params=row["params_json"],
            sequence=row["sequence_no"],
            dependencies=row["dependencies_json"],
            retry_policy=row["retry_policy_json"],
            compensation=ExecutionCompensationPolicy.model_validate(compensation) if compensation is not None else None,
            attempt=row["attempt"],
            last_error=row["last_error_json"],
            status=row["status"],
            result_summary=row["result_summary"],
            evidence=row["evidence_json"],
            metadata=row["metadata_json"],
            started_at=str(row["started_at"]) if row["started_at"] is not None else None,
            finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
