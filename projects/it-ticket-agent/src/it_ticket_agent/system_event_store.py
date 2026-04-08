from __future__ import annotations

from typing import Any

from .events import SystemEvent, SystemEventStoreV2


class SystemEventStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.v2_store = SystemEventStoreV2(db_path)

    def create(self, event: SystemEvent | dict[str, Any]) -> dict[str, Any]:
        record = event if isinstance(event, SystemEvent) else SystemEvent.model_validate(event)
        saved = self.v2_store.create_event(record)
        return saved.model_dump()

    def list_for_session(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return [record.model_dump() for record in self.v2_store.list_events(session_id, limit=limit)]
