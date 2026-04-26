from __future__ import annotations

from typing import Any, Dict, Iterable, List

from ..state.models import KnowledgeHit, RAGContextBundle


DEFAULT_DISABLED_INDEX_INFO = {
    "ready": False,
    "vector_backend": "remote-http",
    "disabled": True,
}


def _coerce_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def normalize_hit(hit: Any) -> KnowledgeHit:
    payload = _coerce_dict(hit)
    if not payload and isinstance(hit, str):
        payload = {"snippet": hit, "title": hit}
    return KnowledgeHit(
        chunk_id=str(payload.get("chunk_id", payload.get("id", ""))),
        parent_id=str(payload.get("parent_id", "")),
        title=str(payload.get("title", payload.get("document", ""))),
        section=str(payload.get("section", payload.get("heading", ""))),
        parent_section=str(payload.get("parent_section", "")),
        path=str(payload.get("path", payload.get("source", ""))),
        category=str(payload.get("category", payload.get("type", ""))),
        score=float(payload.get("score", 0.0) or 0.0),
        snippet=str(payload.get("snippet", payload.get("content", payload.get("text", "")))),
        child_snippet=str(payload.get("child_snippet", "")),
        parent_snippet=str(payload.get("parent_snippet", "")),
        retrieval_granularity=str(payload.get("retrieval_granularity", "chunk")),
        metadata={
            key: value
            for key, value in payload.items()
            if key not in {"chunk_id", "id", "parent_id", "title", "document", "section", "heading", "parent_section", "path", "source", "category", "type", "score", "snippet", "child_snippet", "parent_snippet", "retrieval_granularity", "content", "text"}
        },
    )


def normalize_hits(items: Iterable[Any]) -> List[KnowledgeHit]:
    return [normalize_hit(item) for item in items]


def normalize_rag_search_response(
    response: Dict[str, Any] | None,
    *,
    query: str = "",
) -> RAGContextBundle:
    payload = dict(response or {})
    hits_payload = payload.get("hits")
    context_payload = payload.get("context")
    merged_payload = hits_payload if hits_payload is not None else context_payload
    normalized_hits = normalize_hits(merged_payload or [])
    normalized_context = normalize_hits(context_payload or merged_payload or [])
    if not normalized_context:
        normalized_context = list(normalized_hits)
    if not normalized_hits:
        normalized_hits = list(normalized_context)

    index_info = dict(payload.get("index_info", {}))
    query_type = str(payload.get("query_type") or ("disabled" if index_info.get("disabled") else "search"))
    return RAGContextBundle(
        query=str(payload.get("query") or query),
        query_type=query_type,
        should_respond_directly=bool(payload.get("should_respond_directly", False)),
        direct_answer=payload.get("direct_answer"),
        hits=normalized_hits,
        context=normalized_context,
        citations=list(payload.get("citations", [])),
        facts=list(payload.get("facts", [])),
        index_info=index_info,
        raw_response=payload,
    )


def normalize_rag_search_payload(response: Dict[str, Any] | None, *, query: str = "") -> Dict[str, Any]:
    bundle = normalize_rag_search_response(response, query=query)
    payload = bundle.model_dump()
    payload["hits"] = [item.model_dump() if hasattr(item, "model_dump") else item for item in bundle.hits]
    payload["context"] = [item.model_dump() if hasattr(item, "model_dump") else item for item in bundle.context]
    return payload


def disabled_rag_search_payload(query: str) -> Dict[str, Any]:
    return normalize_rag_search_payload(
        {
            "query": query,
            "query_type": "disabled",
            "should_respond_directly": False,
            "direct_answer": None,
            "hits": [],
            "context": [],
            "citations": [],
            "facts": [],
            "index_info": DEFAULT_DISABLED_INDEX_INFO,
        },
        query=query,
    )
