from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..approval.models import ApprovalProposal, ApprovalRequest

TRANSPORT_PARAM_KEYS = {
    "orchestration_mode",
    "mcp_server",
    "agent_name",
    "source_agent",
    "proposal_count",
    "proposals",
    "incident_state",
}

TYPE_CHECKERS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
}


class ExecutionSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionRegistration:
    action: str
    allowed_risks: frozenset[str]
    target_fields: tuple[str, ...] = ()
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    parameter_types: Mapping[str, str] = field(default_factory=dict)

    @property
    def executable_param_keys(self) -> set[str]:
        return set(self.required_params) | set(self.optional_params)


ACTION_REGISTRY: dict[str, ActionRegistration] = {
    "rollback_deploy": ActionRegistration(
        action="rollback_deploy",
        allowed_risks=frozenset({"high", "critical"}),
        target_fields=("service",),
        required_params=("service",),
        optional_params=("version",),
        parameter_types={
            "service": "string",
            "version": "string",
        },
    ),
    "restart_pods": ActionRegistration(
        action="restart_pods",
        allowed_risks=frozenset({"high", "critical"}),
        target_fields=("service",),
        required_params=("service",),
        optional_params=("namespace",),
        parameter_types={
            "service": "string",
            "namespace": "string",
        },
    ),
    "scale_replicas": ActionRegistration(
        action="scale_replicas",
        allowed_risks=frozenset({"high", "critical"}),
        target_fields=("service",),
        required_params=("service", "count"),
        optional_params=(),
        parameter_types={
            "service": "string",
            "count": "integer",
        },
    ),
    "observe_service": ActionRegistration(
        action="observe_service",
        allowed_risks=frozenset({"low", "medium"}),
        target_fields=("service",),
        required_params=("service",),
        optional_params=(),
        parameter_types={
            "service": "string",
        },
    ),
    "cicd.rollback_release": ActionRegistration(
        action="cicd.rollback_release",
        allowed_risks=frozenset({"high", "critical"}),
        target_fields=("service",),
        required_params=("service",),
        optional_params=("environment", "cluster", "namespace", "target_revision", "reason"),
        parameter_types={
            "service": "string",
            "environment": "string",
            "cluster": "string",
            "namespace": "string",
            "target_revision": "string",
            "reason": "string",
        },
    ),
    "cicd.rollback_service": ActionRegistration(
        action="cicd.rollback_service",
        allowed_risks=frozenset({"high", "critical"}),
        target_fields=("service",),
        required_params=("service",),
        optional_params=("environment", "cluster", "namespace", "reason"),
        parameter_types={
            "service": "string",
            "environment": "string",
            "cluster": "string",
            "namespace": "string",
            "reason": "string",
        },
    ),
    "gitlab.list_merge_requests": ActionRegistration(
        action="gitlab.list_merge_requests",
        allowed_risks=frozenset({"low", "medium"}),
        target_fields=("service",),
        required_params=("service",),
        optional_params=(),
        parameter_types={
            "service": "string",
        },
    ),
}


def get_action_registration(action: str) -> ActionRegistration | None:
    return ACTION_REGISTRY.get(str(action or ""))


def registry_contains(action: str) -> bool:
    return get_action_registration(action) is not None


def normalize_executable_params(action: str, params: Mapping[str, Any]) -> dict[str, Any]:
    registration = get_action_registration(action)
    if registration is None:
        raise ExecutionSafetyError(f"action is not registered for execution: {action}")
    raw = {str(key): value for key, value in dict(params).items() if key not in TRANSPORT_PARAM_KEYS}
    unknown_keys = sorted(key for key in raw.keys() if key not in registration.executable_param_keys)
    if unknown_keys:
        raise ExecutionSafetyError(f"action params contain unknown keys: {', '.join(unknown_keys)}")
    missing = [key for key in registration.required_params if key not in raw or raw[key] in (None, "")]
    if missing:
        raise ExecutionSafetyError(f"action params missing required keys: {', '.join(missing)}")
    for key, expected_type in registration.parameter_types.items():
        if key not in raw:
            continue
        checker = TYPE_CHECKERS.get(expected_type)
        if checker is None:
            raise ExecutionSafetyError(f"unsupported parameter type rule: {expected_type}")
        if not checker(raw[key]):
            raise ExecutionSafetyError(f"action param {key} expected {expected_type}")
    return raw


def infer_target(action: str, resource: str | None, params: Mapping[str, Any]) -> str:
    if resource:
        return str(resource)
    registration = get_action_registration(action)
    if registration is None:
        return ""
    for field in registration.target_fields:
        value = params.get(field)
        if value not in (None, ""):
            return str(value)
    return ""


def build_approval_snapshot(
    *,
    action: str,
    risk: str,
    resource: str | None,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    normalized_params = normalize_executable_params(action, params)
    snapshot_payload = {
        "action": str(action),
        "target": infer_target(action, resource, normalized_params),
        "params": normalized_params,
        "risk": str(risk).lower(),
    }
    snapshot_id = hashlib.sha256(json.dumps(snapshot_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {
        **snapshot_payload,
        "snapshot_id": snapshot_id,
    }


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
