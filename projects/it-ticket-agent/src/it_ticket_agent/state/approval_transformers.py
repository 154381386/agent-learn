from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Sequence

from ..schemas import model_to_dict
from .incident_state import IncidentState
from .models import (
    ApprovedAction,
    ApprovalProposal,
    ExecutionResult,
    VerificationPlan,
)
from .transformers import risk_requires_approval

if TYPE_CHECKING:
    from ..approval.models import (
        ApprovalDecisionRecord as DomainApprovalDecisionRecord,
        ApprovalGateInput as DomainApprovalGateInput,
        ApprovalGateResult as DomainApprovalGateResult,
        ApprovalProposal as DomainApprovalProposal,
        ApprovalRequest as DomainApprovalRequest,
        ApprovalVerificationPlan as DomainApprovalVerificationPlan,
        ApprovedAction as DomainApprovedAction,
    )


def _approval_models():
    return import_module("it_ticket_agent.approval.models")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unique_strings(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _approved_action_key(action: ApprovedAction) -> str:
    return f"{action.approval_id or ''}:{action.proposal_id or ''}:{action.action}"


def _execution_result_key(result: ExecutionResult) -> str:
    proposal_id = str(result.metadata.get("proposal_id", ""))
    approval_id = str(result.metadata.get("approval_id", ""))
    return f"{approval_id}:{proposal_id}:{result.action}:{result.status}"


def _infer_target_from_params(params: Dict[str, Any]) -> str | None:
    for key in ("target", "service", "resource", "environment", "cluster"):
        value = params.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def state_verification_plan_to_domain(plan: VerificationPlan | None) -> "DomainApprovalVerificationPlan":
    approval_models = _approval_models()
    if plan is None:
        return approval_models.ApprovalVerificationPlan()
    window_minutes = int(plan.metadata.get("window_minutes", 15)) if isinstance(plan.metadata, dict) else 15
    return approval_models.ApprovalVerificationPlan(
        summary=plan.objective,
        steps=list(plan.checks),
        window_minutes=window_minutes,
        success_signals=list(plan.success_criteria),
    )


def domain_verification_plan_to_state(
    plan: "DomainApprovalVerificationPlan" | Dict[str, Any] | None,
) -> VerificationPlan | None:
    if plan is None:
        return None
    payload = model_to_dict(plan) if not isinstance(plan, dict) else dict(plan)
    if not payload:
        return None
    return VerificationPlan(
        objective=str(payload.get("summary", "") or "验证已批准动作的执行结果"),
        checks=list(payload.get("steps", [])),
        success_criteria=list(payload.get("success_signals", [])),
        verifier="approval_gate",
        metadata={"window_minutes": int(payload.get("window_minutes", 15) or 15)},
    )


def state_approval_proposal_to_domain(
    proposal: ApprovalProposal | Dict[str, Any],
) -> "DomainApprovalProposal":
    approval_models = _approval_models()
    state_proposal = proposal if isinstance(proposal, ApprovalProposal) else ApprovalProposal.model_validate(proposal)
    metadata = dict(state_proposal.metadata)
    verification_plan = VerificationPlan.model_validate(metadata.get("verification_plan", {})) if metadata.get("verification_plan") else None
    return approval_models.ApprovalProposal(
        proposal_id=state_proposal.proposal_id,
        agent=state_proposal.source_agent,
        action=state_proposal.action,
        resource=state_proposal.target or _infer_target_from_params(state_proposal.params) or "",
        params=dict(state_proposal.params),
        risk=state_proposal.risk,
        reason=state_proposal.reason,
        expected_outcome=str(metadata.get("expected_outcome", state_proposal.title or "")),
        verification_plan=state_verification_plan_to_domain(verification_plan),
        source_refs=_unique_strings([*state_proposal.evidence, *metadata.get("source_refs", [])]),
        metadata=metadata,
        created_at=str(metadata.get("created_at") or _utc_now()),
    )


def build_approval_gate_input_from_state(
    state: IncidentState,
    *,
    context: Dict[str, Any] | None = None,
) -> "DomainApprovalGateInput":
    approval_models = _approval_models()
    gate_context = dict(state.shared_context)
    if context:
        gate_context.update(context)
    aggregated_result = state.metadata.get("aggregated_result") if isinstance(state.metadata, dict) else None
    if isinstance(aggregated_result, dict):
        gate_context["aggregated_result"] = aggregated_result
    if state.subagent_results:
        gate_context["source_agents"] = [result.agent_name for result in state.subagent_results]
    if state.routing:
        gate_context["routing"] = dict(state.routing)
    return approval_models.ApprovalGateInput(
        ticket_id=state.ticket_id,
        thread_id=state.thread_id or state.ticket_id,
        proposals=[state_approval_proposal_to_domain(proposal) for proposal in state.approval_proposals],
        context=gate_context,
    )


def domain_approval_proposal_to_state(
    proposal: "DomainApprovalProposal" | Dict[str, Any],
    *,
    requires_approval: bool | None = None,
) -> ApprovalProposal:
    approval_models = _approval_models()
    domain_proposal = proposal if isinstance(proposal, approval_models.ApprovalProposal) else approval_models.ApprovalProposal.model_validate(proposal)
    metadata: Dict[str, Any] = {
        **dict(domain_proposal.metadata),
        "expected_outcome": domain_proposal.expected_outcome,
        "created_at": domain_proposal.created_at,
    }
    verification_plan = domain_verification_plan_to_state(domain_proposal.verification_plan)
    if verification_plan is not None:
        metadata["verification_plan"] = verification_plan.model_dump()
    return ApprovalProposal(
        proposal_id=domain_proposal.proposal_id,
        source_agent=domain_proposal.agent,
        action=domain_proposal.action,
        risk=domain_proposal.risk,
        reason=domain_proposal.reason,
        params=dict(domain_proposal.params),
        requires_approval=risk_requires_approval(domain_proposal.risk) if requires_approval is None else requires_approval,
        title=domain_proposal.expected_outcome or domain_proposal.action,
        target=domain_proposal.resource or _infer_target_from_params(domain_proposal.params),
        evidence=list(domain_proposal.source_refs),
        metadata=metadata,
    )


def domain_approved_action_to_state(
    action: "DomainApprovedAction" | Dict[str, Any],
    *,
    decision: "DomainApprovalDecisionRecord" | Dict[str, Any] | None = None,
) -> ApprovedAction:
    approval_models = _approval_models()
    approved_action = action if isinstance(action, approval_models.ApprovedAction) else approval_models.ApprovedAction.model_validate(action)
    decision_payload = model_to_dict(decision) if decision is not None and not isinstance(decision, dict) else dict(decision or {})
    verification_plan = domain_verification_plan_to_state(approved_action.verification_plan)
    metadata: Dict[str, Any] = {
        **dict(approved_action.metadata),
        "resource": approved_action.resource,
        "expected_outcome": approved_action.expected_outcome,
    }
    if verification_plan is not None:
        metadata["verification_plan"] = verification_plan.model_dump()
    return ApprovedAction(
        proposal_id=approved_action.proposal_id,
        approval_id=approved_action.approval_id,
        action=approved_action.action,
        risk=approved_action.risk,
        reason=approved_action.reason,
        params=dict(approved_action.params),
        approved_by=decision_payload.get("approver_id") or approved_action.approved_by,
        approved_at=decision_payload.get("decided_at") or approved_action.approved_at,
        comment=decision_payload.get("comment") or approved_action.comment,
        status="approved",
        metadata=metadata,
    )


def approval_resume_result_to_state_actions(
    approval_request: "DomainApprovalRequest" | Dict[str, Any],
    decision: "DomainApprovalDecisionRecord" | Dict[str, Any],
) -> List[ApprovedAction]:
    approval_models = _approval_models()
    approval_request_model = (
        approval_request if isinstance(approval_request, approval_models.ApprovalRequest) else approval_models.ApprovalRequest.model_validate(approval_request)
    )
    decision_model = decision if isinstance(decision, approval_models.ApprovalDecisionRecord) else approval_models.ApprovalDecisionRecord.model_validate(decision)
    if not decision_model.approved:
        return []
    actions: List[ApprovedAction] = []
    for proposal in approval_request_model.proposals:
        actions.append(
            domain_approved_action_to_state(
                approval_models.ApprovedAction(
                    approval_id=approval_request_model.approval_id,
                    proposal_id=proposal.proposal_id,
                    action=proposal.action,
                    resource=proposal.resource,
                    params=dict(proposal.params),
                    risk=proposal.risk,
                    reason=proposal.reason,
                    expected_outcome=proposal.expected_outcome,
                    verification_plan=proposal.verification_plan,
                    approved_by=decision_model.approver_id,
                    approved_at=decision_model.decided_at,
                    comment=decision_model.comment,
                    metadata=dict(getattr(proposal, "metadata", {})),
                ),
            )
        )
    return actions


def execution_result_to_state(
    execution_result: ExecutionResult | Dict[str, Any],
    *,
    action: str | None = None,
    risk: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> ExecutionResult:
    if isinstance(execution_result, ExecutionResult):
        result = execution_result.model_copy(deep=True)
        if metadata:
            result.metadata.update(metadata)
        return result

    payload = dict(execution_result)
    metadata_payload = dict(metadata or {})
    summary = str(payload.get("summary") or payload.get("message") or "执行结果已记录。")
    execution_payload = payload.get("payload")
    if not isinstance(execution_payload, dict):
        execution_payload = dict(payload.get("diagnosis", {}).get("execution", {})) if isinstance(payload.get("diagnosis"), dict) else {}
    resolved_action = action or payload.get("action")
    if not resolved_action and isinstance(payload.get("diagnosis"), dict):
        resolved_action = payload.get("diagnosis", {}).get("approval", {}).get("action")
    resolved_risk = risk or payload.get("risk") or metadata_payload.get("risk") or "low"
    status = str(payload.get("status") or execution_payload.get("status") or "completed")
    if status not in {"pending", "completed", "failed", "skipped"}:
        status = "completed"

    return ExecutionResult(
        action=str(resolved_action or "unknown_action"),
        status=status,
        summary=summary,
        payload=execution_payload,
        evidence=list(payload.get("evidence", [])),
        risk=str(resolved_risk),
        executor=payload.get("executor") or metadata_payload.get("executor"),
        metadata={
            **metadata_payload,
            **dict(payload.get("metadata", {})),
        },
    )


def _merge_verification_plans(plans: Sequence[VerificationPlan]) -> VerificationPlan | None:
    if not plans:
        return None
    if len(plans) == 1:
        return plans[0]
    return VerificationPlan(
        objective="验证已批准动作执行效果",
        checks=_unique_strings(step for plan in plans for step in plan.checks),
        success_criteria=_unique_strings(signal for plan in plans for signal in plan.success_criteria),
        verifier="approval_gate",
        metadata={
            "plan_count": len(plans),
            "plans": [plan.model_dump() for plan in plans],
        },
    )


def apply_approval_gate_result_to_state(
    state: IncidentState,
    gate_result: "DomainApprovalGateResult" | Dict[str, Any],
) -> IncidentState:
    approval_models = _approval_models()
    result = gate_result if isinstance(gate_result, approval_models.ApprovalGateResult) else approval_models.ApprovalGateResult.model_validate(gate_result)
    next_state = state.model_copy(deep=True)

    pending_proposals = [
        domain_approval_proposal_to_state(proposal, requires_approval=True)
        for proposal in (result.approval_request.proposals if result.approval_request is not None else [])
    ]
    auto_approved_actions = [domain_approved_action_to_state(action) for action in result.approved_actions]
    rejected_proposals = [
        domain_approval_proposal_to_state(proposal, requires_approval=False)
        for proposal in result.rejected_proposals
    ]
    auto_approved_proposals = [
        domain_approval_proposal_to_state(proposal, requires_approval=False)
        for proposal in result.auto_approved_proposals
    ]

    next_state.approval_proposals = pending_proposals
    next_state = apply_approved_actions_to_state(next_state, auto_approved_actions)
    next_state.metadata["approval_request"] = result.approval_request.model_dump() if result.approval_request is not None else None
    next_state.metadata["approval_gate"] = {
        "rejected_proposals": [proposal.model_dump() for proposal in rejected_proposals],
        "auto_approved_proposals": [proposal.model_dump() for proposal in auto_approved_proposals],
        "policy_results": [model_to_dict(policy_result) for policy_result in result.policy_results],
    }
    next_state.status = "awaiting_approval" if result.approval_request is not None else "analyzed"
    return next_state


def apply_approved_actions_to_state(
    state: IncidentState,
    approved_actions: Iterable[ApprovedAction | Dict[str, Any] | Any],
    *,
    decision: "DomainApprovalDecisionRecord" | Dict[str, Any] | None = None,
) -> IncidentState:
    next_state = state.model_copy(deep=True)
    converted_actions: List[ApprovedAction] = []
    verification_plans: List[VerificationPlan] = []

    for item in approved_actions:
        if isinstance(item, ApprovedAction):
            converted = item.model_copy(deep=True)
        else:
            converted = domain_approved_action_to_state(item, decision=decision)
        converted_actions.append(converted)
        raw_plan = converted.metadata.get("verification_plan")
        plan = VerificationPlan.model_validate(raw_plan) if raw_plan else None
        if plan is not None:
            verification_plans.append(plan)

    existing = {_approved_action_key(item): item for item in next_state.approved_actions}
    for action in converted_actions:
        existing[_approved_action_key(action)] = action
    next_state.approved_actions = list(existing.values())

    approved_ids = {action.proposal_id for action in converted_actions if action.proposal_id}
    next_state.approval_proposals = [
        proposal for proposal in next_state.approval_proposals if proposal.proposal_id not in approved_ids
    ]

    merged_plan = _merge_verification_plans(verification_plans)
    if merged_plan is not None:
        next_state.verification_plan = merged_plan
    if decision is not None:
        next_state.metadata["approval_decision"] = model_to_dict(decision)
    return next_state


def apply_execution_results_to_state(
    state: IncidentState,
    execution_results: Iterable[ExecutionResult | Dict[str, Any]],
) -> IncidentState:
    next_state = state.model_copy(deep=True)
    converted = [execution_result_to_state(result) for result in execution_results]
    existing = {_execution_result_key(item): item for item in next_state.execution_results}
    for result in converted:
        existing[_execution_result_key(result)] = result
    next_state.execution_results = list(existing.values())
    if converted:
        next_state.final_summary = converted[-1].summary
        next_state.status = "completed"
    return next_state


def apply_approval_resume_result_to_state(
    state: IncidentState,
    approval_request: "DomainApprovalRequest" | Dict[str, Any],
    decision: "DomainApprovalDecisionRecord" | Dict[str, Any],
    *,
    execution_results: Iterable[ExecutionResult | Dict[str, Any]] | None = None,
) -> IncidentState:
    approval_models = _approval_models()
    approval_request_model = (
        approval_request if isinstance(approval_request, approval_models.ApprovalRequest) else approval_models.ApprovalRequest.model_validate(approval_request)
    )
    decision_model = decision if isinstance(decision, approval_models.ApprovalDecisionRecord) else approval_models.ApprovalDecisionRecord.model_validate(decision)

    next_state = state.model_copy(deep=True)
    next_state.metadata["approval_request"] = approval_request_model.model_dump()
    next_state.metadata["approval_decision"] = decision_model.model_dump()

    if decision_model.approved:
        approved_actions = approval_resume_result_to_state_actions(approval_request_model, decision_model)
        next_state = apply_approved_actions_to_state(next_state, approved_actions, decision=decision_model)
        next_state.status = "approved"
    else:
        rejected_ids = {proposal.proposal_id for proposal in approval_request_model.proposals}
        next_state.approval_proposals = [
            proposal for proposal in next_state.approval_proposals if proposal.proposal_id not in rejected_ids
        ]
        next_state.metadata.setdefault("approval_gate", {})
        next_state.metadata["approval_gate"]["rejected_proposals"] = [
            domain_approval_proposal_to_state(proposal, requires_approval=False).model_dump()
            for proposal in approval_request_model.proposals
        ]
        next_state.status = "completed"
        next_state.final_summary = "审批未通过，未执行任何高风险动作。"

    if execution_results is not None:
        next_state = apply_execution_results_to_state(next_state, execution_results)
    return next_state


def approval_example_payloads() -> Dict[str, Dict[str, Any]]:
    state = IncidentState(
        ticket_id="T-2001",
        user_id="u-approver-demo",
        thread_id="T-2001",
        message="订单服务发布后 5xx 上升，需要判断是否回滚。",
        service="order-service",
        cluster="prod-shanghai-1",
        namespace="orders",
        channel="feishu",
        status="analyzed",
        approval_proposals=[
            ApprovalProposal(
                proposal_id="proposal-high-1",
                source_agent="cicd_agent",
                action="cicd.rollback_service",
                risk="high",
                reason="发布后错误率显著上升，建议准备回滚。",
                params={"service": "order-service", "environment": "prod-shanghai-1"},
                requires_approval=True,
                title="回滚订单服务",
                target="order-service",
                evidence=["5xx 激增", "发布时间与故障窗口重合"],
                metadata={
                    "expected_outcome": "错误率恢复基线",
                    "verification_plan": {
                        "objective": "验证回滚效果",
                        "checks": ["观察 15 分钟错误率", "确认告警恢复"],
                        "success_criteria": ["5xx 降至基线", "核心告警恢复"],
                        "verifier": "approval_gate",
                        "metadata": {"window_minutes": 15},
                    },
                },
            ),
            ApprovalProposal(
                proposal_id="proposal-low-1",
                source_agent="cicd_agent",
                action="gitlab.list_merge_requests",
                risk="low",
                reason="先补充最近发布事实。",
                params={"service": "order-service"},
                requires_approval=False,
                title="查询最近 MR",
                target="order-service",
                evidence=["需要补事实"],
                metadata={},
            ),
        ],
    )
    gate_input = build_approval_gate_input_from_state(state).model_dump()
    gate_result = {
        "approval_request": {
            "approval_id": "approval-2001",
            "ticket_id": "T-2001",
            "thread_id": "T-2001",
            "status": "pending",
            "proposals": [gate_input["proposals"][0]],
            "highest_risk": "high",
            "summary": "cicd_agent:cicd.rollback_service",
            "context": {"service": "order-service"},
            "created_at": "2026-03-29T00:00:00+00:00",
            "updated_at": "2026-03-29T00:00:00+00:00",
        },
        "approved_actions": [
            {
                "approval_id": "auto-approved",
                "proposal_id": "proposal-low-1",
                "action": "gitlab.list_merge_requests",
                "resource": "order-service",
                "params": {"service": "order-service"},
                "risk": "low",
                "reason": "先补充最近发布事实。",
                "expected_outcome": "拿到最近 MR 列表",
                "verification_plan": {
                    "summary": "确认 MR 查询返回成功",
                    "steps": ["检查返回条目是否非空"],
                    "window_minutes": 5,
                    "success_signals": ["返回最近 MR 记录"],
                },
            }
        ],
        "rejected_proposals": [],
        "auto_approved_proposals": [gate_input["proposals"][1]],
        "policy_results": [
            {
                "proposal_id": "proposal-high-1",
                "decision": "requires_approval",
                "reasons": ["high risk rollback"],
                "normalized_risk": "high",
            },
            {
                "proposal_id": "proposal-low-1",
                "decision": "auto_approve",
                "reasons": ["read-only action"],
                "normalized_risk": "low",
            },
        ],
    }
    resume_writeback = {
        "approval_request": gate_result["approval_request"],
        "decision": {
            "approval_id": "approval-2001",
            "approved": True,
            "approver_id": "alice",
            "comment": "确认回滚",
            "decided_at": "2026-03-29T00:05:00+00:00",
        },
        "approved_actions": [
            {
                "approval_id": "approval-2001",
                "proposal_id": "proposal-high-1",
                "action": "cicd.rollback_service",
                "risk": "high",
                "reason": "发布后错误率显著上升，建议准备回滚。",
                "params": {"service": "order-service", "environment": "prod-shanghai-1"},
                "approved_by": "alice",
                "approved_at": "2026-03-29T00:05:00+00:00",
                "comment": "确认回滚",
                "status": "approved",
                "metadata": {
                    "resource": "order-service",
                    "expected_outcome": "错误率恢复基线",
                    "verification_plan": {
                        "objective": "验证回滚效果",
                        "checks": ["观察 15 分钟错误率", "确认告警恢复"],
                        "success_criteria": ["5xx 降至基线", "核心告警恢复"],
                        "verifier": "approval_gate",
                        "metadata": {"window_minutes": 15},
                    },
                },
            }
        ],
        "execution_results": [
            {
                "action": "cicd.rollback_service",
                "status": "completed",
                "summary": "回滚任务已执行完成。",
                "payload": {"job_id": "rollback-2001", "status": "completed"},
                "evidence": ["执行系统返回 completed"],
                "risk": "high",
                "executor": "executor",
                "metadata": {
                    "approval_id": "approval-2001",
                    "proposal_id": "proposal-high-1",
                },
            }
        ],
    }
    return {
        "approval_gate_input": gate_input,
        "approval_gate_result": gate_result,
        "approval_resume_writeback": resume_writeback,
    }


__all__ = [
    "approval_example_payloads",
    "approval_resume_result_to_state_actions",
    "apply_approval_gate_result_to_state",
    "apply_approval_resume_result_to_state",
    "apply_approved_actions_to_state",
    "apply_execution_results_to_state",
    "build_approval_gate_input_from_state",
    "domain_approval_proposal_to_state",
    "domain_approved_action_to_state",
    "domain_verification_plan_to_state",
    "execution_result_to_state",
    "state_approval_proposal_to_domain",
    "state_verification_plan_to_domain",
]
