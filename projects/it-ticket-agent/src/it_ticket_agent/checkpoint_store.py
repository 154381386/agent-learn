from __future__ import annotations

from typing import Any, Optional

from .checkpoints import CheckpointStoreV2, ExecutionCheckpoint


class CheckpointStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.v2_store = CheckpointStoreV2(db_path)

    def create(self, checkpoint: ExecutionCheckpoint | dict[str, Any]) -> dict[str, Any]:
        record = checkpoint if isinstance(checkpoint, ExecutionCheckpoint) else ExecutionCheckpoint.model_validate(checkpoint)
        saved = self.v2_store.create_checkpoint(record)
        return saved.model_dump()

    def get(self, checkpoint_id: str) -> Optional[dict[str, Any]]:
        record = self.v2_store.get_checkpoint(checkpoint_id)
        return None if record is None else record.model_dump()

    def get_latest(self, session_id: str) -> Optional[dict[str, Any]]:
        record = self.v2_store.get_latest_checkpoint(session_id)
        return None if record is None else record.model_dump()
