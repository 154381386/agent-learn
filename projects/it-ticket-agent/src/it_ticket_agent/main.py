from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .approval_store import ApprovalStore
from .runtime.orchestrator import SupervisorOrchestrator
from .schemas import (
    ApprovalDecisionRequest,
    TicketRequest,
    TicketResponse,
)
from .settings import get_settings


settings = get_settings()
static_dir = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    approval_store = ApprovalStore(settings.approval_db_path)
    app.state.supervisor_orchestrator = SupervisorOrchestrator(settings, approval_store)
    app.state.approval_store = approval_store
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


@app.post("/api/v1/approvals/{approval_id}/decision", response_model=TicketResponse)
async def decide_approval(approval_id: str, request: ApprovalDecisionRequest, http_request: Request):
    approval_store = http_request.app.state.approval_store
    approval = approval_store.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")

    approval_store.decide(approval_id, request.approved, request.approver_id, request.comment)
    supervisor_orchestrator = http_request.app.state.supervisor_orchestrator
    result = await supervisor_orchestrator.handle_approval_decision(approval, request)
    return TicketResponse(
        ticket_id=approval["ticket_id"],
        status=result["status"],
        message=result["message"],
        diagnosis=result.get("diagnosis"),
    )
