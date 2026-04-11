from __future__ import annotations

import json
from typing import Optional

from .models import ExecutionCheckpoint
from ..session.models import utc_now
from ..storage.postgres import postgres_connection


class PostgresCheckpointStoreV2:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._init_db()

    def _init_db(self) -> None:
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                create table if not exists execution_checkpoint (
                    checkpoint_id text primary key,
                    session_id text not null,
                    thread_id text not null,
                    ticket_id text not null,
                    stage text not null,
                    next_action text,
                    state_snapshot_json jsonb not null,
                    metadata_json jsonb not null,
                    created_at timestamptz not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_execution_checkpoint_session_created_at
                on execution_checkpoint (session_id, created_at desc, checkpoint_id desc)
                """
            )

    def create_checkpoint(self, checkpoint: ExecutionCheckpoint) -> ExecutionCheckpoint:
        payload = checkpoint.model_copy(update={"created_at": utc_now()})
        with postgres_connection(self.dsn) as conn:
            conn.execute(
                """
                insert into execution_checkpoint (
                    checkpoint_id, session_id, thread_id, ticket_id, stage,
                    next_action, state_snapshot_json, metadata_json, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    payload.checkpoint_id,
                    payload.session_id,
                    payload.thread_id,
                    payload.ticket_id,
                    payload.stage,
                    payload.next_action,
                    json.dumps(payload.state_snapshot, ensure_ascii=False),
                    json.dumps(payload.metadata, ensure_ascii=False),
                    payload.created_at,
                ),
            )
        return payload

    def get_checkpoint(self, checkpoint_id: str) -> Optional[ExecutionCheckpoint]:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                """
                select checkpoint_id, session_id, thread_id, ticket_id, stage,
                       next_action, state_snapshot_json, metadata_json, created_at
                from execution_checkpoint
                where checkpoint_id = %s
                """,
                (checkpoint_id,),
            ).fetchone()
        return self._row_to_checkpoint(row)

    def get_latest_checkpoint(self, session_id: str) -> Optional[ExecutionCheckpoint]:
        with postgres_connection(self.dsn) as conn:
            row = conn.execute(
                """
                select checkpoint_id, session_id, thread_id, ticket_id, stage,
                       next_action, state_snapshot_json, metadata_json, created_at
                from execution_checkpoint
                where session_id = %s
                order by created_at desc, checkpoint_id desc
                limit 1
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_checkpoint(row)

    def list_checkpoints(self, session_id: str, limit: int = 20) -> list[ExecutionCheckpoint]:
        with postgres_connection(self.dsn) as conn:
            rows = conn.execute(
                """
                select checkpoint_id, session_id, thread_id, ticket_id, stage,
                       next_action, state_snapshot_json, metadata_json, created_at
                from execution_checkpoint
                where session_id = %s
                order by created_at desc, checkpoint_id desc
                limit %s
                """,
                (session_id, limit),
            ).fetchall()
        return [checkpoint for row in rows if (checkpoint := self._row_to_checkpoint(row)) is not None]

    @staticmethod
    def _row_to_checkpoint(row: dict | None) -> Optional[ExecutionCheckpoint]:
        if row is None:
            return None
        return ExecutionCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            ticket_id=row["ticket_id"],
            stage=row["stage"],
            next_action=row["next_action"],
            state_snapshot=row["state_snapshot_json"],
            metadata=row["metadata_json"],
            created_at=str(row["created_at"]),
        )
