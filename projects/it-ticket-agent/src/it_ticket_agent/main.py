from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from .approval_store import ApprovalStore
from .graph import TicketGraphFactory
from .schemas import (
    ApprovalDecisionRequest,
    RAGIndexResponse,
    RAGSearchRequest,
    RAGSearchResponse,
    TicketRequest,
    TicketResponse,
)
from .settings import get_settings


settings = get_settings()
graph_factory = TicketGraphFactory(settings)
static_dir = Path(__file__).parent / "static"


if not hasattr(aiosqlite.Connection, "is_alive"):
    def _is_alive(self):
        return bool(getattr(self, "_running", False))

    aiosqlite.Connection.is_alive = _is_alive


@asynccontextmanager
async def lifespan(app: FastAPI):
    checkpointer_cm = AsyncSqliteSaver.from_conn_string(settings.langgraph_checkpoint_db)
    checkpointer = await checkpointer_cm.__aenter__()
    app.state.checkpointer_cm = checkpointer_cm
    await graph_factory.knowledge.ensure_ready()
    app.state.ticket_graph = graph_factory.build(checkpointer=checkpointer)
    app.state.approval_store = ApprovalStore(settings.approval_db_path)
    app.state.knowledge = graph_factory.knowledge
    yield
    await checkpointer_cm.__aexit__(None, None, None)


app = FastAPI(title="IT Ticket Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/api/v1/rag/status")
async def rag_status(http_request: Request):
    knowledge = http_request.app.state.knowledge
    return knowledge.status()


@app.post("/api/v1/rag/search", response_model=RAGSearchResponse)
async def rag_search(request: RAGSearchRequest, http_request: Request):
    knowledge = http_request.app.state.knowledge
    result = await knowledge.search(
        query=request.query,
        service=request.service or "",
        top_k=request.top_k,
    )
    return RAGSearchResponse(**result)


@app.post("/api/v1/rag/sync", response_model=RAGIndexResponse)
async def rag_sync(http_request: Request):
    knowledge = http_request.app.state.knowledge
    result = await knowledge.reindex(force=False)
    return RAGIndexResponse(status="ok", **{key: result.get(key, 0) for key in RAGIndexResponse.model_fields if key != "status"})


@app.post("/api/v1/rag/reindex", response_model=RAGIndexResponse)
async def rag_reindex(http_request: Request):
    knowledge = http_request.app.state.knowledge
    result = await knowledge.reindex(force=True)
    return RAGIndexResponse(status="ok", **{key: result.get(key, 0) for key in RAGIndexResponse.model_fields if key != "status"})


@app.post("/api/v1/tickets", response_model=TicketResponse)
async def create_ticket(request: TicketRequest, http_request: Request):
    ticket_graph = http_request.app.state.ticket_graph
    config = {"configurable": {"thread_id": request.ticket_id}}
    initial_state = {
        "ticket_id": request.ticket_id,
        "thread_id": request.ticket_id,
        "user_id": request.user_id,
        "raw_message": request.message,
        "service": request.service or "",
        "cluster": request.cluster,
        "namespace": request.namespace,
    }
    result = await ticket_graph.ainvoke(initial_state, config=config)
    snapshot = await ticket_graph.aget_state(config)

    if snapshot.interrupts:
        payload = snapshot.interrupts[0].value
        return TicketResponse(
            ticket_id=request.ticket_id,
            status="awaiting_approval",
            message="需要人工审批后才能继续执行",
            approval_request=payload,
            diagnosis=result.get("fused_diagnosis"),
        )

    return TicketResponse(
        ticket_id=request.ticket_id,
        status="completed",
        message=result.get("final_response", "处理完成"),
        diagnosis=result.get("fused_diagnosis"),
    )


@app.post("/api/v1/approvals/{approval_id}/decision", response_model=TicketResponse)
async def decide_approval(approval_id: str, request: ApprovalDecisionRequest, http_request: Request):
    approval_store = http_request.app.state.approval_store
    ticket_graph = http_request.app.state.ticket_graph
    approval = approval_store.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")

    approval_store.decide(approval_id, request.approved, request.approver_id, request.comment)
    config = {"configurable": {"thread_id": approval["thread_id"]}}
    result = await ticket_graph.ainvoke(
        Command(
            resume={
                "approved": request.approved,
                "approver_id": request.approver_id,
                "comment": request.comment,
                "approval_id": approval_id,
            }
        ),
        config=config,
    )
    return TicketResponse(
        ticket_id=approval["ticket_id"],
        status="completed",
        message=result.get("final_response", "处理完成"),
        diagnosis=result.get("fused_diagnosis"),
    )
