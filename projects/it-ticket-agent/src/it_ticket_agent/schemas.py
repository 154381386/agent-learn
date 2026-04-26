from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TicketRequest(BaseModel):
    ticket_id: str
    user_id: str
    message: str
    service: Optional[str] = None
    environment: Optional[str] = None
    host_identifier: Optional[str] = None
    db_name: Optional[str] = None
    db_type: Optional[str] = None
    cluster: str = "prod-shanghai-1"
    namespace: str = "default"
    channel: str = "feishu"
    mock_scenario: Optional[str] = None
    mock_scenarios: Dict[str, str] = Field(default_factory=dict)
    mock_tool_responses: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    mock_world_state: Dict[str, Any] = Field(default_factory=dict)


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
    environment: Optional[str] = None
    host_identifier: Optional[str] = None
    db_name: Optional[str] = None
    db_type: Optional[str] = None
    cluster: str = "prod-shanghai-1"
    namespace: str = "default"
    channel: str = "feishu"
    mock_scenario: Optional[str] = None
    mock_scenarios: Dict[str, str] = Field(default_factory=dict)
    mock_tool_responses: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    mock_world_state: Dict[str, Any] = Field(default_factory=dict)


class ConversationMessageRequest(BaseModel):
    message: str
    message_mode: Literal["default", "supplement"] = "default"
    environment: Optional[str] = None
    host_identifier: Optional[str] = None
    db_name: Optional[str] = None
    db_type: Optional[str] = None
    mock_scenario: Optional[str] = None
    mock_scenarios: Dict[str, str] = Field(default_factory=dict)
    mock_tool_responses: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    mock_world_state: Dict[str, Any] = Field(default_factory=dict)


class MockWorldResponse(BaseModel):
    world_id: str
    case_id: str
    service: str
    label: str
    description: str = ""
    tool_count: int = 0
    tool_names: List[str] = Field(default_factory=list)
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




class RuntimeSnapshotResponse(BaseModel):
    session_id: str
    orchestration_mode: str
    react_runtime: Dict[str, Any] = Field(default_factory=dict)
    process_memory_summary: Dict[str, Any] = Field(default_factory=dict)
    pending_interrupt: Optional["InterruptResponse"] = None
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
    case_status: str = "pending_review"
    failure_mode: str = ""
    root_cause_taxonomy: str = ""
    signal_pattern: str = ""
    action_pattern: str = ""
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
    reviewed_by: str = ""
    reviewed_at: Optional[str] = None
    review_note: str = ""
    created_at: str
    updated_at: str
    closed_at: Optional[str] = None


class IncidentCaseReviewRequest(BaseModel):
    human_verified: bool
    hypothesis_accuracy: Dict[str, float] = Field(default_factory=dict)
    actual_root_cause_hypothesis: Optional[str] = None
    reviewed_by: Optional[str] = None
    review_note: Optional[str] = None


class PlaybookExtractionRequest(BaseModel):
    allow_single_case: bool = False
    min_cases: int = 3
    reviewed_by: Optional[str] = None
    review_note: Optional[str] = None


class PlaybookExtractionResponse(BaseModel):
    incident_case: IncidentCaseResponse
    playbook_candidate: Optional["DiagnosisPlaybookResponse"] = None
    extracted: bool = False
    reason: str = ""
    related_case_count: int = 0


class IncidentCaseReviewResponse(BaseModel):
    incident_case: IncidentCaseResponse
    playbook_candidate: Optional["DiagnosisPlaybookResponse"] = None
    playbook_extraction: Dict[str, Any] = Field(default_factory=dict)


class BadCaseCandidateResponse(BaseModel):
    candidate_id: str
    session_id: str
    thread_id: str
    ticket_id: str
    source: str = ""
    reason_codes: List[str] = Field(default_factory=list)
    severity: str = "low"
    request_payload: Dict[str, Any] = Field(default_factory=dict)
    response_payload: Dict[str, Any] = Field(default_factory=dict)
    incident_state_snapshot: Dict[str, Any] = Field(default_factory=dict)
    context_snapshot: Dict[str, Any] = Field(default_factory=dict)
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    retrieval_expansion: Dict[str, Any] = Field(default_factory=dict)
    human_feedback: Dict[str, Any] = Field(default_factory=dict)
    conversation_turns: List[Dict[str, Any]] = Field(default_factory=list)
    system_events: List[Dict[str, Any]] = Field(default_factory=list)
    export_status: str = "pending"
    export_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class BadCaseCandidateExportStatusRequest(BaseModel):
    export_status: str
    export_metadata: Dict[str, Any] = Field(default_factory=dict)


class BadCaseEvalSkeletonExportRequest(BaseModel):
    output_dir: Optional[str] = None
    mark_exported: bool = True


class BadCaseEvalSkeletonExportResponse(BaseModel):
    candidate_id: str
    target_dataset: str
    output_path: str
    export_payload: Dict[str, Any] = Field(default_factory=dict)
    candidate: Optional[BadCaseCandidateResponse] = None


class BadCaseCuratedMergeRequest(BaseModel):
    input_paths: List[str] = Field(default_factory=list)
    generated_dir: Optional[str] = None
    mark_merged: bool = True
    allow_placeholders: bool = False
    dry_run: bool = False


class BadCaseCuratedMergeResponse(BaseModel):
    count: int
    results: List[Dict[str, Any]] = Field(default_factory=list)


class DiagnosisPlaybookResponse(BaseModel):
    playbook_id: str
    version: int = 1
    title: str = ""
    status: str = "pending_review"
    human_verified: bool = False
    service_type: str = ""
    failure_modes: List[str] = Field(default_factory=list)
    environments: List[str] = Field(default_factory=list)
    trigger_conditions: List[str] = Field(default_factory=list)
    signal_patterns: List[str] = Field(default_factory=list)
    negative_conditions: List[str] = Field(default_factory=list)
    required_entities: List[str] = Field(default_factory=list)
    diagnostic_goal: str = ""
    diagnostic_steps: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_requirements: List[str] = Field(default_factory=list)
    guardrails: List[str] = Field(default_factory=list)
    common_false_positives: List[str] = Field(default_factory=list)
    source_case_ids: List[str] = Field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    last_eval_passed: Optional[bool] = None
    reviewed_by: str = ""
    reviewed_at: Optional[str] = None
    review_note: str = ""
    created_at: str
    updated_at: str
    retired_at: Optional[str] = None


class DiagnosisPlaybookUpsertRequest(BaseModel):
    playbook_id: Optional[str] = None
    version: int = 1
    title: str = ""
    status: str = "pending_review"
    human_verified: bool = False
    service_type: str = ""
    failure_modes: List[str] = Field(default_factory=list)
    environments: List[str] = Field(default_factory=list)
    trigger_conditions: List[str] = Field(default_factory=list)
    signal_patterns: List[str] = Field(default_factory=list)
    negative_conditions: List[str] = Field(default_factory=list)
    required_entities: List[str] = Field(default_factory=list)
    diagnostic_goal: str = ""
    diagnostic_steps: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_requirements: List[str] = Field(default_factory=list)
    guardrails: List[str] = Field(default_factory=list)
    common_false_positives: List[str] = Field(default_factory=list)
    source_case_ids: List[str] = Field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    last_eval_passed: Optional[bool] = None
    reviewed_by: str = ""
    reviewed_at: Optional[str] = None
    review_note: str = ""
    retired_at: Optional[str] = None


class DiagnosisPlaybookReviewRequest(BaseModel):
    human_verified: bool
    status: Optional[str] = None
    reviewed_by: Optional[str] = None
    review_note: Optional[str] = None


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
