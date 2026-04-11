from __future__ import annotations

from typing import Any, Dict, Optional

from .approval.adapters import (
    approval_request_to_legacy_payload,
    legacy_decision_to_record,
    legacy_payload_to_approval_request,
)
from .approval.models import ApprovalDecisionRecord, ApprovalRequest
from .approval.store import ApprovalStoreV2


class ApprovalStore:
    def __init__(self, db_path: str, *, backend: ApprovalStoreV2 | None = None) -> None:
        self.db_path = db_path
        self.v2_store = backend or ApprovalStoreV2(db_path)

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = legacy_payload_to_approval_request(payload)
        saved = self.v2_store.create_request(request)
        return approval_request_to_legacy_payload(saved)

    def create_request(self, request: ApprovalRequest | Dict[str, Any]) -> ApprovalRequest:
        record = request if isinstance(request, ApprovalRequest) else ApprovalRequest.model_validate(request)
        return self.v2_store.create_request(record)

    def get(self, approval_id: str) -> Optional[Dict[str, Any]]:
        record = self.v2_store.get_request(approval_id)
        if record is None:
            return None
        return approval_request_to_legacy_payload(record)

    def get_request(self, approval_id: str) -> Optional[ApprovalRequest]:
        return self.v2_store.get_request(approval_id)

    def decide(self, approval_id: str, approved: bool, approver_id: str, comment: Optional[str]) -> Dict[str, Any]:
        decision = legacy_decision_to_record(
            {
                "approved": approved,
                "approver_id": approver_id,
                "comment": comment,
            },
            approval_id=approval_id,
        )
        saved = self.v2_store.record_decision(decision)
        return approval_request_to_legacy_payload(saved)

    def record_decision(self, decision: ApprovalDecisionRecord | Dict[str, Any]) -> ApprovalRequest:
        record = decision if isinstance(decision, ApprovalDecisionRecord) else ApprovalDecisionRecord.model_validate(decision)
        return self.v2_store.record_decision(record)

    def expire(self, approval_id: str, *, actor_id: str = "system", comment: Optional[str] = None) -> Dict[str, Any]:
        saved = self.v2_store.expire_request(approval_id, actor_id=actor_id, comment=comment)
        return approval_request_to_legacy_payload(saved)

    def cancel(self, approval_id: str, *, actor_id: str = "system", comment: Optional[str] = None) -> Dict[str, Any]:
        saved = self.v2_store.cancel_request(approval_id, actor_id=actor_id, comment=comment)
        return approval_request_to_legacy_payload(saved)

    def record_resumed(
        self,
        approval_id: str,
        *,
        actor_id: str = "system",
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.v2_store.record_resumed(approval_id, actor_id=actor_id, detail=detail)

    def list_events(self, approval_id: str) -> list[dict[str, Any]]:
        return self.v2_store.list_events(approval_id)
