from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RAGSearchRequest(BaseModel):
    query: str
    service: Optional[str] = None
    top_k: Optional[int] = None


class RAGHit(BaseModel):
    chunk_id: str
    parent_id: str = ""
    title: str
    section: str
    parent_section: str = ""
    path: str
    category: str
    score: float
    snippet: str
    child_snippet: str = ""
    parent_snippet: str = ""
    retrieval_granularity: str = "chunk"


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


class CaseMemorySyncItem(BaseModel):
    case_id: str
    service: str = ""
    cluster: str = ""
    namespace: str = ""
    failure_mode: str = ""
    root_cause_taxonomy: str = ""
    signal_pattern: str = ""
    action_pattern: str = ""
    symptom: str = ""
    root_cause: str = ""
    key_evidence: List[str] = Field(default_factory=list)
    final_action: str = ""
    final_conclusion: str = ""
    human_verified: bool = False
    content_checksum: str = ""
    source_version: str = ""


class CaseMemorySyncRequest(BaseModel):
    cases: List[CaseMemorySyncItem] = Field(default_factory=list)


class CaseMemoryHit(BaseModel):
    case_id: str
    service: str = ""
    cluster: str = ""
    namespace: str = ""
    failure_mode: str = ""
    root_cause_taxonomy: str = ""
    signal_pattern: str = ""
    action_pattern: str = ""
    symptom: str = ""
    root_cause: str = ""
    final_action: str = ""
    summary: str = ""
    human_verified: bool = False
    recall_source: str = ""
    score: float = 0.0


class CaseMemorySearchRequest(BaseModel):
    query: str
    service: str = ""
    cluster: str = ""
    namespace: str = ""
    failure_mode: str = ""
    root_cause_taxonomy: str = ""
    exclude_case_ids: List[str] = Field(default_factory=list)
    top_k: Optional[int] = None


class CaseMemorySearchResponse(BaseModel):
    query: str
    hits: List[CaseMemoryHit] = Field(default_factory=list)
    index_info: Dict[str, Any] = Field(default_factory=dict)


class CaseMemorySyncResponse(BaseModel):
    status: str
    indexed_cases: int = 0
    skipped_cases: int = 0


class CaseMemoryStatusResponse(BaseModel):
    ready: bool
    schema_name: str
    table: str
    indexed_cases: int
    embedding_enabled: bool
    embedding_model: str
    vector_backend: str
