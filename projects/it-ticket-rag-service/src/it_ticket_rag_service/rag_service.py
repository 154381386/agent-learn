from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from .knowledge import KnowledgeBase
from .schemas import RAGIndexResponse, RAGSearchRequest, RAGSearchResponse
from .settings import get_settings


settings = get_settings()
knowledge = KnowledgeBase(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await knowledge.ensure_ready()
    app.state.knowledge = knowledge
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
