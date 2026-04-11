from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from .models import InterruptRequest
from .store import InterruptStoreV2


class InterruptService:
    def __init__(self, store: InterruptStoreV2) -> None:
        self.store = store

    def create_clarification_interrupt(
        self,
        *,
        session_id: str,
        ticket_id: str,
        reason: str,
        question: str,
        expected_input_schema: dict[str, Any],
        timeout_at: str | None = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> InterruptRequest:
        return self._create_interrupt(
            session_id=session_id,
            ticket_id=ticket_id,
            interrupt_type="clarification",
            source="clarification",
            reason=reason,
            question=question,
            expected_input_schema=expected_input_schema,
            timeout_at=timeout_at,
            metadata=metadata,
        )

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
    ) -> InterruptRequest:
        return self._create_interrupt(
            session_id=session_id,
            ticket_id=ticket_id,
            interrupt_type="approval",
            source="approval",
            reason=reason,
            question=question,
            expected_input_schema=expected_input_schema,
            timeout_at=timeout_at,
            metadata=metadata,
        )

    def create_external_event_interrupt(
        self,
        *,
        session_id: str,
        ticket_id: str,
        reason: str,
        question: str,
        expected_input_schema: dict[str, Any],
        timeout_at: str | None = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> InterruptRequest:
        return self._create_interrupt(
            session_id=session_id,
            ticket_id=ticket_id,
            interrupt_type="external_event",
            source="external_event",
            reason=reason,
            question=question,
            expected_input_schema=expected_input_schema,
            timeout_at=timeout_at,
            metadata=metadata,
        )

    def create_feedback_interrupt(
        self,
        *,
        session_id: str,
        ticket_id: str,
        reason: str,
        question: str,
        expected_input_schema: dict[str, Any],
        timeout_at: str | None = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> InterruptRequest:
        return self._create_interrupt(
            session_id=session_id,
            ticket_id=ticket_id,
            interrupt_type="feedback",
            source="feedback",
            reason=reason,
            question=question,
            expected_input_schema=expected_input_schema,
            timeout_at=timeout_at,
            metadata=metadata,
        )

    def get_interrupt(self, interrupt_id: str) -> InterruptRequest | None:
        return self.store.get_interrupt(interrupt_id)

    def get_pending_interrupts(
        self,
        *,
        session_id: str | None = None,
        ticket_id: str | None = None,
    ):
        return self.store.get_pending_interrupts(session_id=session_id, ticket_id=ticket_id)

    def answer_interrupt(self, interrupt_id: str, *, answer_payload: dict[str, Any]) -> InterruptRequest | None:
        return self.store.mark_answered(interrupt_id, answer_payload=answer_payload)

    def cancel_interrupt(self, interrupt_id: str, *, answer_payload: Optional[dict[str, Any]] = None) -> InterruptRequest | None:
        return self.store.mark_cancelled(interrupt_id, answer_payload=answer_payload or {})

    def expire_interrupt(self, interrupt_id: str, *, answer_payload: Optional[dict[str, Any]] = None) -> InterruptRequest | None:
        return self.store.mark_expired(interrupt_id, answer_payload=answer_payload or {})

    def _create_interrupt(
        self,
        *,
        session_id: str,
        ticket_id: str,
        interrupt_type: str,
        source: str,
        reason: str,
        question: str,
        expected_input_schema: dict[str, Any],
        timeout_at: str | None,
        metadata: Optional[dict[str, Any]],
    ) -> InterruptRequest:
        interrupt = InterruptRequest(
            interrupt_id=str(uuid4()),
            session_id=session_id,
            ticket_id=ticket_id,
            type=interrupt_type,
            source=source,
            reason=reason,
            question=question,
            expected_input_schema=expected_input_schema,
            resume_token=str(uuid4()),
            timeout_at=timeout_at,
            metadata=dict(metadata or {}),
        )
        return self.store.create_interrupt(interrupt)
