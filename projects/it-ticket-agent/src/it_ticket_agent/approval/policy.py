from __future__ import annotations

from typing import List

from .models import ApprovalPolicyResult, ApprovalProposal, RiskLevel


RISK_PRIORITY: dict[RiskLevel, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


class ApprovalPolicy:
    def evaluate(self, proposal: ApprovalProposal) -> ApprovalPolicyResult:
        reasons: List[str] = []
        normalized_risk = proposal.risk

        if proposal.params.get("auto_approve") is True:
            reasons.append("params.auto_approve=true")
            return ApprovalPolicyResult(
                proposal_id=proposal.proposal_id,
                decision="auto_approve",
                reasons=reasons,
                normalized_risk=normalized_risk,
            )

        if proposal.params.get("policy_blocked") is True:
            reasons.append("params.policy_blocked=true")
            return ApprovalPolicyResult(
                proposal_id=proposal.proposal_id,
                decision="reject",
                reasons=reasons,
                normalized_risk=normalized_risk,
            )

        destructive_hint = any(
            token in proposal.action.lower()
            for token in ("rollback", "delete", "restart", "scale", "drain")
        )
        if destructive_hint and RISK_PRIORITY[normalized_risk] < RISK_PRIORITY["high"]:
            normalized_risk = "high"
            reasons.append("destructive_action_escalated_to_high")

        if RISK_PRIORITY[normalized_risk] >= RISK_PRIORITY["high"]:
            reasons.append(f"risk={normalized_risk}")
            decision = "requires_approval"
        else:
            reasons.append(f"risk={normalized_risk}")
            decision = "auto_approve"

        return ApprovalPolicyResult(
            proposal_id=proposal.proposal_id,
            decision=decision,
            reasons=reasons,
            normalized_risk=normalized_risk,
        )
