from .coordinator import ApprovalCoordinator
from .models import (
    ApprovalAuditEvent,
    ApprovalDecisionRecord,
    ApprovalGateInput,
    ApprovalGateResult,
    ApprovalPolicyResult,
    ApprovalProposal,
    ApprovalRequest,
    ApprovalVerificationPlan,
    ApprovedAction,
)
from .policy import ApprovalPolicy
from .store import ApprovalStateError, ApprovalStoreV2

__all__ = [
    "ApprovalAuditEvent",
    "ApprovalCoordinator",
    "ApprovalDecisionRecord",
    "ApprovalGateInput",
    "ApprovalGateResult",
    "ApprovalPolicy",
    "ApprovalPolicyResult",
    "ApprovalProposal",
    "ApprovalRequest",
    "ApprovalStateError",
    "ApprovalStoreV2",
    "ApprovalVerificationPlan",
    "ApprovedAction",
    "approval_request_to_legacy_payload",
    "domain_approval_proposal_to_state",
    "domain_approved_action_to_state",
    "legacy_decision_to_record",
    "legacy_payload_to_approval_request",
    "state_approval_proposal_to_domain",
]


def __getattr__(name: str):
    if name in {
        "approval_request_to_legacy_payload",
        "domain_approval_proposal_to_state",
        "domain_approved_action_to_state",
        "legacy_decision_to_record",
        "legacy_payload_to_approval_request",
        "state_approval_proposal_to_domain",
    }:
        from . import adapters as _adapters

        return getattr(_adapters, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
