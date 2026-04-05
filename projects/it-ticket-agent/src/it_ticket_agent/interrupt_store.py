from __future__ import annotations

from typing import Any, Optional

from .interrupts import InterruptRequest, InterruptService, InterruptStoreV2


class InterruptStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.v2_store = InterruptStoreV2(db_path)
        self.service = InterruptService(self.v2_store)

    def get(self, interrupt_id: str) -> Optional[dict[str, Any]]:
        record = self.service.get_interrupt(interrupt_id)
        return None if record is None else record.model_dump()

    def get_pending(
        self,
        *,
        session_id: str | None = None,
        ticket_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return [record.model_dump() for record in self.service.get_pending_interrupts(session_id=session_id, ticket_id=ticket_id)]

    def create_approval_interrupt(
        self,
        *,
        session_id: str,
        ticket_id: str,
        reason: str,
        question: str,
        expected_input_schema: dict[str, Any],
        timeout_at: str | None = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        record = self.service.create_approval_interrupt(
            session_id=session_id,
            ticket_id=ticket_id,
            reason=reason,
            question=question,
            expected_input_schema=expected_input_schema,
            timeout_at=timeout_at,
            metadata=metadata,
        )
        return record.model_dump()

    def answer(self, interrupt_id: str, *, answer_payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        record = self.service.answer_interrupt(interrupt_id, answer_payload=answer_payload)
        return None if record is None else record.model_dump()

    def cancel(self, interrupt_id: str, *, answer_payload: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        record = self.service.cancel_interrupt(interrupt_id, answer_payload=answer_payload or {})
        return None if record is None else record.model_dump()

    def expire(self, interrupt_id: str, *, answer_payload: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        record = self.service.expire_interrupt(interrupt_id, answer_payload=answer_payload or {})
        return None if record is None else record.model_dump()
