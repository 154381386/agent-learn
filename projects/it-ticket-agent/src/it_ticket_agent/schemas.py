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
    mock_scenario: Optional[str] = None
    mock_scenarios: Dict[str, str] = Field(default_factory=dict)
    mock_tool_responses: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    approver_id: str
    comment: Optional[str] = None


class ApprovalResolutionRequest(BaseModel):
    actor_id: str = "system"
    comment: Optional[str] = None


class ApprovalPayload(BaseModel):
    approval_id: str
    ticket_id: str
    thread_id: str
    status: str = "pending"
    action: str
    risk: str
    reason: str
    params: Dict[str, Any] = Field(default_factory=dict)


class ApprovalEventResponse(BaseModel):
    approval_id: str
    event_type: str
    actor_id: str
    detail: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ExecutionStepResponse(BaseModel):
    step_id: str
    plan_id: str
    session_id: str
    action: str
    tool_name: str
    params: Dict[str, Any] = Field(default_factory=dict)
    sequence: int = 0
    dependencies: List[str] = Field(default_factory=list)
    retry_policy: Dict[str, Any] = Field(default_factory=dict)
    compensation: Optional[Dict[str, Any]] = None
    attempt: int = 0
    last_error: Dict[str, Any] = Field(default_factory=dict)
    status: str
    result_summary: str = ""
    evidence: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: str
    updated_at: str


class ExecutionPlanResponse(BaseModel):
    plan_id: str
    session_id: str
    thread_id: str
    ticket_id: str
    status: str
    steps: List[ExecutionStepResponse] = Field(default_factory=list)
    current_step_id: Optional[str] = None
    summary: str = ""
    recovery: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ExecutionRecoveryResponse(BaseModel):
    session_id: str
    recovery_action: str
    reason: str
    latest_checkpoint: Optional[Dict[str, Any]] = None
    last_success_checkpoint: Optional[Dict[str, Any]] = None
    execution_plan: Optional[ExecutionPlanResponse] = None
    resume_from_step_id: Optional[str] = None
    failed_step_id: Optional[str] = None
    last_completed_step_id: Optional[str] = None
    recovery_hints: List[str] = Field(default_factory=list)


class SystemEventResponse(BaseModel):
    event_id: str
    session_id: str
    thread_id: str
    ticket_id: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ConversationCreateRequest(BaseModel):
    user_id: str
    message: str
    ticket_id: Optional[str] = None
    service: Optional[str] = None
    cluster: str = "prod-shanghai-1"
    namespace: str = "default"
    channel: str = "feishu"
    mock_scenario: Optional[str] = None
    mock_scenarios: Dict[str, str] = Field(default_factory=dict)
    mock_tool_responses: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class ConversationMessageRequest(BaseModel):
    message: str
    mock_scenario: Optional[str] = None
    mock_scenarios: Dict[str, str] = Field(default_factory=dict)
    mock_tool_responses: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class ConversationResumeRequest(BaseModel):
    interrupt_id: Optional[str] = None
    answer_payload: Dict[str, Any] = Field(default_factory=dict)
    approved: Optional[bool] = None
    approver_id: Optional[str] = None
    comment: Optional[str] = None
    approval_id: Optional[str] = None  # compatibility-only; cannot select a different approval target


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
    current_agent: Optional[str] = None
    incident_state: Dict[str, Any]
    latest_approval_id: Optional[str] = None
    pending_interrupt_id: Optional[str] = None
    last_checkpoint_id: Optional[str] = None
    session_memory: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    last_active_at: str
    closed_at: Optional[str] = None


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


class IncidentCaseResponse(BaseModel):
    case_id: str
    session_id: str
    thread_id: str
    ticket_id: str
    service: str = ""
    cluster: str = ""
    namespace: str = ""
    current_agent: str = ""
    symptom: str = ""
    root_cause: str = ""
    key_evidence: List[str] = Field(default_factory=list)
    final_action: str = ""
    approval_required: bool = False
    verification_passed: Optional[bool] = None
    human_verified: bool = False
    hypothesis_accuracy: Dict[str, float] = Field(default_factory=dict)
    actual_root_cause_hypothesis: str = ""
    selected_hypothesis_id: str = ""
    selected_ranker_features: Dict[str, float] = Field(default_factory=dict)
    final_conclusion: str = ""
    created_at: str
    updated_at: str
    closed_at: Optional[str] = None


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
