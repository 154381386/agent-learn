from __future__ import annotations

from typing import Any, Optional

from .bad_cases import BadCaseCandidate, BadCaseCandidateStoreV2
from .bad_cases.pg_store import PostgresBadCaseCandidateStoreV2


class BadCaseCandidateStore:
    def __init__(
        self,
        db_path: str,
        *,
        backend: BadCaseCandidateStoreV2 | PostgresBadCaseCandidateStoreV2 | None = None,
    ) -> None:
        self.db_path = db_path
        self.v2_store = backend or BadCaseCandidateStoreV2(db_path)

    def create(self, candidate: BadCaseCandidate | dict[str, Any]) -> dict[str, Any]:
        record = candidate if isinstance(candidate, BadCaseCandidate) else BadCaseCandidate.model_validate(candidate)
        saved = self.v2_store.create_candidate(record)
        return saved.model_dump()

    def get(self, candidate_id: str) -> Optional[dict[str, Any]]:
        record = self.v2_store.get_candidate(candidate_id)
        return None if record is None else record.model_dump()

    def list_candidates(
        self,
        *,
        session_id: str | None = None,
        source: str | None = None,
        export_status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return [
            record.model_dump()
            for record in self.v2_store.list_candidates(
                session_id=session_id,
                source=source,
                export_status=export_status,
                limit=limit,
            )
        ]

    def update_export_status(
        self,
        candidate_id: str,
        *,
        export_status: str,
        export_metadata: dict[str, Any] | None = None,
    ) -> Optional[dict[str, Any]]:
        record = self.v2_store.update_export_status(
            candidate_id,
            export_status=export_status,
            export_metadata=export_metadata,
        )
        return None if record is None else record.model_dump()
