from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

from .models import ExecutionCheckpoint
from ..session.models import utc_now


class CheckpointStoreV2:
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
                create table if not exists execution_checkpoint (
                    checkpoint_id text primary key,
                    session_id text not null,
                    thread_id text not null,
                    ticket_id text not null,
                    stage text not null,
                    next_action text,
                    state_snapshot_json text not null,
                    metadata_json text not null,
                    created_at text not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_execution_checkpoint_session_created_at
                on execution_checkpoint (session_id, created_at, checkpoint_id)
                """
            )
            conn.commit()

    def create_checkpoint(self, checkpoint: ExecutionCheckpoint) -> ExecutionCheckpoint:
        payload = checkpoint.model_copy(update={"created_at": utc_now()})
        with self._connect() as conn:
            conn.execute(
                """
                insert into execution_checkpoint (
                    checkpoint_id, session_id, thread_id, ticket_id, stage,
                    next_action, state_snapshot_json, metadata_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            conn.commit()
        return payload

    def get_checkpoint(self, checkpoint_id: str) -> Optional[ExecutionCheckpoint]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select checkpoint_id, session_id, thread_id, ticket_id, stage,
                       next_action, state_snapshot_json, metadata_json, created_at
                from execution_checkpoint
                where checkpoint_id = ?
                """,
                (checkpoint_id,),
            ).fetchone()
        return self._row_to_checkpoint(row)

    def get_latest_checkpoint(self, session_id: str) -> Optional[ExecutionCheckpoint]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select checkpoint_id, session_id, thread_id, ticket_id, stage,
                       next_action, state_snapshot_json, metadata_json, created_at
                from execution_checkpoint
                where session_id = ?
                order by created_at desc, checkpoint_id desc
                limit 1
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_checkpoint(row)

    @staticmethod
    def _row_to_checkpoint(row: sqlite3.Row | None) -> Optional[ExecutionCheckpoint]:
        if row is None:
            return None
        return ExecutionCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            ticket_id=row["ticket_id"],
            stage=row["stage"],
            next_action=row["next_action"],
            state_snapshot=json.loads(row["state_snapshot_json"]),
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
        )
