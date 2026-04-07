from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .approval_store import ApprovalStore
from .checkpoint_store import CheckpointStore
from .interrupt_store import InterruptStore
from .memory_store import IncidentCaseStore, ProcessMemoryStore
from .runtime.orchestrator import SupervisorOrchestrator
from .schemas import (
    ApprovalDecisionRequest,
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationMessageRequest,
    ConversationMutationResponse,
    ConversationResumeRequest,
    IncidentCaseResponse,
    InterruptResponse,
    SessionResponse,
    TicketRequest,
    TicketResponse,
)
from .session import SessionService
from .session_store import SessionStore
from .settings import get_settings


settings = get_settings()
static_dir = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    approval_store = ApprovalStore(settings.approval_db_path)
    session_store = SessionStore(settings.approval_db_path)
    session_service = SessionService(session_store)
    interrupt_store = InterruptStore(settings.approval_db_path)
    checkpoint_store = CheckpointStore(settings.approval_db_path)
    process_memory_store = ProcessMemoryStore(settings.approval_db_path)
    incident_case_store = IncidentCaseStore(settings.approval_db_path)
    app.state.supervisor_orchestrator = SupervisorOrchestrator(settings, approval_store, session_store, interrupt_store, checkpoint_store, process_memory_store, session_service=session_service, incident_case_store=incident_case_store)
    app.state.approval_store = approval_store
    app.state.session_store = session_store
    app.state.session_service = session_service
    app.state.interrupt_store = interrupt_store
    app.state.checkpoint_store = checkpoint_store
    app.state.process_memory_store = process_memory_store
    app.state.incident_case_store = incident_case_store
    yield


app = FastAPI(title="IT Ticket Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


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


@app.get("/api/v1/cases", response_model=list[IncidentCaseResponse])
async def list_cases(
    http_request: Request,
    service: str | None = None,
    final_action: str | None = None,
    approval_required: bool | None = None,
    verification_passed: bool | None = None,
    keyword: str | None = None,
    limit: int = 20,
):
    incident_case_store = http_request.app.state.incident_case_store
    cases = incident_case_store.list_cases(
        service=service,
        final_action=final_action,
        approval_required=approval_required,
        verification_passed=verification_passed,
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
