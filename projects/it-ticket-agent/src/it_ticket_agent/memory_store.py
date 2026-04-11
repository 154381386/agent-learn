from __future__ import annotations

from typing import Any, Optional

from .memory import IncidentCase, ProcessMemoryEntry, ProcessMemoryStoreV2


class ProcessMemoryStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.v2_store = ProcessMemoryStoreV2(db_path)

    def append(self, entry: ProcessMemoryEntry | dict[str, Any]) -> dict[str, Any]:
        record = entry if isinstance(entry, ProcessMemoryEntry) else ProcessMemoryEntry.model_validate(entry)
        saved = self.v2_store.append_entry(record)
        return saved.model_dump()

    def list_entries(self, session_id: str, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        return [record.model_dump() for record in self.v2_store.list_entries(session_id, limit=limit)]

    def summarize(self, session_id: str, *, limit: int = 20) -> dict[str, Any]:
        return self.v2_store.summarize(session_id, limit=limit).model_dump()


class IncidentCaseStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.v2_store = ProcessMemoryStoreV2(db_path)

    def upsert(self, case: IncidentCase | dict[str, Any]) -> dict[str, Any]:
        record = case if isinstance(case, IncidentCase) else IncidentCase.model_validate(case)
        saved = self.v2_store.upsert_case(record)
        return saved.model_dump()

    def get(self, case_id: str) -> Optional[dict[str, Any]]:
        record = self.v2_store.get_case(case_id)
        return None if record is None else record.model_dump()

    def get_by_session_id(self, session_id: str) -> Optional[dict[str, Any]]:
        record = self.v2_store.get_case_by_session_id(session_id)
        return None if record is None else record.model_dump()

    def update_feedback(
        self,
        session_id: str,
        *,
        human_verified: bool,
        hypothesis_accuracy: dict[str, float] | None = None,
        actual_root_cause_hypothesis: str | None = None,
    ) -> Optional[dict[str, Any]]:
        record = self.v2_store.get_case_by_session_id(session_id)
        if record is None:
            return None
        updated = record.model_copy(
            update={
                "human_verified": human_verified,
                "hypothesis_accuracy": dict(hypothesis_accuracy or record.hypothesis_accuracy),
                "actual_root_cause_hypothesis": (
                    str(actual_root_cause_hypothesis or record.actual_root_cause_hypothesis)
                ),
            }
        )
        saved = self.v2_store.upsert_case(updated)
        return saved.model_dump()

    def list_cases(
        self,
        *,
        service: str | None = None,
        final_action: str | None = None,
        approval_required: bool | None = None,
        verification_passed: bool | None = None,
        keyword: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return [
            record.model_dump()
            for record in self.v2_store.list_cases(
                service=service,
                final_action=final_action,
                approval_required=approval_required,
                verification_passed=verification_passed,
                keyword=keyword,
                limit=limit,
            )
        ]
