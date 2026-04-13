from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..approval.models import ApprovalProposal, ApprovalRequest

from ..approval.models import ApprovalProposal, ApprovalRequest
from .action_registry import (
    ExecutionSafetyError,
    ActionRegistration,
    ACTION_REGISTRY,
    get_action_registration,
    registry_contains,
    normalize_executable_params,
    infer_target,
    build_approval_snapshot,
)


def attach_snapshot_to_proposal(proposal: ApprovalProposal) -> ApprovalProposal:
    metadata = dict(proposal.metadata)
    try:
        snapshot = build_approval_snapshot(
            action=proposal.action,
            risk=proposal.risk,
            resource=proposal.resource,
            params=proposal.params,
        )
        metadata["approval_snapshot"] = snapshot
        metadata["registered_action"] = True
        metadata.pop("registration_error", None)
    except ExecutionSafetyError as exc:
        metadata["registered_action"] = False
        metadata["registration_error"] = str(exc)
    return proposal.model_copy(update={"metadata": metadata})


def bind_request_snapshots(request: ApprovalRequest) -> ApprovalRequest:
    proposals = [attach_snapshot_to_proposal(proposal) for proposal in request.proposals]
    context = dict(request.context)
    context["approval_snapshots"] = [dict(proposal.metadata.get("approval_snapshot") or {}) for proposal in proposals]
    return request.model_copy(update={"proposals": proposals, "context": context})


def split_registered_proposals(proposals: Iterable[ApprovalProposal]) -> tuple[list[ApprovalProposal], list[ApprovalProposal]]:
    valid: list[ApprovalProposal] = []
    invalid: list[ApprovalProposal] = []
    for proposal in proposals:
        prepared = attach_snapshot_to_proposal(proposal)
        if prepared.metadata.get("registered_action") is False:
            invalid.append(prepared)
        else:
            valid.append(prepared)
    return valid, invalid


def validate_execution_binding(proposal: ApprovalProposal | Mapping[str, Any], approval_request: ApprovalRequest | Mapping[str, Any]) -> dict[str, Any]:
    proposal_model = proposal if isinstance(proposal, ApprovalProposal) else ApprovalProposal.model_validate(proposal)
    request_model = approval_request if isinstance(approval_request, ApprovalRequest) else ApprovalRequest.model_validate(approval_request)
    registration = get_action_registration(proposal_model.action)
    if registration is None:
        raise ExecutionSafetyError(f"action is not registered for execution: {proposal_model.action}")
    if proposal_model.risk not in registration.allowed_risks:
        raise ExecutionSafetyError(
            f"action risk {proposal_model.risk} does not match registry policy for {proposal_model.action}"
        )
    snapshot = proposal_model.metadata.get("approval_snapshot") if isinstance(proposal_model.metadata, Mapping) else None
    if not isinstance(snapshot, Mapping):
        raise ExecutionSafetyError("approval snapshot is missing from persisted proposal metadata")
    recomputed = build_approval_snapshot(
        action=proposal_model.action,
        risk=proposal_model.risk,
        resource=proposal_model.resource,
        params=proposal_model.params,
    )
    if dict(snapshot) != recomputed:
        raise ExecutionSafetyError("approval snapshot mismatch between approved proposal and execution request")
    request_snapshots = request_model.context.get("approval_snapshots") if isinstance(request_model.context, Mapping) else None
    if isinstance(request_snapshots, list) and request_snapshots and recomputed not in request_snapshots:
        raise ExecutionSafetyError("approval request context does not contain the executing snapshot")
    validated_params = normalize_executable_params(proposal_model.action, proposal_model.params)
    return {
        "action": proposal_model.action,
        "risk": proposal_model.risk,
        "target": infer_target(proposal_model.action, proposal_model.resource, validated_params),
        "tool_params": validated_params,
        "snapshot": recomputed,
        "mcp_server": proposal_model.params.get("mcp_server"),
    }
