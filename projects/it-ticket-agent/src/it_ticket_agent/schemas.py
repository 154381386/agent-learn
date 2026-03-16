from typing import Any, Dict, List, Optional, TypedDict

from pydantic import BaseModel, Field


class TicketRequest(BaseModel):
    ticket_id: str
    user_id: str
    message: str
    service: Optional[str] = None
    cluster: str = "prod-shanghai-1"
    namespace: str = "default"
    channel: str = "feishu"


class TaskConstraints(BaseModel):
    timeout_sec: int = 8
    allowed_tools: List[str] = Field(default_factory=list)


class TaskPackage(BaseModel):
    ticket_id: str
    service: str
    cluster: str
    namespace: str
    symptom: str
    summary: str
    known_facts: List[str] = Field(default_factory=list)
    knowledge_context: List[Dict[str, Any]] = Field(default_factory=list)
    questions: List[str] = Field(default_factory=list)
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)


class SuggestedAction(BaseModel):
    action: str
    risk: str
    reason: str
    params: Dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    agent: str
    conclusion: str
    confidence: float
    evidence: List[str] = Field(default_factory=list)
    suggested_actions: List[SuggestedAction] = Field(default_factory=list)


class RoutingDecision(BaseModel):
    intent: str
    confidence: float
    complexity_score: float
    recommended_mode: str
    candidate_agents: List[str] = Field(default_factory=list)


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


class TicketState(TypedDict, total=False):
    ticket_id: str
    thread_id: str
    user_id: str
    raw_message: str
    service: str
    cluster: str
    namespace: str
    summary: str
    known_facts: List[str]
    rag_answer: str
    rag_hit: bool
    rag_query_type: str
    rag_context: List[Dict[str, Any]]
    rag_sources: List[Dict[str, Any]]
    routing: Dict[str, Any]
    task_package: Dict[str, Any]
    agent_results: List[Dict[str, Any]]
    fused_diagnosis: Dict[str, Any]
    approval_request: Dict[str, Any]
    approval_decision: Dict[str, Any]
    action_result: Dict[str, Any]
    final_response: str


def model_to_dict(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
