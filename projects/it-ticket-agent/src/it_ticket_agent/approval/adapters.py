from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Mapping

from ..schemas import ApprovalDecisionRequest, ApprovalPayload, model_to_dict
from .models import (
    ApprovalDecisionRecord,
    ApprovalProposal,
    ApprovalRequest,
    ApprovalVerificationPlan,
    ApprovedAction,
    utc_now,
)

if TYPE_CHECKING:
    from ..state.models import ApprovalProposal as StateApprovalProposal
    from ..state.models import ApprovedAction as StateApprovedAction


_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _verification_plan_from_state_metadata(metadata: Mapping[str, Any]) -> ApprovalVerificationPlan:
    verification_payload = metadata.get("verification_plan")
    if isinstance(verification_payload, Mapping):
        nested_metadata = verification_payload.get("metadata")
        if isinstance(nested_metadata, Mapping):
            window_minutes = int(nested_metadata.get("window_minutes", 15) or 15)
        else:
            window_minutes = 15
        return ApprovalVerificationPlan(
            summary=str(verification_payload.get("objective", "") or metadata.get("verification_summary", "")),
            steps=list(verification_payload.get("checks", []) or metadata.get("verification_steps", [])),
            window_minutes=window_minutes,
            success_signals=list(
                verification_payload.get("success_criteria", []) or metadata.get("success_signals", [])
            ),
        )
    return ApprovalVerificationPlan(
        summary=str(metadata.get("verification_summary", "")),
        steps=list(metadata.get("verification_steps", [])),
        window_minutes=int(metadata.get("verification_window_minutes", 15) or 15),
        success_signals=list(metadata.get("success_signals", [])),
    )


def _verification_plan_to_state_metadata(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "objective": str(plan.get("summary", "") or "验证已批准动作的执行结果"),
        "checks": list(plan.get("steps", [])),
        "success_criteria": list(plan.get("success_signals", [])),
        "verifier": "approval_gate",
        "metadata": {"window_minutes": int(plan.get("window_minutes", 15) or 15)},
    }


def state_approval_proposal_to_domain(proposal: "StateApprovalProposal" | Mapping[str, Any]) -> ApprovalProposal:
    raw = model_to_dict(proposal) if not isinstance(proposal, Mapping) else dict(proposal)
    metadata = dict(raw.get("metadata", {}))
    return ApprovalProposal(
        proposal_id=str(raw.get("proposal_id", "")),
        agent=str(raw.get("source_agent", "")),
        action=str(raw.get("action", "")),
        resource=str(raw.get("target") or raw.get("params", {}).get("resource") or raw.get("params", {}).get("service") or raw.get("params", {}).get("target") or ""),
        params=dict(raw.get("params", {})),
        risk=str(raw.get("risk", "low")).lower(),
        reason=str(raw.get("reason", "")),
        expected_outcome=str(metadata.get("expected_outcome", raw.get("title", "") or "")),
        verification_plan=_verification_plan_from_state_metadata(metadata),
        source_refs=list(raw.get("evidence", [])) + list(metadata.get("source_refs", [])),
        metadata=metadata,
        created_at=str(metadata.get("created_at") or raw.get("created_at") or utc_now()),
    )


def domain_approval_proposal_to_state(
    proposal: ApprovalProposal | Mapping[str, Any],
    *,
    requires_approval: bool | None = None,
) -> "StateApprovalProposal":
    from ..state.models import ApprovalProposal as StateApprovalProposal

    raw = model_to_dict(proposal) if not isinstance(proposal, Mapping) else dict(proposal)
    metadata = dict(raw.get("metadata", {}))
    metadata["expected_outcome"] = raw.get("expected_outcome", "")
    metadata["created_at"] = raw.get("created_at") or utc_now()
    metadata["verification_plan"] = _verification_plan_to_state_metadata(dict(raw.get("verification_plan", {})))
    return StateApprovalProposal(
        proposal_id=str(raw.get("proposal_id", "")),
        source_agent=str(raw.get("agent", "")),
        action=str(raw.get("action", "")),
        risk=str(raw.get("risk", "low")).lower(),
        reason=str(raw.get("reason", "")),
        params=dict(raw.get("params", {})),
        requires_approval=(str(raw.get("risk", "low")).lower() in {"high", "critical"}) if requires_approval is None else requires_approval,
        title=str(raw.get("expected_outcome", "") or raw.get("action", "")) or None,
        target=str(raw.get("resource", "") or None) if raw.get("resource") else None,
        evidence=list(raw.get("source_refs", [])),
        metadata=metadata,
    )


def legacy_payload_to_approval_request(payload: ApprovalPayload | Mapping[str, Any]) -> ApprovalRequest:
    raw = model_to_dict(payload) if not isinstance(payload, Mapping) else dict(payload)
    approval_id = str(raw.get("approval_id") or uuid.uuid4())
    params = dict(raw.get("params", {}))
    embedded_proposals = params.get("proposals") if isinstance(params.get("proposals"), list) else []

    proposals: list[ApprovalProposal] = []
    if embedded_proposals:
        for index, item in enumerate(embedded_proposals):
            if not isinstance(item, Mapping):
                continue
            proposal_payload = dict(item)
            proposal_payload.setdefault("proposal_id", f"{approval_id}:legacy:{index}")
            proposal_payload.setdefault("agent", str(params.get("agent_name") or params.get("source_agent") or "legacy"))
            proposal_payload.setdefault("resource", str(proposal_payload.get("resource") or params.get("resource") or ""))
            proposal_payload.setdefault("reason", str(proposal_payload.get("reason") or raw.get("reason") or ""))
            proposal_payload.setdefault("risk", str(proposal_payload.get("risk") or raw.get("risk") or "low").lower())
            proposal_payload.setdefault("expected_outcome", str(proposal_payload.get("expected_outcome") or params.get("expected_outcome") or ""))
            proposal_payload.setdefault("verification_plan", proposal_payload.get("verification_plan") or {})
            proposal_payload.setdefault("source_refs", proposal_payload.get("source_refs") or params.get("source_refs") or [])
            proposal_payload.setdefault("metadata", {"legacy_payload": True, "legacy_params": True})
            proposals.append(ApprovalProposal.model_validate(proposal_payload))

    if not proposals:
        proposal_id = str(params.get("proposal_id") or f"{approval_id}:legacy")
        resource = str(params.get("resource") or params.get("service") or params.get("target") or "")
        verification_plan = ApprovalVerificationPlan(
            summary=str(params.get("verification_summary", "")),
            steps=list(params.get("verification_steps", [])),
            window_minutes=int(params.get("verification_window_minutes", 15) or 15),
            success_signals=list(params.get("success_signals", [])),
        )
        proposals.append(
            ApprovalProposal(
                proposal_id=proposal_id,
                agent=str(params.get("agent_name") or params.get("source_agent") or "legacy"),
                action=str(raw.get("action", "")),
                resource=resource,
                params=params,
                risk=str(raw.get("risk", "low")).lower(),
                reason=str(raw.get("reason", "")),
                expected_outcome=str(params.get("expected_outcome", "")),
                verification_plan=verification_plan,
                source_refs=list(params.get("source_refs", [])),
                metadata={"legacy_payload": True, "legacy_params": True},
            )
        )

    summary = str(raw.get("summary") or raw.get("reason") or raw.get("action") or "")
    highest_risk = max((proposal.risk for proposal in proposals), key=lambda risk: _RISK_ORDER.get(risk, 0))
    return ApprovalRequest(
        approval_id=approval_id,
        ticket_id=str(raw.get("ticket_id", "")),
        thread_id=str(raw.get("thread_id", raw.get("ticket_id", ""))),
        status=str(raw.get("status", "pending")).lower(),
        proposals=proposals,
        highest_risk=highest_risk,
        summary=summary,
        context={"legacy_payload": True},
        approver_id=raw.get("approver_id"),
        comment=raw.get("comment"),
        decided_at=raw.get("decided_at"),
    )


def approval_request_to_legacy_payload(request: ApprovalRequest | Mapping[str, Any]) -> dict[str, Any]:
    approval_request = request if isinstance(request, ApprovalRequest) else ApprovalRequest.model_validate(request)
    primary = approval_request.proposals[0] if approval_request.proposals else None
    proposal_payloads = [proposal.model_dump() for proposal in approval_request.proposals]
    params = dict(primary.params) if primary is not None else {}
    params.setdefault("proposal_count", len(approval_request.proposals))
    params.setdefault("proposals", proposal_payloads)
    if primary is not None:
        params.setdefault("resource", primary.resource)
        params.setdefault("expected_outcome", primary.expected_outcome)
        params.setdefault("source_refs", list(primary.source_refs))
        params.setdefault("proposal_id", primary.proposal_id)
        params.setdefault("agent_name", primary.agent)
    action = primary.action if primary is not None else approval_request.summary
    reason = primary.reason if primary is not None and len(approval_request.proposals) <= 1 else approval_request.summary
    return {
        "approval_id": approval_request.approval_id,
        "ticket_id": approval_request.ticket_id,
        "thread_id": approval_request.thread_id,
        "action": action,
        "risk": approval_request.highest_risk,
        "reason": reason,
        "params": params,
        "status": approval_request.status,
        "approver_id": approval_request.approver_id,
        "comment": approval_request.comment,
        "decided_at": approval_request.decided_at,
    }


def legacy_decision_to_record(
    decision: ApprovalDecisionRequest | Mapping[str, Any],
    *,
    approval_id: str,
) -> ApprovalDecisionRecord:
    raw = model_to_dict(decision) if not isinstance(decision, Mapping) else dict(decision)
    return ApprovalDecisionRecord(
        approval_id=approval_id,
        approved=bool(raw.get("approved", False)),
        approver_id=str(raw.get("approver_id", "")),
        comment=raw.get("comment"),
        decided_at=str(raw.get("decided_at") or utc_now()),
    )


def domain_approved_action_to_state(action: ApprovedAction | Mapping[str, Any]) -> "StateApprovedAction":
    from ..state.models import ApprovedAction as StateApprovedAction

    raw = model_to_dict(action) if not isinstance(action, Mapping) else dict(action)
    metadata = dict(raw.get("metadata", {}))
    metadata["resource"] = raw.get("resource", "")
    metadata["expected_outcome"] = raw.get("expected_outcome", "")
    metadata["verification_plan"] = _verification_plan_to_state_metadata(dict(raw.get("verification_plan", {})))
    return StateApprovedAction(
        proposal_id=raw.get("proposal_id"),
        approval_id=raw.get("approval_id"),
        action=str(raw.get("action", "")),
        risk=str(raw.get("risk", "low")).lower(),
        reason=str(raw.get("reason", "")),
        params=dict(raw.get("params", {})),
        approved_by=raw.get("approved_by"),
        approved_at=raw.get("approved_at"),
        comment=raw.get("comment"),
        status="approved",
        metadata=metadata,
    )
