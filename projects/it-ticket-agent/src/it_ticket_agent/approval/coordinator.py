from __future__ import annotations

import uuid
from collections import OrderedDict
from typing import Iterable, Sequence

from .models import (
    ApprovalDecisionRecord,
    ApprovalGateInput,
    ApprovalGateResult,
    ApprovalPolicyResult,
    ApprovalProposal,
    ApprovalRequest,
    ApprovedAction,
    utc_now,
)
from .policy import ApprovalPolicy, RISK_PRIORITY


class ApprovalCoordinator:
    def __init__(self, policy: ApprovalPolicy | None = None) -> None:
        self.policy = policy or ApprovalPolicy()

    def build_gate_result(self, gate_input: ApprovalGateInput) -> ApprovalGateResult:
        proposals = self.collect_proposals(gate_input.proposals)
        proposals = self.dedupe_proposals(proposals)
        policy_results = self.evaluate_policies(proposals)

        requires_approval: list[ApprovalProposal] = []
        approved_actions: list[ApprovedAction] = []
        rejected_proposals: list[ApprovalProposal] = []
        auto_approved_proposals: list[ApprovalProposal] = []
        policy_map = {result.proposal_id: result for result in policy_results}

        for proposal in proposals:
            policy_result = policy_map[proposal.proposal_id]
            normalized = proposal.model_copy(update={"risk": policy_result.normalized_risk})
            if policy_result.decision == "requires_approval":
                requires_approval.append(normalized)
            elif policy_result.decision == "auto_approve":
                auto_approved_proposals.append(normalized)
                approved_actions.append(
                    self._to_approved_action(
                        "auto-approved",
                        normalized,
                        approved_by="system",
                        approved_at=utc_now(),
                        comment="auto approved by approval policy",
                    )
                )
            else:
                rejected_proposals.append(normalized)

        approval_request = None
        if requires_approval:
            approval_request = self._build_approval_request(
                ticket_id=gate_input.ticket_id,
                thread_id=gate_input.thread_id,
                proposals=requires_approval,
                context=gate_input.context,
            )

        return ApprovalGateResult(
            approval_request=approval_request,
            approved_actions=approved_actions,
            rejected_proposals=rejected_proposals,
            auto_approved_proposals=auto_approved_proposals,
            policy_results=policy_results,
        )

    def collect_proposals(self, proposals: Iterable[ApprovalProposal | dict]) -> list[ApprovalProposal]:
        collected: list[ApprovalProposal] = []
        for proposal in proposals:
            if isinstance(proposal, ApprovalProposal):
                collected.append(proposal)
            else:
                collected.append(ApprovalProposal.model_validate(proposal))
        return collected

    def dedupe_proposals(self, proposals: Sequence[ApprovalProposal]) -> list[ApprovalProposal]:
        merged: OrderedDict[str, ApprovalProposal] = OrderedDict()
        for proposal in proposals:
            current = merged.get(proposal.dedupe_key)
            if current is None:
                merged[proposal.dedupe_key] = proposal
                continue
            merged[proposal.dedupe_key] = self.merge_proposals(current, proposal)
        return list(merged.values())

    def merge_proposals(self, left: ApprovalProposal, right: ApprovalProposal) -> ApprovalProposal:
        winner = left if RISK_PRIORITY[left.risk] >= RISK_PRIORITY[right.risk] else right
        loser = right if winner is left else left

        refs = list(dict.fromkeys([*winner.source_refs, *loser.source_refs]))
        reason = winner.reason if winner.reason else loser.reason
        if winner.reason and loser.reason and winner.reason != loser.reason:
            reason = f"{winner.reason}；{loser.reason}"

        merged_metadata = dict(loser.metadata)
        merged_metadata.update(winner.metadata)
        merged_metadata["merged_proposal_ids"] = list(
            dict.fromkeys(
                [
                    *loser.metadata.get("merged_proposal_ids", [loser.proposal_id]),
                    *winner.metadata.get("merged_proposal_ids", [winner.proposal_id]),
                ]
            )
        )

        return winner.model_copy(
            update={
                "reason": reason,
                "source_refs": refs,
                "metadata": merged_metadata,
            }
        )

    def evaluate_policies(self, proposals: Sequence[ApprovalProposal]) -> list[ApprovalPolicyResult]:
        return [self.policy.evaluate(proposal) for proposal in proposals]

    def build_resume_result(
        self,
        approval_request: ApprovalRequest,
        decision: ApprovalDecisionRecord,
    ) -> ApprovalGateResult:
        if decision.approved:
            approved_actions = [
                self._to_approved_action(
                    approval_request.approval_id,
                    proposal,
                    approved_by=decision.approver_id,
                    approved_at=decision.decided_at,
                    comment=decision.comment,
                )
                for proposal in approval_request.proposals
            ]
            return ApprovalGateResult(approved_actions=approved_actions)

        return ApprovalGateResult(rejected_proposals=approval_request.proposals)

    def _build_approval_request(
        self,
        ticket_id: str,
        thread_id: str,
        proposals: Sequence[ApprovalProposal],
        context: dict,
    ) -> ApprovalRequest:
        highest = max(proposals, key=lambda proposal: RISK_PRIORITY[proposal.risk]).risk
        summary = "；".join(f"{proposal.agent}:{proposal.action}" for proposal in proposals)
        return ApprovalRequest(
            approval_id=str(uuid.uuid4()),
            ticket_id=ticket_id,
            thread_id=thread_id,
            proposals=list(proposals),
            highest_risk=highest,
            summary=summary,
            context=dict(context),
        )

    @staticmethod
    def _to_approved_action(
        approval_id: str,
        proposal: ApprovalProposal,
        *,
        approved_by: str | None = None,
        approved_at: str | None = None,
        comment: str | None = None,
    ) -> ApprovedAction:
        return ApprovedAction(
            approval_id=approval_id,
            proposal_id=proposal.proposal_id,
            action=proposal.action,
            resource=proposal.resource,
            params=dict(proposal.params),
            risk=proposal.risk,
            reason=proposal.reason,
            expected_outcome=proposal.expected_outcome,
            verification_plan=proposal.verification_plan,
            approved_by=approved_by,
            approved_at=approved_at,
            comment=comment,
            metadata=dict(proposal.metadata),
        )
