from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TicketRequest(BaseModel):
    ticket_id: str
    user_id: str
    message: str
    service: Optional[str] = None
    cluster: str = "prod-shanghai-1"
    namespace: str = "default"
    channel: str = "feishu"


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    approver_id: str
    comment: Optional[str] = None


class ApprovalPayload(BaseModel):
    approval_id: str
    ticket_id: str
    thread_id: str
    action: str
    risk: str
    reason: str
    params: Dict[str, Any] = Field(default_factory=dict)


class TicketResponse(BaseModel):
    ticket_id: str
    status: str
    message: str
    approval_request: Optional[ApprovalPayload] = None
    diagnosis: Optional[Dict[str, Any]] = None


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


def model_to_dict(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
