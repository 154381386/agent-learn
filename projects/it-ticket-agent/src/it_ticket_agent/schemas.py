from typing import Any, Dict, List, Literal, Optional

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


class ConversationCreateRequest(BaseModel):
    user_id: str
    message: str
    ticket_id: Optional[str] = None
    service: Optional[str] = None
    cluster: str = "prod-shanghai-1"
    namespace: str = "default"
    channel: str = "feishu"


class ConversationMessageRequest(BaseModel):
    message: str


class ConversationResumeRequest(BaseModel):
    approved: bool
    approver_id: str
    comment: Optional[str] = None
    approval_id: Optional[str] = None
    interrupt_id: Optional[str] = None


class ConversationTurnResponse(BaseModel):
    turn_id: str
    session_id: str
    role: str
    content: str
    structured_payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ConversationDetailResponse(BaseModel):
    session: "SessionResponse"
    turns: List[ConversationTurnResponse] = Field(default_factory=list)
    pending_interrupt: Optional["InterruptResponse"] = None


class ConversationMutationResponse(BaseModel):
    session: "SessionResponse"
    status: str
    message: str
    diagnosis: Optional[Dict[str, Any]] = None
    approval_request: Optional[ApprovalPayload] = None
    pending_interrupt: Optional["InterruptResponse"] = None
    assistant_turn: Optional[ConversationTurnResponse] = None


class TicketResponse(BaseModel):
    ticket_id: str
    status: str
    message: str
    approval_request: Optional[ApprovalPayload] = None
    diagnosis: Optional[Dict[str, Any]] = None


class SessionResponse(BaseModel):
    session_id: str
    thread_id: str
    ticket_id: str
    user_id: str
    status: str
    current_stage: str
    incident_state: Dict[str, Any]
    latest_approval_id: Optional[str] = None
    pending_interrupt_id: Optional[str] = None
    last_checkpoint_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    last_active_at: str


class InterruptResponse(BaseModel):
    interrupt_id: str
    session_id: str
    ticket_id: str
    type: str
    source: str
    reason: str
    question: str
    expected_input_schema: Dict[str, Any] = Field(default_factory=dict)
    status: str
    resume_token: str
    timeout_at: Optional[str] = None
    answer_payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    resolved_at: Optional[str] = None


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
