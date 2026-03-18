from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RAGSearchRequest(BaseModel):
    query: str
    service: Optional[str] = None
    top_k: Optional[int] = None


class RAGHit(BaseModel):
    chunk_id: str
    title: str
    section: str
    path: str
    category: str
    score: float
    snippet: str


class RAGSearchResponse(BaseModel):
    query: str
    query_type: str
    should_respond_directly: bool
    direct_answer: Optional[str] = None
    hits: List[RAGHit] = Field(default_factory=list)
    index_info: Dict[str, Any] = Field(default_factory=dict)


class RAGIndexResponse(BaseModel):
    status: str
    documents: int
    chunks: int
    embedding_enabled: bool
    index_path: str
    new_documents: int = 0
    updated_documents: int = 0
    removed_documents: int = 0
    skipped_documents: int = 0
