from contextlib import asynccontextmanager
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
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationMessageRequest,
    ConversationMutationResponse,
    DiagnosisPlaybookResponse,
    DiagnosisPlaybookReviewRequest,
    DiagnosisPlaybookUpsertRequest,
    ConversationResumeRequest,
    ExecutionPlanResponse,
    ExecutionRecoveryResponse,
    IncidentCaseResponse,
    InterruptResponse,
    RuntimeSnapshotResponse,
    SessionResponse,
    SystemEventResponse,
    TicketRequest,
    TicketResponse,
)
from .session import SessionService
from .settings import get_settings
from .storage import StoreProvider


settings = get_settings()
static_dir = Path(__file__).parent / "static"


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


@app.get("/api/v1/cases/{case_id}", response_model=IncidentCaseResponse)
async def get_case(case_id: str, http_request: Request):
    incident_case_store = http_request.app.state.incident_case_store
    case = incident_case_store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return IncidentCaseResponse(**case)
