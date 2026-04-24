from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack, nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any, Literal, Mapping, Sequence
from unittest.mock import patch

from pydantic import BaseModel, Field, model_validator

from ..mcp.client import MCPClient
from ..schemas import (
    ConversationCreateRequest,
    ConversationMessageRequest,
    ConversationResumeRequest,
)
from ..tools.runtime import LocalToolRuntime
from .agent_eval import (
    AgentEvalCase,
    AgentEvalCheck,
    AgentEvalExpectation,
    AgentEvalRunner,
    AgentEvalScore,
    AgentEvalSetup,
    DisabledEvalLLM,
    EvalGateResult,
    extract_eval_observation,
    score_agent_eval_case,
)


class SessionFlowStepExpectation(AgentEvalExpectation):
    response_status: str | None = None
    session_status: str | None = None
    session_stage: str | None = None
    current_agent: str | None = None
    message_event_type: str | None = None
    message_event_topic_shift_detected: bool | None = None
    message_event_incremental_tool_domains: list[str] = Field(default_factory=list)
    case_exists: bool | None = None
    human_verified: bool | None = None
    actual_root_cause_contains: list[str] = Field(default_factory=list)
    new_system_event_types: list[str] = Field(default_factory=list)
    new_approval_event_types: list[str] = Field(default_factory=list)
    min_current_intent_history_length: int | None = None
    recovery_action: str | None = None
    recovery_reason_contains: list[str] = Field(default_factory=list)
    recovery_hint_contains: list[str] = Field(default_factory=list)
    execution_plan_status: str | None = None
    latest_checkpoint_stage: str | None = None
    latest_checkpoint_next_action: str | None = None
    failed_step_exists: bool | None = None
    resume_from_step_exists: bool | None = None


class SessionFlowStepRuntimePatch(BaseModel):
    execution_error: str | None = None
    execution_tool_name: str | None = None
    execution_fail_call_count: int = 1


class SessionFlowEvalStep(BaseModel):
    step_id: str = ""
    action: Literal[
        "start_conversation",
        "post_message",
        "resume_conversation",
        "expire_approval",
        "cancel_approval",
        "tamper_latest_approval",
        "get_execution_recovery",
    ]
    request: dict[str, Any] = Field(default_factory=dict)
    runtime_patch: SessionFlowStepRuntimePatch = Field(default_factory=SessionFlowStepRuntimePatch)
    expect: SessionFlowStepExpectation = Field(default_factory=SessionFlowStepExpectation)

    @model_validator(mode="after")
    def _validate_request(self):
        self.build_request_model()
        return self

    def build_request_model(self):
        if self.action == "start_conversation":
            return ConversationCreateRequest.model_validate(self.request)
        if self.action == "post_message":
            return ConversationMessageRequest.model_validate(self.request)
        if self.action == "resume_conversation":
            return ConversationResumeRequest.model_validate(self.request)
        return dict(self.request)


class SessionFlowEvalCase(BaseModel):
    case_id: str
    description: str = ""
    setup: AgentEvalSetup = Field(default_factory=AgentEvalSetup)
    steps: list[SessionFlowEvalStep] = Field(default_factory=list)


class SessionFlowEvalGate(BaseModel):
    min_pass_rate: float | None = None
    min_step_pass_rate: float | None = None
    max_avg_duration_ms: float | None = None


class SessionFlowEvalDataset(BaseModel):
    schema_version: int = 1
    description: str = ""
    gate: SessionFlowEvalGate = Field(default_factory=SessionFlowEvalGate)
    cases: list[SessionFlowEvalCase] = Field(default_factory=list)


@dataclass
class SessionFlowStepObservation:
    action: str
    response_status: str
    session_status: str
    session_stage: str
    current_agent: str
    pending_interrupt_type: str
    message: str
    conclusion: str
    route: str
    intent: str
    stop_reason: str
    approval_required: bool
    primary_root_cause: str
    tool_names: list[str]
    tool_calls_used: int
    evidence: list[str]
    case_exists: bool
    human_verified: bool
    actual_root_cause_hypothesis: str
    current_intent_history_length: int
    message_event_type: str = ""
    message_event_topic_shift_detected: bool = False
    message_event_incremental_tool_domains: list[str] = field(default_factory=list)
    system_event_types: list[str] = field(default_factory=list)
    new_system_event_types: list[str] = field(default_factory=list)
    approval_event_types: list[str] = field(default_factory=list)
    new_approval_event_types: list[str] = field(default_factory=list)
    recovery_action: str = ""
    recovery_reason: str = ""
    recovery_hints: list[str] = field(default_factory=list)
    execution_plan_status: str = ""
    latest_checkpoint_stage: str = ""
    latest_checkpoint_next_action: str = ""
    resume_from_step_id: str = ""
    failed_step_id: str = ""
    raw_result: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionFlowStepResult:
    step_id: str
    action: str
    passed: bool
    score: float
    passed_checks: int
    total_checks: int
    observation: SessionFlowStepObservation | None = None
    checks: list[AgentEvalCheck] = field(default_factory=list)
    error: str = ""


@dataclass
class SessionFlowEvalCaseResult:
    case_id: str
    description: str
    passed: bool
    duration_ms: int
    step_results: list[SessionFlowStepResult] = field(default_factory=list)
    error: str = ""


@dataclass
class SessionFlowEvalReport:
    total_cases: int
    passed_cases: int
    failed_cases: int
    errored_cases: int
    pass_rate: float
    total_steps: int
    passed_steps: int
    step_pass_rate: float
    avg_duration_ms: float = 0.0
    gate_result: EvalGateResult | None = None
    results: list[SessionFlowEvalCaseResult] = field(default_factory=list)


def load_session_flow_eval_dataset(path: str | Path) -> SessionFlowEvalDataset:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return SessionFlowEvalDataset(cases=[SessionFlowEvalCase.model_validate(item) for item in payload])
    return SessionFlowEvalDataset.model_validate(payload)


def score_session_flow_step(
    expectation: SessionFlowStepExpectation,
    observation: SessionFlowStepObservation,
) -> AgentEvalScore:
    base_score = score_agent_eval_case(expectation, observation)
    checks = list(base_score.checks)

    def add_check(name: str, passed: bool, *, expected: Any = None, actual: Any = None, detail: str = "") -> None:
        checks.append(AgentEvalCheck(name=name, passed=passed, expected=expected, actual=actual, detail=detail))

    if expectation.response_status is not None:
        add_check(
            "response_status",
            observation.response_status == expectation.response_status,
            expected=expectation.response_status,
            actual=observation.response_status,
        )
    if expectation.session_status is not None:
        add_check(
            "session_status",
            observation.session_status == expectation.session_status,
            expected=expectation.session_status,
            actual=observation.session_status,
        )
    if expectation.session_stage is not None:
        add_check(
            "session_stage",
            observation.session_stage == expectation.session_stage,
            expected=expectation.session_stage,
            actual=observation.session_stage,
        )
    if expectation.current_agent is not None:
        add_check(
            "current_agent",
            observation.current_agent == expectation.current_agent,
            expected=expectation.current_agent,
            actual=observation.current_agent,
        )
    if expectation.message_event_type is not None:
        add_check(
            "message_event_type",
            observation.message_event_type == expectation.message_event_type,
            expected=expectation.message_event_type,
            actual=observation.message_event_type,
        )
    if expectation.message_event_topic_shift_detected is not None:
        add_check(
            "message_event_topic_shift_detected",
            observation.message_event_topic_shift_detected is expectation.message_event_topic_shift_detected,
            expected=expectation.message_event_topic_shift_detected,
            actual=observation.message_event_topic_shift_detected,
        )
    if expectation.message_event_incremental_tool_domains:
        missing = [
            item for item in expectation.message_event_incremental_tool_domains
            if item not in observation.message_event_incremental_tool_domains
        ]
        add_check(
            "message_event_incremental_tool_domains",
            not missing,
            expected=expectation.message_event_incremental_tool_domains,
            actual=observation.message_event_incremental_tool_domains,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.case_exists is not None:
        add_check(
            "case_exists",
            observation.case_exists is expectation.case_exists,
            expected=expectation.case_exists,
            actual=observation.case_exists,
        )
    if expectation.human_verified is not None:
        add_check(
            "human_verified",
            observation.human_verified is expectation.human_verified,
            expected=expectation.human_verified,
            actual=observation.human_verified,
        )
    if expectation.actual_root_cause_contains:
        joined = observation.actual_root_cause_hypothesis.lower()
        missing = [fragment for fragment in expectation.actual_root_cause_contains if fragment.lower() not in joined]
        add_check(
            "actual_root_cause_contains",
            not missing,
            expected=expectation.actual_root_cause_contains,
            actual=observation.actual_root_cause_hypothesis,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.new_system_event_types:
        missing = [item for item in expectation.new_system_event_types if item not in observation.new_system_event_types]
        add_check(
            "new_system_event_types",
            not missing,
            expected=expectation.new_system_event_types,
            actual=observation.new_system_event_types,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.new_approval_event_types:
        missing = [item for item in expectation.new_approval_event_types if item not in observation.new_approval_event_types]
        add_check(
            "new_approval_event_types",
            not missing,
            expected=expectation.new_approval_event_types,
            actual=observation.new_approval_event_types,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.min_current_intent_history_length is not None:
        add_check(
            "min_current_intent_history_length",
            observation.current_intent_history_length >= expectation.min_current_intent_history_length,
            expected=expectation.min_current_intent_history_length,
            actual=observation.current_intent_history_length,
        )
    if expectation.recovery_action is not None:
        add_check(
            "recovery_action",
            observation.recovery_action == expectation.recovery_action,
            expected=expectation.recovery_action,
            actual=observation.recovery_action,
        )
    if expectation.recovery_reason_contains:
        joined = observation.recovery_reason.lower()
        missing = [fragment for fragment in expectation.recovery_reason_contains if fragment.lower() not in joined]
        add_check(
            "recovery_reason_contains",
            not missing,
            expected=expectation.recovery_reason_contains,
            actual=observation.recovery_reason,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.recovery_hint_contains:
        joined = "\n".join(observation.recovery_hints).lower()
        missing = [fragment for fragment in expectation.recovery_hint_contains if fragment.lower() not in joined]
        add_check(
            "recovery_hint_contains",
            not missing,
            expected=expectation.recovery_hint_contains,
            actual=observation.recovery_hints,
            detail="" if not missing else f"missing={missing}",
        )
    if expectation.execution_plan_status is not None:
        add_check(
            "execution_plan_status",
            observation.execution_plan_status == expectation.execution_plan_status,
            expected=expectation.execution_plan_status,
            actual=observation.execution_plan_status,
        )
    if expectation.latest_checkpoint_stage is not None:
        add_check(
            "latest_checkpoint_stage",
            observation.latest_checkpoint_stage == expectation.latest_checkpoint_stage,
            expected=expectation.latest_checkpoint_stage,
            actual=observation.latest_checkpoint_stage,
        )
    if expectation.latest_checkpoint_next_action is not None:
        add_check(
            "latest_checkpoint_next_action",
            observation.latest_checkpoint_next_action == expectation.latest_checkpoint_next_action,
            expected=expectation.latest_checkpoint_next_action,
            actual=observation.latest_checkpoint_next_action,
        )
    if expectation.failed_step_exists is not None:
        add_check(
            "failed_step_exists",
            bool(observation.failed_step_id) is expectation.failed_step_exists,
            expected=expectation.failed_step_exists,
            actual=bool(observation.failed_step_id),
        )
    if expectation.resume_from_step_exists is not None:
        add_check(
            "resume_from_step_exists",
            bool(observation.resume_from_step_id) is expectation.resume_from_step_exists,
            expected=expectation.resume_from_step_exists,
            actual=bool(observation.resume_from_step_id),
        )

    passed_checks = sum(1 for check in checks if check.passed)
    total_checks = len(checks)
    score = 1.0 if total_checks == 0 else round(passed_checks / total_checks, 3)
    return AgentEvalScore(
        passed=passed_checks == total_checks,
        passed_checks=passed_checks,
        total_checks=total_checks,
        score=score,
        checks=checks,
    )


def build_session_flow_report(results: Sequence[SessionFlowEvalCaseResult]) -> SessionFlowEvalReport:
    total_cases = len(results)
    passed_cases = sum(1 for item in results if item.passed)
    errored_cases = sum(1 for item in results if item.error)
    failed_cases = sum(1 for item in results if not item.passed and not item.error)
    total_steps = sum(len(item.step_results) for item in results)
    passed_steps = sum(
        1
        for item in results
        for step in item.step_results
        if step.passed and not step.error
    )
    pass_rate = 1.0 if total_cases == 0 else round(passed_cases / total_cases, 3)
    step_pass_rate = 1.0 if total_steps == 0 else round(passed_steps / total_steps, 3)
    avg_duration_ms = round(sum(item.duration_ms for item in results) / total_cases, 3) if total_cases else 0.0
    return SessionFlowEvalReport(
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        errored_cases=errored_cases,
        pass_rate=pass_rate,
        total_steps=total_steps,
        passed_steps=passed_steps,
        step_pass_rate=step_pass_rate,
        avg_duration_ms=avg_duration_ms,
        results=list(results),
    )


def evaluate_session_flow_gate(
    gate: SessionFlowEvalGate,
    report: SessionFlowEvalReport,
) -> EvalGateResult | None:
    thresholds = gate.model_dump(exclude_none=True)
    if not thresholds:
        return None

    checks: list[AgentEvalCheck] = []

    def add_check(name: str, passed: bool, *, expected: Any = None, actual: Any = None, detail: str = "") -> None:
        checks.append(AgentEvalCheck(name=name, passed=passed, expected=expected, actual=actual, detail=detail))

    if gate.min_pass_rate is not None:
        add_check(
            "min_pass_rate",
            report.pass_rate >= gate.min_pass_rate,
            expected=gate.min_pass_rate,
            actual=report.pass_rate,
        )
    if gate.min_step_pass_rate is not None:
        add_check(
            "min_step_pass_rate",
            report.step_pass_rate >= gate.min_step_pass_rate,
            expected=gate.min_step_pass_rate,
            actual=report.step_pass_rate,
        )
    if gate.max_avg_duration_ms is not None:
        add_check(
            "max_avg_duration_ms",
            report.avg_duration_ms <= gate.max_avg_duration_ms,
            expected=gate.max_avg_duration_ms,
            actual=report.avg_duration_ms,
        )

    passed_checks = sum(1 for check in checks if check.passed)
    total_checks = len(checks)
    return EvalGateResult(
        passed=passed_checks == total_checks,
        passed_checks=passed_checks,
        total_checks=total_checks,
        checks=checks,
    )


class SessionFlowEvalRunner(AgentEvalRunner):
    async def run_case(self, case: SessionFlowEvalCase) -> SessionFlowEvalCaseResult:
        started_at = perf_counter()
        with TemporaryDirectory() as temp_dir:
            try:
                db_path = str(Path(temp_dir) / f"{case.case_id}.db")
                orchestrator = self._build_orchestrator(db_path)
                if self.configure_orchestrator is not None:
                    self.configure_orchestrator(orchestrator)
                if case.setup.llm_mode == "disabled":
                    orchestrator.react_supervisor.llm = DisabledEvalLLM()
                if self.require_llm_enabled and case.setup.llm_mode != "disabled" and not orchestrator.react_supervisor.llm.enabled:
                    raise RuntimeError("LLM is not enabled by current settings")

                session_id = ""
                step_results: list[SessionFlowStepResult] = []
                seen_system_event_ids: set[str] = set()
                seen_approval_event_keys: set[tuple[str, str, str, str]] = set()
                known_approval_ids: list[str] = []

                for index, step in enumerate(case.steps, start=1):
                    result, session_id = await self._run_step(
                        orchestrator=orchestrator,
                        case=case,
                        step=step,
                        step_index=index,
                        session_id=session_id,
                    )
                    observation = self._build_step_observation(
                        orchestrator=orchestrator,
                        action=step.action,
                        result=result,
                        session_id=session_id,
                        seen_system_event_ids=seen_system_event_ids,
                        seen_approval_event_keys=seen_approval_event_keys,
                        known_approval_ids=known_approval_ids,
                    )
                    score = score_session_flow_step(step.expect, observation)
                    step_results.append(
                        SessionFlowStepResult(
                            step_id=step.step_id or f"step-{index}",
                            action=step.action,
                            passed=score.passed,
                            score=score.score,
                            passed_checks=score.passed_checks,
                            total_checks=score.total_checks,
                            observation=observation,
                            checks=score.checks,
                        )
                    )

                duration_ms = int((perf_counter() - started_at) * 1000)
                return SessionFlowEvalCaseResult(
                    case_id=case.case_id,
                    description=case.description,
                    passed=all(item.passed for item in step_results),
                    duration_ms=duration_ms,
                    step_results=step_results,
                )
            except Exception as exc:
                duration_ms = int((perf_counter() - started_at) * 1000)
                return SessionFlowEvalCaseResult(
                    case_id=case.case_id,
                    description=case.description,
                    passed=False,
                    duration_ms=duration_ms,
                    error=f"{exc.__class__.__name__}: {exc}",
                )

    async def run_dataset(
        self,
        dataset: SessionFlowEvalDataset,
        *,
        selected_case_ids: Sequence[str] | None = None,
        fail_fast: bool = False,
    ) -> SessionFlowEvalReport:
        selected = set(selected_case_ids or [])
        cases = [case for case in dataset.cases if not selected or case.case_id in selected]
        results: list[SessionFlowEvalCaseResult] = []
        for case in cases:
            case_result = await self.run_case(case)
            results.append(case_result)
            if fail_fast and (case_result.error or not case_result.passed):
                break
        return build_session_flow_report(results)

    async def _run_step(
        self,
        *,
        orchestrator,
        case: SessionFlowEvalCase,
        step: SessionFlowEvalStep,
        step_index: int,
        session_id: str,
    ) -> tuple[dict[str, Any], str]:
        with self._build_runtime_patch_context(step.runtime_patch):
            if step.action == "start_conversation":
                create_request = step.build_request_model()
                request = self._build_request(
                    AgentEvalCase(
                        case_id=f"{case.case_id}:{step.step_id or step_index}",
                        request=create_request,
                        setup=case.setup,
                    )
                )
                result = await orchestrator.start_conversation(request)
                next_session_id = str(((result.get("session") or {}) if isinstance(result, dict) else {}).get("session_id") or "")
                if not next_session_id:
                    raise RuntimeError("start_conversation did not return session_id")
                return result, next_session_id
            if not session_id:
                raise RuntimeError(f"{step.action} requires an existing session_id")
            if step.action == "post_message":
                result = await orchestrator.post_message(session_id, step.build_request_model())
                return result, session_id
            if step.action == "resume_conversation":
                result = await orchestrator.resume_conversation(session_id, step.build_request_model())
                return result, session_id
            if step.action == "tamper_latest_approval":
                approval = self._resolve_approval_for_step(
                    orchestrator=orchestrator,
                    session_id=session_id,
                    request_payload=step.build_request_model(),
                )
                self._tamper_approval_request(orchestrator=orchestrator, approval=approval, request_payload=step.request)
                return {
                    "status": "completed",
                    "message": "已按评估场景篡改待审批 request。",
                }, session_id
            if step.action == "get_execution_recovery":
                return {
                    "status": "completed",
                    "message": "已读取执行恢复信息。",
                }, session_id
            approval = self._resolve_approval_for_step(
                orchestrator=orchestrator,
                session_id=session_id,
                request_payload=step.build_request_model(),
            )
            if step.action == "expire_approval":
                result = await orchestrator.expire_approval(
                    approval,
                    actor_id=str(step.request.get("actor_id") or "system"),
                    comment=str(step.request.get("comment") or "") or None,
                )
                return result, session_id
            result = await orchestrator.cancel_approval(
                approval,
                actor_id=str(step.request.get("actor_id") or "system"),
                comment=str(step.request.get("comment") or "") or None,
            )
            return result, session_id

    def _build_step_observation(
        self,
        *,
        orchestrator,
        action: str,
        result: dict[str, Any],
        session_id: str,
        seen_system_event_ids: set[str],
        seen_approval_event_keys: set[tuple[str, str, str, str]],
        known_approval_ids: list[str],
    ) -> SessionFlowStepObservation:
        base = extract_eval_observation(result)
        detail = orchestrator.get_conversation(session_id) or {}
        session = dict(detail.get("session") or {})
        pending_interrupt = dict(detail.get("pending_interrupt") or result.get("pending_interrupt") or {})
        diagnosis = dict(result.get("diagnosis") or {})
        message_event = dict(diagnosis.get("message_event") or {})
        system_events = list(orchestrator.list_system_events(session_id, limit=200) or [])
        new_system_event_types: list[str] = []
        system_event_types: list[str] = []
        for event in system_events:
            event_id = str(event.get("event_id") or "")
            event_type = str(event.get("event_type") or "")
            if event_type and event_type not in system_event_types:
                system_event_types.append(event_type)
            if event_id and event_id not in seen_system_event_ids:
                seen_system_event_ids.add(event_id)
                if event_type and event_type not in new_system_event_types:
                    new_system_event_types.append(event_type)

        for approval_id in self._extract_approval_ids(result=result, session=session, pending_interrupt=pending_interrupt):
            if approval_id not in known_approval_ids:
                known_approval_ids.append(approval_id)

        approval_event_types: list[str] = []
        new_approval_event_types: list[str] = []
        for approval_id in known_approval_ids:
            for event in list(orchestrator.list_approval_events(approval_id) or []):
                event_type = str(event.get("event_type") or "")
                created_at = str(event.get("created_at") or "")
                actor_id = str(event.get("actor_id") or "")
                event_key = (approval_id, event_type, created_at, actor_id)
                if event_type and event_type not in approval_event_types:
                    approval_event_types.append(event_type)
                if event_key not in seen_approval_event_keys:
                    seen_approval_event_keys.add(event_key)
                    if event_type and event_type not in new_approval_event_types:
                        new_approval_event_types.append(event_type)

        incident_case = orchestrator.incident_case_store.get_by_session_id(session_id)
        session_memory = dict(session.get("session_memory") or {})
        current_intent_history = list(session_memory.get("current_intent_history") or [])
        recovery = orchestrator.get_execution_recovery(session_id) or {}
        latest_checkpoint = dict(recovery.get("latest_checkpoint") or {})
        execution_plan = dict(recovery.get("execution_plan") or {})

        return SessionFlowStepObservation(
            action=action,
            response_status=str(result.get("status") or ""),
            session_status=str(session.get("status") or ""),
            session_stage=str(session.get("current_stage") or ""),
            current_agent=str(session.get("current_agent") or ""),
            pending_interrupt_type=str(pending_interrupt.get("type") or ""),
            message_event_type=str(message_event.get("event_type") or ""),
            message_event_topic_shift_detected=bool(message_event.get("topic_shift_detected")),
            message_event_incremental_tool_domains=[
                str(item)
                for item in list(message_event.get("incremental_tool_domains") or [])
                if str(item or "").strip()
            ],
            message=base.message,
            conclusion=base.conclusion,
            route=base.route,
            intent=base.intent,
            stop_reason=base.stop_reason,
            approval_required=base.approval_required,
            primary_root_cause=base.primary_root_cause,
            tool_names=list(base.tool_names),
            tool_calls_used=base.tool_calls_used,
            evidence=list(base.evidence),
            case_exists=incident_case is not None,
            human_verified=bool((incident_case or {}).get("human_verified")) if isinstance(incident_case, dict) else False,
            actual_root_cause_hypothesis=str((incident_case or {}).get("actual_root_cause_hypothesis") or "")
            if isinstance(incident_case, dict)
            else "",
            current_intent_history_length=len(current_intent_history),
            system_event_types=system_event_types,
            new_system_event_types=new_system_event_types,
            approval_event_types=approval_event_types,
            new_approval_event_types=new_approval_event_types,
            recovery_action=str(recovery.get("recovery_action") or ""),
            recovery_reason=str(recovery.get("reason") or ""),
            recovery_hints=list(recovery.get("recovery_hints") or []),
            execution_plan_status=str(execution_plan.get("status") or ""),
            latest_checkpoint_stage=str(latest_checkpoint.get("stage") or ""),
            latest_checkpoint_next_action=str(latest_checkpoint.get("next_action") or ""),
            resume_from_step_id=str(recovery.get("resume_from_step_id") or ""),
            failed_step_id=str(recovery.get("failed_step_id") or ""),
            raw_result=dict(result),
        )

    @staticmethod
    def _extract_approval_ids(
        *,
        result: dict[str, Any],
        session: dict[str, Any],
        pending_interrupt: dict[str, Any],
    ) -> list[str]:
        approval_ids: list[str] = []

        def append(value: Any) -> None:
            approval_id = str(value or "").strip()
            if approval_id and approval_id not in approval_ids:
                approval_ids.append(approval_id)

        approval_request = result.get("approval_request")
        approval_request = dict(approval_request) if isinstance(approval_request, dict) else {}
        append(approval_request.get("approval_id"))
        append(session.get("latest_approval_id"))
        metadata = dict(pending_interrupt.get("metadata") or {})
        append(metadata.get("approval_id"))
        diagnosis = dict(result.get("diagnosis") or {})
        approval = dict(diagnosis.get("approval") or {})
        append(approval.get("approval_id"))
        return approval_ids

    @staticmethod
    def _resolve_approval_for_step(
        *,
        orchestrator,
        session_id: str,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        approval_id = str(request_payload.get("approval_id") or "").strip()
        if not approval_id:
            detail = orchestrator.get_conversation(session_id) or {}
            session = dict(detail.get("session") or {})
            approval_id = str(session.get("latest_approval_id") or "").strip()
        if not approval_id:
            raise RuntimeError("approval action requires latest_approval_id or request.approval_id")
        approval = orchestrator.approval_store.get(approval_id)
        if approval is None:
            raise RuntimeError(f"approval not found: {approval_id}")
        return approval

    @staticmethod
    def _tamper_approval_request(*, orchestrator, approval: dict[str, Any], request_payload: Mapping[str, Any]) -> None:
        proposal_index = int(request_payload.get("proposal_index") or 0)
        proposal_patch = dict(request_payload.get("proposal_patch") or {})
        context_patch = dict(request_payload.get("context_patch") or {})

        approval_id = str(approval.get("approval_id") or "").strip()
        raw_params = dict(approval.get("params") or {})
        proposals = list(approval.get("proposals") or raw_params.get("proposals") or [])
        context = dict(approval.get("context") or {})

        if (not proposals or not context) and approval_id:
            approval_request = orchestrator.approval_store.get_request(approval_id)
            if approval_request is not None:
                if not proposals:
                    proposals = [
                        item.model_dump() if hasattr(item, "model_dump") else dict(item)
                        for item in list(approval_request.proposals or [])
                    ]
                if not context:
                    context = dict(approval_request.context or {})

        if not proposals:
            raise RuntimeError("approval has no proposals to tamper")
        if proposal_index < 0 or proposal_index >= len(proposals):
            raise RuntimeError(f"proposal_index out of range: {proposal_index}")

        proposals[proposal_index] = _deep_merge_dicts(dict(proposals[proposal_index]), proposal_patch)
        context = _deep_merge_dicts(context, context_patch)

        db_path = str(orchestrator.approval_store.db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                update approval_request_v2
                set proposals_json = ?, context_json = ?
                where approval_id = ?
                """,
                (
                    json.dumps(proposals, ensure_ascii=False),
                    json.dumps(context, ensure_ascii=False),
                    approval_id,
                ),
            )
            conn.commit()

    @staticmethod
    def _build_runtime_patch_context(runtime_patch: SessionFlowStepRuntimePatch):
        execution_error = str(runtime_patch.execution_error or "").strip()
        if not execution_error:
            return nullcontext()

        target_tool = str(runtime_patch.execution_tool_name or "").strip()
        fail_call_count = max(int(runtime_patch.execution_fail_call_count or 1), 1)
        state = {"remaining_failures": fail_call_count}
        original_mcp_call = MCPClient.call_tool
        original_local_execute = LocalToolRuntime.execute_action

        def should_fail(action_name: str) -> bool:
            return state["remaining_failures"] > 0 and (not target_tool or action_name == target_tool)

        async def patched_mcp_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            action_name = str(name)
            if should_fail(action_name):
                state["remaining_failures"] -= 1
                return {
                    "structuredContent": {
                        "status": "failed",
                        "error": execution_error,
                        "action": action_name,
                    },
                    "content": [{"text": execution_error}],
                }
            return await original_mcp_call(self, name, arguments)

        async def patched_local_execute(self, action: str, *, params: dict[str, object], incident_state=None) -> dict[str, object]:
            action_name = str(action)
            if should_fail(action_name):
                state["remaining_failures"] -= 1
                return {
                    "ticket_id": incident_state.ticket_id if incident_state is not None else "",
                    "status": "failed",
                    "message": f"审批已通过，但执行失败：{execution_error}",
                    "diagnosis": {
                        "execution": {
                            "status": "failed",
                            "action": action_name,
                            "error": execution_error,
                        }
                    },
                    "structuredContent": {
                        "status": "failed",
                        "action": action_name,
                        "error": execution_error,
                    },
                    "content": [{"text": execution_error}],
                }
            return await original_local_execute(self, action, params=params, incident_state=incident_state)

        stack = ExitStack()
        stack.enter_context(patch("it_ticket_agent.graph.nodes.MCPClient.call_tool", new=patched_mcp_call))
        stack.enter_context(patch("it_ticket_agent.graph.nodes.LocalToolRuntime.execute_action", new=patched_local_execute))
        return stack


def _deep_merge_dicts(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge_dicts(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def serialize_session_flow_report(report: SessionFlowEvalReport) -> dict[str, Any]:
    return {
        "total_cases": report.total_cases,
        "passed_cases": report.passed_cases,
        "failed_cases": report.failed_cases,
        "errored_cases": report.errored_cases,
        "pass_rate": report.pass_rate,
        "total_steps": report.total_steps,
        "passed_steps": report.passed_steps,
        "step_pass_rate": report.step_pass_rate,
        "avg_duration_ms": report.avg_duration_ms,
        "gate_result": (
            {
                "passed": report.gate_result.passed,
                "passed_checks": report.gate_result.passed_checks,
                "total_checks": report.gate_result.total_checks,
                "checks": [asdict(check) for check in report.gate_result.checks],
            }
            if report.gate_result is not None
            else None
        ),
        "results": [
            {
                "case_id": item.case_id,
                "description": item.description,
                "passed": item.passed,
                "duration_ms": item.duration_ms,
                "error": item.error,
                "step_results": [
                    {
                        "step_id": step.step_id,
                        "action": step.action,
                        "passed": step.passed,
                        "score": step.score,
                        "passed_checks": step.passed_checks,
                        "total_checks": step.total_checks,
                        "error": step.error,
                        "checks": [asdict(check) for check in step.checks],
                        "observation": asdict(step.observation) if step.observation is not None else None,
                    }
                    for step in item.step_results
                ],
            }
            for item in report.results
        ],
    }
