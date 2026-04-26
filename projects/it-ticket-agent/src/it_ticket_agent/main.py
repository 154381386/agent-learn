from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .observability import configure_observability
from .runtime.orchestrator import SupervisorOrchestrator
from .schemas import (
    ApprovalDecisionRequest,
    ApprovalEventResponse,
    ApprovalResolutionRequest,
    BadCaseCandidateExportStatusRequest,
    BadCaseCandidateResponse,
    BadCaseCuratedMergeRequest,
    BadCaseCuratedMergeResponse,
    BadCaseEvalSkeletonExportRequest,
    BadCaseEvalSkeletonExportResponse,
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationMessageRequest,
    ConversationMutationResponse,
    DiagnosisPlaybookResponse,
    DiagnosisPlaybookReviewRequest,
    DiagnosisPlaybookUpsertRequest,
    ConversationResumeRequest,
    PlaybookExtractionRequest,
    PlaybookExtractionResponse,
    ExecutionPlanResponse,
    ExecutionRecoveryResponse,
    IncidentCaseResponse,
    IncidentCaseReviewRequest,
    IncidentCaseReviewResponse,
    InterruptResponse,
    MockWorldResponse,
    RuntimeSnapshotResponse,
    SessionResponse,
    SystemEventResponse,
    TicketRequest,
    TicketResponse,
)
from .evals import export_bad_case_candidates, merge_curated_bad_case_files
from .session import SessionService
from .settings import get_settings
from .storage import StoreProvider


settings = get_settings()
static_dir = Path(__file__).parent / "static"
project_root = Path(__file__).resolve().parents[2]
default_generated_eval_dir = project_root / "data" / "evals" / "generated"
default_mock_worlds_path = project_root / "data" / "mock_case_profiles.json"



def _normalize_mock_tool_response(value: dict) -> dict:
    payload = value.get("payload") if isinstance(value.get("payload"), dict) else {
        key: item
        for key, item in value.items()
        if key not in {"status", "risk", "summary", "evidence"}
    }
    normalized = {"status": str(value.get("status") or "completed"), "payload": dict(payload)}
    if value.get("risk"):
        normalized["risk"] = str(value.get("risk"))
    return normalized

def _describe_mock_world(case_id: str, service: str, tool_payloads: dict[str, dict]) -> str:
    domain_tools = {
        "发布/变更": {"check_recent_deployments", "check_pipeline_status", "check_canary_status", "get_deployment_status", "get_change_records", "get_rollback_history"},
        "K8s": {"check_pod_status", "inspect_pod_logs", "inspect_pod_events", "inspect_jvm_memory", "inspect_cpu_saturation", "inspect_thread_pool_status"},
        "网络": {"inspect_dns_resolution", "inspect_ingress_route", "inspect_vpc_connectivity", "inspect_load_balancer_status", "inspect_upstream_dependency", "inspect_egress_policy"},
        "数据库": {"inspect_db_instance_health", "inspect_connection_pool", "inspect_slow_queries", "inspect_replication_status", "inspect_deadlock_signals"},
        "监控": {"check_service_health", "check_recent_alerts", "inspect_error_budget_burn"},
    }
    tool_names = set(tool_payloads.keys())
    domains = [label for label, names in domain_tools.items() if tool_names.intersection(names)]
    domain_text = "、".join(domains[:5]) if domains else "通用诊断"
    return f"{service} / {case_id} mock 世界，包含 {len(tool_payloads)} 个工具返回，覆盖 {domain_text}。"


def _load_mock_worlds() -> list[MockWorldResponse]:
    if not default_mock_worlds_path.exists():
        return []
    payload = json.loads(default_mock_worlds_path.read_text(encoding="utf-8"))
    worlds: list[MockWorldResponse] = []
    if not isinstance(payload, dict):
        return worlds
    for case_id, case_payload in sorted(payload.items()):
        if not isinstance(case_payload, dict):
            continue
        services = case_payload.get("services")
        if not isinstance(services, dict):
            continue
        title = str(case_payload.get("title") or "").strip()
        case_description = str(case_payload.get("description") or "").strip()
        for service, tool_payloads in sorted(services.items()):
            if not isinstance(tool_payloads, dict):
                continue
            normalized_tools = {str(name): _normalize_mock_tool_response(value) for name, value in tool_payloads.items() if isinstance(value, dict)}
            world_id = f"{case_id}::{service}"
            worlds.append(
                MockWorldResponse(
                    world_id=world_id,
                    case_id=str(case_id),
                    service=str(service),
                    label=f"{title} / {service}" if title else f"{case_id} / {service}",
                    description=case_description or _describe_mock_world(str(case_id), str(service), normalized_tools),
                    difficulty=str(case_payload.get("difficulty") or ""),
                    tags=[str(item) for item in list(case_payload.get("tags") or [])],
                    user_prompt_templates=[str(item) for item in list(case_payload.get("user_prompt_templates") or [])],
                    noise_factors=[str(item) for item in list(case_payload.get("noise_factors") or [])],
                    evaluation_focus=[str(item) for item in list(case_payload.get("evaluation_focus") or [])],
                    expected_diagnosis=dict(case_payload.get("expected_diagnosis") or {}),
                    tool_count=len(normalized_tools),
                    tool_names=sorted(normalized_tools.keys()),
                    mock_tool_responses=normalized_tools,
                )
            )
    return worlds


@asynccontextmanager
async def lifespan(app: FastAPI):
    observability = configure_observability(settings)
    stores = StoreProvider(settings).build()
    approval_store = stores.approval_store
    session_store = stores.session_store
    session_service = SessionService(session_store)
    interrupt_store = stores.interrupt_store
    checkpoint_store = stores.checkpoint_store
    process_memory_store = stores.process_memory_store
    execution_store = stores.execution_store
    incident_case_store = stores.incident_case_store
    playbook_store = stores.playbook_store
    bad_case_candidate_store = stores.bad_case_candidate_store
    system_event_store = stores.system_event_store
    app.state.supervisor_orchestrator = SupervisorOrchestrator(
        settings,
        approval_store,
        session_store,
        interrupt_store,
        checkpoint_store,
        process_memory_store,
        execution_store=execution_store,
        session_service=session_service,
        incident_case_store=incident_case_store,
        playbook_store=playbook_store,
        bad_case_candidate_store=bad_case_candidate_store,
        system_event_store=system_event_store,
    )
    app.state.approval_store = approval_store
    app.state.session_store = session_store
    app.state.session_service = session_service
    app.state.interrupt_store = interrupt_store
    app.state.checkpoint_store = checkpoint_store
    app.state.process_memory_store = process_memory_store
    app.state.execution_store = execution_store
    app.state.incident_case_store = incident_case_store
    app.state.playbook_store = playbook_store
    app.state.bad_case_candidate_store = bad_case_candidate_store
    app.state.system_event_store = system_event_store
    app.state.observability = observability
    try:
        yield
    finally:
        observability.flush()
        observability.shutdown()


app = FastAPI(title="IT Ticket Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "observability": {"langfuse_enabled": bool(settings.langfuse_public_key and settings.langfuse_secret_key)}}


@app.get("/api/v1/mock-worlds", response_model=list[MockWorldResponse])
async def list_mock_worlds():
    return _load_mock_worlds()


@app.post("/api/v1/tickets", response_model=TicketResponse)
async def create_ticket(request: TicketRequest, http_request: Request):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    result = await supervisor_orchestrator.handle_ticket(request)
    return TicketResponse(
        ticket_id=request.ticket_id,
        status=result["status"],
        message=result["message"],
        approval_request=result.get("approval_request"),
        diagnosis=result.get("diagnosis"),
    )


@app.post("/api/v1/conversations", response_model=ConversationMutationResponse)
async def create_conversation(request: ConversationCreateRequest, http_request: Request):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    result = await supervisor_orchestrator.start_conversation(request)
    return ConversationMutationResponse(**result)


@app.post("/api/v1/conversations/{session_id}/messages", response_model=ConversationMutationResponse)
async def post_conversation_message(session_id: str, request: ConversationMessageRequest, http_request: Request):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    try:
        result = await supervisor_orchestrator.post_message(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ConversationMutationResponse(**result)


@app.post("/api/v1/conversations/{session_id}/resume", response_model=ConversationMutationResponse)
async def resume_conversation(session_id: str, request: ConversationResumeRequest, http_request: Request):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    try:
        result = await supervisor_orchestrator.resume_conversation(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ConversationMutationResponse(**result)


@app.get("/api/v1/conversations/{session_id}", response_model=ConversationDetailResponse)
async def get_conversation(session_id: str, http_request: Request):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    detail = supervisor_orchestrator.get_conversation(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationDetailResponse(**detail)


@app.post("/api/v1/approvals/{approval_id}/decision", response_model=TicketResponse)
async def decide_approval(approval_id: str, request: ApprovalDecisionRequest, http_request: Request):
    approval_store = http_request.app.state.approval_store
    approval = approval_store.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")

    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    result = await supervisor_orchestrator.handle_approval_decision(approval, request)
    return TicketResponse(
        ticket_id=approval["ticket_id"],
        status=result["status"],
        message=result["message"],
        diagnosis=result.get("diagnosis"),
    )


@app.post("/api/v1/approvals/{approval_id}/expire", response_model=TicketResponse)
async def expire_approval(approval_id: str, request: ApprovalResolutionRequest, http_request: Request):
    approval_store = http_request.app.state.approval_store
    approval = approval_store.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")

    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    result = await supervisor_orchestrator.expire_approval(approval, actor_id=request.actor_id, comment=request.comment)
    return TicketResponse(
        ticket_id=approval["ticket_id"],
        status=result["status"],
        message=result["message"],
        diagnosis=result.get("diagnosis"),
    )


@app.post("/api/v1/approvals/{approval_id}/cancel", response_model=TicketResponse)
async def cancel_approval(approval_id: str, request: ApprovalResolutionRequest, http_request: Request):
    approval_store = http_request.app.state.approval_store
    approval = approval_store.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")

    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    result = await supervisor_orchestrator.cancel_approval(approval, actor_id=request.actor_id, comment=request.comment)
    return TicketResponse(
        ticket_id=approval["ticket_id"],
        status=result["status"],
        message=result["message"],
        diagnosis=result.get("diagnosis"),
    )


@app.get("/api/v1/approvals/{approval_id}/events", response_model=list[ApprovalEventResponse])
async def list_approval_events(approval_id: str, http_request: Request):
    approval_store = http_request.app.state.approval_store
    approval = approval_store.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return [ApprovalEventResponse(**event) for event in approval_store.list_events(approval_id)]



@app.get("/api/v1/sessions", response_model=list[SessionResponse])
async def list_sessions(
    http_request: Request,
    user_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
):
    session_service = http_request.app.state.session_service
    bounded_limit = max(1, min(int(limit or 20), 50))
    sessions = session_service.list_sessions(limit=bounded_limit, user_id=user_id, status=status)
    return [SessionResponse(**session) for session in sessions]


@app.get("/api/v1/sessions/{session_id}/events", response_model=list[SystemEventResponse])
async def list_system_events(session_id: str, http_request: Request, limit: int = 100):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    session = supervisor_orchestrator.get_conversation(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return [SystemEventResponse(**event) for event in supervisor_orchestrator.list_system_events(session_id, limit=limit)]


@app.get("/api/v1/sessions/{session_id}/runtime", response_model=RuntimeSnapshotResponse)
async def get_runtime_snapshot(session_id: str, http_request: Request):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    snapshot = supervisor_orchestrator.get_runtime_snapshot(session_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="session not found")
    return RuntimeSnapshotResponse(**snapshot)


@app.get("/api/v1/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, http_request: Request):
    session_service = http_request.app.state.session_service
    session = session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return SessionResponse(**session)


@app.get("/api/v1/sessions/by-thread/{thread_id}", response_model=SessionResponse)
async def get_session_by_thread(thread_id: str, http_request: Request):
    session_service = http_request.app.state.session_service
    session = session_service.get_session_by_thread_id(thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return SessionResponse(**session)


@app.get("/api/v1/sessions/{session_id}/execution-recovery", response_model=ExecutionRecoveryResponse)
async def get_execution_recovery(session_id: str, http_request: Request):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    recovery = supervisor_orchestrator.get_execution_recovery(session_id)
    if recovery is None:
        raise HTTPException(status_code=404, detail="session not found")
    return ExecutionRecoveryResponse(**recovery)


@app.get("/api/v1/sessions/{session_id}/execution-plans", response_model=list[ExecutionPlanResponse])
async def list_execution_plans(session_id: str, http_request: Request):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    return [ExecutionPlanResponse(**plan) for plan in supervisor_orchestrator.list_execution_plans(session_id)]


@app.get("/api/v1/execution-plans/{plan_id}", response_model=ExecutionPlanResponse)
async def get_execution_plan(plan_id: str, http_request: Request):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    plan = supervisor_orchestrator.get_execution_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="execution plan not found")
    return ExecutionPlanResponse(**plan)


@app.get("/api/v1/interrupts", response_model=list[InterruptResponse])
async def list_interrupts(http_request: Request, status: str | None = None, session_id: str | None = None, ticket_id: str | None = None):
    interrupt_store = http_request.app.state.interrupt_store
    if status == "pending":
        interrupts = interrupt_store.get_pending(session_id=session_id, ticket_id=ticket_id)
        return [InterruptResponse(**interrupt) for interrupt in interrupts]
    return []


@app.get("/api/v1/interrupts/{interrupt_id}", response_model=InterruptResponse)
async def get_interrupt(interrupt_id: str, http_request: Request):
    interrupt_store = http_request.app.state.interrupt_store
    interrupt = interrupt_store.get(interrupt_id)
    if interrupt is None:
        raise HTTPException(status_code=404, detail="interrupt not found")
    return InterruptResponse(**interrupt)


@app.get("/api/v1/playbooks", response_model=list[DiagnosisPlaybookResponse])
async def list_playbooks(
    http_request: Request,
    status: str | None = None,
    human_verified: bool | None = None,
    service_type: str | None = None,
    failure_mode: str | None = None,
    environment: str | None = None,
    keyword: str | None = None,
    limit: int = 20,
):
    playbook_store = http_request.app.state.playbook_store
    playbooks = playbook_store.list_playbooks(
        status=status,
        human_verified=human_verified,
        service_type=service_type,
        failure_mode=failure_mode,
        environment=environment,
        keyword=keyword,
        limit=limit,
    )
    return [DiagnosisPlaybookResponse(**playbook) for playbook in playbooks]


@app.post("/api/v1/playbooks", response_model=DiagnosisPlaybookResponse)
async def upsert_playbook(payload: DiagnosisPlaybookUpsertRequest, http_request: Request):
    playbook_store = http_request.app.state.playbook_store
    data = payload.model_dump(exclude_none=True)
    playbook = playbook_store.upsert(data)
    return DiagnosisPlaybookResponse(**playbook)


@app.get("/api/v1/playbooks/{playbook_id}", response_model=DiagnosisPlaybookResponse)
async def get_playbook(playbook_id: str, http_request: Request):
    playbook_store = http_request.app.state.playbook_store
    playbook = playbook_store.get(playbook_id)
    if playbook is None:
        raise HTTPException(status_code=404, detail="playbook not found")
    return DiagnosisPlaybookResponse(**playbook)


@app.post("/api/v1/playbooks/{playbook_id}/review", response_model=DiagnosisPlaybookResponse)
async def review_playbook(playbook_id: str, payload: DiagnosisPlaybookReviewRequest, http_request: Request):
    playbook_store = http_request.app.state.playbook_store
    playbook = playbook_store.review(
        playbook_id,
        human_verified=payload.human_verified,
        status=payload.status,
        reviewed_by=payload.reviewed_by,
        review_note=payload.review_note,
    )
    if playbook is None:
        raise HTTPException(status_code=404, detail="playbook not found")
    return DiagnosisPlaybookResponse(**playbook)


@app.get("/api/v1/cases", response_model=list[IncidentCaseResponse])
async def list_cases(
    http_request: Request,
    service: str | None = None,
    final_action: str | None = None,
    approval_required: bool | None = None,
    verification_passed: bool | None = None,
    case_status: str | None = None,
    human_verified: bool | None = None,
    keyword: str | None = None,
    limit: int = 20,
):
    incident_case_store = http_request.app.state.incident_case_store
    cases = incident_case_store.list_cases(
        service=service,
        final_action=final_action,
        approval_required=approval_required,
        verification_passed=verification_passed,
        case_status=case_status,
        human_verified=human_verified,
        keyword=keyword,
        limit=limit,
    )
    return [IncidentCaseResponse(**case) for case in cases]


@app.post("/api/v1/cases/{case_id}/review", response_model=IncidentCaseReviewResponse)
async def review_case(case_id: str, payload: IncidentCaseReviewRequest, http_request: Request):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    result = supervisor_orchestrator.review_incident_case(
        case_id,
        human_verified=payload.human_verified,
        hypothesis_accuracy=payload.hypothesis_accuracy,
        actual_root_cause_hypothesis=payload.actual_root_cause_hypothesis,
        reviewed_by=payload.reviewed_by,
        review_note=payload.review_note,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="case not found")
    playbook_candidate = result.get("playbook_candidate")
    return IncidentCaseReviewResponse(
        incident_case=IncidentCaseResponse(**result["incident_case"]),
        playbook_candidate=(
            DiagnosisPlaybookResponse(**playbook_candidate)
            if isinstance(playbook_candidate, dict)
            else None
        ),
        playbook_extraction=dict(result.get("playbook_extraction") or {}),
    )


@app.post("/api/v1/cases/{case_id}/extract-playbook", response_model=PlaybookExtractionResponse)
async def extract_case_playbook(case_id: str, payload: PlaybookExtractionRequest, http_request: Request):
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    result = supervisor_orchestrator.extract_playbook_candidate_from_case(
        case_id,
        allow_single_case=payload.allow_single_case,
        min_cases=payload.min_cases,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="case not found")
    playbook_candidate = result.get("playbook_candidate")
    return PlaybookExtractionResponse(
        incident_case=IncidentCaseResponse(**result["incident_case"]),
        playbook_candidate=(
            DiagnosisPlaybookResponse(**playbook_candidate)
            if isinstance(playbook_candidate, dict)
            else None
        ),
        extracted=bool(result.get("extracted")),
        reason=str(result.get("reason") or ""),
        related_case_count=int(result.get("related_case_count") or 0),
    )


@app.get("/api/v1/cases/{case_id}", response_model=IncidentCaseResponse)
async def get_case(case_id: str, http_request: Request):
    incident_case_store = http_request.app.state.incident_case_store
    case = incident_case_store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return IncidentCaseResponse(**case)


@app.get("/api/v1/bad-case-candidates", response_model=list[BadCaseCandidateResponse])
async def list_bad_case_candidates(
    http_request: Request,
    session_id: str | None = None,
    source: str | None = None,
    export_status: str | None = None,
    limit: int = 50,
):
    bad_case_candidate_store = http_request.app.state.bad_case_candidate_store
    candidates = bad_case_candidate_store.list_candidates(
        session_id=session_id,
        source=source,
        export_status=export_status,
        limit=limit,
    )
    return [BadCaseCandidateResponse(**candidate) for candidate in candidates]



@app.post("/api/v1/bad-case-candidates/{candidate_id}/export-eval-skeleton", response_model=BadCaseEvalSkeletonExportResponse)
async def export_bad_case_eval_skeleton(
    candidate_id: str,
    payload: BadCaseEvalSkeletonExportRequest,
    http_request: Request,
):
    bad_case_candidate_store = http_request.app.state.bad_case_candidate_store
    candidate = bad_case_candidate_store.get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="bad case candidate not found")
    output_dir = payload.output_dir or str(default_generated_eval_dir)
    results = export_bad_case_candidates(
        bad_case_candidate_store,
        output_dir=output_dir,
        candidate_ids=[candidate_id],
        export_status=None,
        limit=1,
        mark_exported=payload.mark_exported,
    )
    if not results:
        raise HTTPException(status_code=404, detail="bad case candidate not found")
    result = results[0]
    output_path = Path(str(result.get("output_path") or ""))
    export_payload: dict = {}
    if output_path.exists():
        export_payload = json.loads(output_path.read_text(encoding="utf-8"))
    updated_candidate = bad_case_candidate_store.get(candidate_id)
    return BadCaseEvalSkeletonExportResponse(
        candidate_id=str(result.get("candidate_id") or candidate_id),
        target_dataset=str(result.get("target_dataset") or export_payload.get("target_dataset") or ""),
        output_path=str(output_path),
        export_payload=export_payload,
        candidate=BadCaseCandidateResponse(**updated_candidate) if updated_candidate is not None else None,
    )


@app.post("/api/v1/bad-case-candidates/merge-curated-eval-skeletons", response_model=BadCaseCuratedMergeResponse)
async def merge_curated_bad_case_eval_skeletons(payload: BadCaseCuratedMergeRequest, http_request: Request):
    generated_dir = Path(payload.generated_dir or default_generated_eval_dir)
    input_paths = [Path(path) for path in payload.input_paths]
    if not input_paths:
        input_paths = sorted(generated_dir.glob("*.json"))
    if not input_paths:
        return BadCaseCuratedMergeResponse(count=0, results=[])
    bad_case_candidate_store = http_request.app.state.bad_case_candidate_store
    try:
        results = merge_curated_bad_case_files(
            input_paths=input_paths,
            project_root=project_root,
            store=bad_case_candidate_store,
            mark_merged=payload.mark_merged,
            allow_placeholders=payload.allow_placeholders,
            dry_run=payload.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BadCaseCuratedMergeResponse(count=len(results), results=results)


@app.get("/api/v1/bad-case-candidates/{candidate_id}", response_model=BadCaseCandidateResponse)
async def get_bad_case_candidate(candidate_id: str, http_request: Request):
    bad_case_candidate_store = http_request.app.state.bad_case_candidate_store
    candidate = bad_case_candidate_store.get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="bad case candidate not found")
    return BadCaseCandidateResponse(**candidate)


@app.post("/api/v1/bad-case-candidates/{candidate_id}/export-status", response_model=BadCaseCandidateResponse)
async def update_bad_case_export_status(
    candidate_id: str,
    payload: BadCaseCandidateExportStatusRequest,
    http_request: Request,
):
    bad_case_candidate_store = http_request.app.state.bad_case_candidate_store
    candidate = bad_case_candidate_store.update_export_status(
        candidate_id,
        export_status=payload.export_status,
        export_metadata=payload.export_metadata,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="bad case candidate not found")
    return BadCaseCandidateResponse(**candidate)
