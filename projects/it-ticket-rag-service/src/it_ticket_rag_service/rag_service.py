from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from .case_memory import CaseMemoryService
from .knowledge import KnowledgeBase
from .schemas import (
    CaseMemorySearchRequest,
    CaseMemorySearchResponse,
    CaseMemoryStatusResponse,
    CaseMemorySyncRequest,
    CaseMemorySyncResponse,
    RAGIndexResponse,
    RAGSearchRequest,
    RAGSearchResponse,
)
from .settings import get_settings


settings = get_settings()
knowledge = KnowledgeBase(settings)
case_memory = CaseMemoryService(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await knowledge.ensure_ready()
    app.state.knowledge = knowledge
    app.state.case_memory = case_memory
    yield


app = FastAPI(title="IT Ticket RAG Service", lifespan=lifespan)


@app.get("/")
async def index():
    return {
        "service": "rag",
        "status": "ok",
        "endpoints": [
            "/healthz",
            "/api/v1/rag/status",
            "/api/v1/rag/search",
            "/api/v1/rag/sync",
            "/api/v1/rag/reindex",
            "/api/v1/case-memory/status",
            "/api/v1/case-memory/search",
            "/api/v1/case-memory/sync",
        ],
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/api/v1/rag/status")
async def rag_status(http_request: Request):
    return http_request.app.state.knowledge.status()


@app.post("/api/v1/rag/search", response_model=RAGSearchResponse)
async def rag_search(request: RAGSearchRequest, http_request: Request):
    result = await http_request.app.state.knowledge.search(
        query=request.query,
        service=request.service or "",
        top_k=request.top_k,
    )
    return RAGSearchResponse(**result)


@app.post("/api/v1/rag/sync", response_model=RAGIndexResponse)
async def rag_sync(http_request: Request):
    result = await http_request.app.state.knowledge.reindex(force=False)
    return RAGIndexResponse(
        status="ok",
        **{key: result.get(key, 0) for key in RAGIndexResponse.model_fields if key != "status"},
    )


@app.post("/api/v1/rag/reindex", response_model=RAGIndexResponse)
async def rag_reindex(http_request: Request):
    result = await http_request.app.state.knowledge.reindex(force=True)
    return RAGIndexResponse(
        status="ok",
        **{key: result.get(key, 0) for key in RAGIndexResponse.model_fields if key != "status"},
    )


@app.get("/api/v1/case-memory/status", response_model=CaseMemoryStatusResponse)
async def case_memory_status(http_request: Request):
    result = await http_request.app.state.case_memory.status()
    return CaseMemoryStatusResponse(**result)


@app.post("/api/v1/case-memory/search", response_model=CaseMemorySearchResponse)
async def case_memory_search(request: CaseMemorySearchRequest, http_request: Request):
    result = await http_request.app.state.case_memory.search(
        query=request.query,
        service=request.service,
        cluster=request.cluster,
        namespace=request.namespace,
        failure_mode=request.failure_mode,
        root_cause_taxonomy=request.root_cause_taxonomy,
        exclude_case_ids=request.exclude_case_ids,
        top_k=request.top_k,
    )
    return CaseMemorySearchResponse(**result)


@app.post("/api/v1/case-memory/sync", response_model=CaseMemorySyncResponse)
async def case_memory_sync(request: CaseMemorySyncRequest, http_request: Request):
    result = await http_request.app.state.case_memory.sync_cases([item.model_dump() for item in request.cases])
    return CaseMemorySyncResponse(**result)
