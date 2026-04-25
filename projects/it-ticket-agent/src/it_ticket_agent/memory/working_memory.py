from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..session.models import utc_now

WORKING_MEMORY_UNSET = object()

_COMPACTION_VERSION = 1
_MAX_NARRATIVE_SUMMARY_CHARS = 1800
_MAX_CONFIRMED_FACTS = 24
_MAX_OPEN_QUESTIONS = 8
_MAX_KEY_EVIDENCE = 12
_MAX_ACTIONS = 12
_MAX_USER_CORRECTIONS = 12
_MAX_CONSTRAINTS = 12
_MAX_HYPOTHESES = 12
_MAX_RULED_OUT_HYPOTHESES = 12
_MAX_SOURCE_REFS = 32

_LIST_KEYS = (
    "confirmed_facts",
    "constraints",
    "open_questions",
    "hypotheses",
    "ruled_out_hypotheses",
    "key_evidence",
    "actions_taken",
    "user_corrections",
    "source_refs",
)
_COMPACTED_LIST_LIMITS = {
    "confirmed_facts": 16,
    "constraints": 8,
    "open_questions": 6,
    "hypotheses": 6,
    "ruled_out_hypotheses": 8,
    "key_evidence": 8,
    "actions_taken": 8,
    "user_corrections": 8,
    "source_refs": 24,
}
_PROTECTED_SOURCE_TYPES = {"user_confirmed", "user_correction", "tool_observed"}
_LLM_COMPACTION_SCHEMA_KEYS = (
    "narrative_summary",
    "confirmed_facts",
    "constraints",
    "open_questions",
    "hypotheses",
    "ruled_out_hypotheses",
    "key_evidence",
    "actions_taken",
    "user_corrections",
    "decision_state",
    "source_refs",
)
_ENTITY_LABELS = {
    "service": "服务",
    "environment": "环境",
    "host_identifier": "主机/实例",
    "db_name": "数据库",
    "db_type": "数据库类型",
    "cluster": "集群",
    "namespace": "命名空间",
}
_SOURCE_TYPE_PRIORITY = {
    "user_confirmed": 120,
    "user_correction": 115,
    "tool_observed": 105,
    "ranker_selected": 95,
    "approval_state": 90,
    "user_reported": 85,
    "system_state": 80,
    "runtime_summary": 75,
    "runtime_derived": 70,
    "llm_inferred": 60,
    "historical_case_hint": 30,
}
_SOURCE_REF_KEYS = {
    "turn_id",
    "event_id",
    "interrupt_id",
    "observation_id",
    "checkpoint_id",
    "hypothesis_id",
    "approval_id",
    "proposal_id",
    "case_id",
}
_DEFAULT_IDENTITY_KEYS = {
    "confirmed_facts": ("key",),
    "constraints": ("constraint", "value"),
    "open_questions": ("question",),
    "hypotheses": ("hypothesis_id", "root_cause"),
    "ruled_out_hypotheses": ("hypothesis_id", "root_cause"),
    "key_evidence": ("evidence", "source"),
    "actions_taken": ("action", "status", "source"),
    "user_corrections": ("message", "event_type"),
}


def empty_working_memory() -> dict[str, Any]:
    return {
        "task_focus": {},
        "narrative_summary": "",
        "confirmed_facts": [],
        "constraints": [],
        "open_questions": [],
        "hypotheses": [],
        "ruled_out_hypotheses": [],
        "key_evidence": [],
        "actions_taken": [],
        "user_corrections": [],
        "decision_state": {},
        "source_refs": [],
        "compaction": {},
        "last_updated_at": utc_now(),
    }


def build_initial_working_memory(
    *,
    original_user_message: str | None = None,
    current_user_message: str | None = None,
    key_entities: dict[str, Any] | None = None,
    current_stage: str | None = None,
    current_intent: dict[str, Any] | None = None,
    pending_interrupt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return merge_working_memory(
        None,
        reset=True,
        original_user_message=original_user_message,
        current_user_message=current_user_message,
        key_entities=key_entities,
        current_stage=current_stage,
        current_intent=current_intent,
        pending_interrupt=pending_interrupt,
    )


def merge_working_memory(
    existing: dict[str, Any] | None,
    *,
    reset: bool = False,
    original_user_message: str | None = None,
    current_user_message: str | None = None,
    current_intent: dict[str, Any] | None = None,
    key_entities: dict[str, Any] | None = None,
    clarification_answers: dict[str, Any] | None = None,
    current_stage: str | None = None,
    pending_approval: dict[str, Any] | None | object = WORKING_MEMORY_UNSET,
    pending_interrupt: dict[str, Any] | None | object = WORKING_MEMORY_UNSET,
    session_event_queue: list[dict[str, Any]] | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    memory = empty_working_memory() if reset else normalize_working_memory(existing)
    now = utc_now()

    task_focus = dict(memory.get("task_focus") or {})
    if original_user_message is not None:
        task_focus["original_user_message"] = str(original_user_message or "")
        task_focus["current_user_message"] = str(original_user_message or "")
        _merge_narrative_summary(memory, f"当前问题：{original_user_message}")
    if current_user_message is not None:
        task_focus["current_user_message"] = str(current_user_message or "")
    if current_stage is not None:
        task_focus["current_stage"] = str(current_stage or "")
    if current_intent:
        task_focus["current_intent"] = _compact_mapping(current_intent)
        for key in ("intent", "current_node", "route_source"):
            if current_intent.get(key) is not None:
                task_focus[key] = current_intent.get(key)
    if key_entities:
        task_focus["entities"] = _merge_mapping(dict(task_focus.get("entities") or {}), key_entities)
    memory["task_focus"] = task_focus

    if key_entities:
        for key, value in key_entities.items():
            if _has_value(value):
                _upsert_fact(
                    memory,
                    key=f"entity.{key}",
                    label=_ENTITY_LABELS.get(str(key), str(key)),
                    value=value,
                    source="session_state",
                    source_type="system_state",
                    confidence=1.0,
                    updated_at=now,
                )

    if clarification_answers:
        for interrupt_id, answer in clarification_answers.items():
            if not isinstance(answer, dict):
                continue
            normalized = answer.get("normalized_answers")
            if not isinstance(normalized, dict):
                continue
            for key, value in normalized.items():
                if _has_value(value):
                    _upsert_fact(
                        memory,
                        key=f"clarification.{key}",
                        label=_ENTITY_LABELS.get(str(key), str(key)),
                        value=value,
                        source="clarification",
                        source_type="user_confirmed",
                        confidence=1.0,
                        updated_at=now,
                        refs={"interrupt_id": str(interrupt_id)},
                    )
            answered = ", ".join(f"{key}={value}" for key, value in normalized.items() if _has_value(value))
            if answered:
                _merge_narrative_summary(memory, f"用户已澄清：{answered}")

    decision_state = dict(memory.get("decision_state") or {})
    if current_stage is not None:
        decision_state["current_stage"] = str(current_stage or "")
    if pending_approval is not WORKING_MEMORY_UNSET:
        if pending_approval:
            decision_state["pending_approval"] = _compact_mapping(pending_approval)
            _append_unique_item(
                memory,
                "actions_taken",
                {
                    "action": str(pending_approval.get("action") or ""),
                    "status": "awaiting_approval",
                    "risk": pending_approval.get("risk"),
                    "reason": pending_approval.get("reason"),
                    "source": "approval_gate",
                    "source_type": "approval_state",
                    "confidence": 1.0,
                    "refs": {"approval_id": pending_approval.get("approval_id")},
                    "created_at": now,
                },
                identity_keys=("action", "status"),
                limit=_MAX_ACTIONS,
            )
        else:
            decision_state.pop("pending_approval", None)
    if pending_interrupt is not WORKING_MEMORY_UNSET:
        if pending_interrupt:
            compact_interrupt = _compact_mapping(pending_interrupt)
            decision_state["pending_interrupt"] = compact_interrupt
            if str(pending_interrupt.get("type") or "") == "clarification":
                question = str(pending_interrupt.get("question") or "").strip()
                if question:
                    _append_unique_item(
                        memory,
                        "open_questions",
                        {
                            "question": question,
                            "reason": pending_interrupt.get("reason"),
                            "source": "clarification",
                            "source_type": "user_confirmed",
                            "confidence": 1.0,
                            "refs": {"interrupt_id": pending_interrupt.get("interrupt_id")},
                            "created_at": now,
                        },
                        identity_keys=("question",),
                        limit=_MAX_OPEN_QUESTIONS,
                    )
        else:
            decision_state.pop("pending_interrupt", None)
            if current_stage not in {"awaiting_clarification", "awaiting_approval"}:
                memory["open_questions"] = []
    memory["decision_state"] = decision_state

    if session_event_queue:
        _merge_session_events(memory, session_event_queue, now=now)

    if updates:
        _apply_explicit_updates(memory, updates, now=now)

    _trim_memory(memory)
    trigger = working_memory_compaction_trigger(memory)
    if trigger:
        memory = compact_working_memory(memory, trigger=trigger, source="deterministic_priority")
    memory["last_updated_at"] = now
    return memory


def normalize_working_memory(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return empty_working_memory()
    memory = deepcopy(value)
    if not isinstance(memory.get("task_focus"), dict):
        memory["task_focus"] = {}
    if not isinstance(memory.get("decision_state"), dict):
        memory["decision_state"] = {}
    if not isinstance(memory.get("narrative_summary"), str):
        memory["narrative_summary"] = str(memory.get("narrative_summary") or "")
    if not isinstance(memory.get("compaction"), dict):
        memory["compaction"] = {}
    for key in _LIST_KEYS:
        if not isinstance(memory.get(key), list):
            memory[key] = []
    if not memory.get("last_updated_at"):
        memory["last_updated_at"] = utc_now()
    _trim_memory(memory)
    return memory


@dataclass(frozen=True)
class WorkingMemoryCompactionPolicy:
    max_approx_tokens: int = 2400
    max_narrative_summary_chars: int = 1400
    max_total_items: int = 44
    min_changed_items_since_compaction: int = 2


def working_memory_compaction_trigger(
    memory: dict[str, Any] | None,
    *,
    policy: WorkingMemoryCompactionPolicy | None = None,
) -> str | None:
    normalized = normalize_working_memory(memory)
    policy = policy or WorkingMemoryCompactionPolicy()
    signature = _compaction_input_signature(normalized)
    compaction = dict(normalized.get("compaction") or {})
    if compaction.get("input_signature") == signature:
        return None
    previous_count = _coerce_int(compaction.get("preserved_item_count"))
    current_count = _working_memory_item_count(normalized)
    if previous_count is not None and current_count < previous_count + policy.min_changed_items_since_compaction:
        return None
    approx_tokens = estimate_working_memory_tokens(normalized)
    if approx_tokens > policy.max_approx_tokens:
        return "approx_token_budget_exceeded"
    if len(str(normalized.get("narrative_summary") or "")) > policy.max_narrative_summary_chars:
        return "narrative_summary_budget_exceeded"
    if current_count > policy.max_total_items:
        return "item_count_budget_exceeded"
    return None


def estimate_working_memory_tokens(memory: dict[str, Any] | None) -> int:
    normalized = normalize_working_memory(memory)
    payload = {key: value for key, value in normalized.items() if key != "compaction"}
    return max(1, len(json.dumps(payload, ensure_ascii=False, default=str)) // 4)


def compact_working_memory(
    memory: dict[str, Any] | None,
    *,
    trigger: str = "manual",
    source: str = "deterministic_priority",
) -> dict[str, Any]:
    source_memory = normalize_working_memory(memory)
    compacted = empty_working_memory()
    compacted["task_focus"] = dict(source_memory.get("task_focus") or {})
    compacted["decision_state"] = dict(source_memory.get("decision_state") or {})
    compacted["narrative_summary"] = _build_compacted_narrative_summary(source_memory)
    for key, limit in _COMPACTED_LIST_LIMITS.items():
        if key == "source_refs":
            continue
        compacted[key] = _select_compacted_items(key, list(source_memory.get(key) or []), limit)
    compacted["source_refs"] = _select_compacted_source_refs(source_memory, compacted)
    compacted["last_updated_at"] = utc_now()
    _attach_compaction_metadata(compacted, source_memory, trigger=trigger, source=source, llm_used=False)
    return compacted


async def compact_working_memory_with_llm(
    memory: dict[str, Any] | None,
    llm: Any,
    *,
    trigger: str | None = None,
    policy: WorkingMemoryCompactionPolicy | None = None,
) -> dict[str, Any]:
    source_memory = normalize_working_memory(memory)
    trigger = trigger or working_memory_compaction_trigger(source_memory, policy=policy)
    if not trigger:
        return source_memory
    fallback = compact_working_memory(source_memory, trigger=trigger, source="deterministic_fallback")
    if not getattr(llm, "enabled", False):
        return fallback
    try:
        response = await llm.chat(_build_llm_compaction_messages(source_memory, trigger=trigger), tools=None)
        content = str(response.get("content") or "") if isinstance(response, dict) else str(response or "")
        parsed = _extract_json_object(content)
        return _merge_llm_compaction_output(source_memory, fallback, parsed, trigger=trigger)
    except Exception as exc:
        fallback["compaction"]["llm_used"] = False
        fallback["compaction"]["llm_error_type"] = exc.__class__.__name__
        return fallback


def _build_llm_compaction_messages(memory: dict[str, Any], *, trigger: str) -> list[dict[str, str]]:
    payload = {key: value for key, value in memory.items() if key != "compaction"}
    return [
        {
            "role": "system",
            "content": (
                "你是 IT 诊断 Agent 的工作记忆压缩器。只输出 JSON，不要输出 Markdown。"
                "目标是压缩当前会话工作记忆，保留用户确认事实、用户纠错、工具观测、已排除假设、待澄清问题和 source_refs。"
                "不要编造新事实；source_refs 只能来自输入。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "trigger": trigger,
                    "schema": {key: "keep same semantic shape" for key in _LLM_COMPACTION_SCHEMA_KEYS},
                    "working_memory": payload,
                },
                ensure_ascii=False,
                default=str,
            ),
        },
    ]


def _merge_llm_compaction_output(
    source_memory: dict[str, Any],
    fallback: dict[str, Any],
    parsed: dict[str, Any],
    *,
    trigger: str,
) -> dict[str, Any]:
    compacted = normalize_working_memory(fallback)
    if _has_value(parsed.get("narrative_summary")):
        compacted["narrative_summary"] = _truncate_summary(str(parsed.get("narrative_summary") or ""), 1200)
    for key, limit in _COMPACTED_LIST_LIMITS.items():
        if key == "source_refs":
            continue
        incoming = _coerce_compaction_items(parsed.get(key))
        if incoming:
            merged = _merge_compaction_lists(key, list(compacted.get(key) or []), incoming)
            compacted[key] = _select_compacted_items(key, merged, limit)
    if isinstance(parsed.get("decision_state"), dict):
        decision_state = dict(compacted.get("decision_state") or {})
        decision_state.update(_compact_mapping(parsed["decision_state"]))
        compacted["decision_state"] = decision_state
    compacted["source_refs"] = _merge_valid_llm_source_refs(
        source_memory,
        list(compacted.get("source_refs") or []),
        _coerce_compaction_items(parsed.get("source_refs")),
    )
    _attach_compaction_metadata(compacted, source_memory, trigger=trigger, source="llm_structured", llm_used=True)
    return compacted


def _build_compacted_narrative_summary(memory: dict[str, Any], *, limit: int = 1200) -> str:
    fragments: list[str] = []
    task_focus = dict(memory.get("task_focus") or {})
    original = str(task_focus.get("original_user_message") or "").strip()
    current = str(task_focus.get("current_user_message") or "").strip()
    if original:
        fragments.append(f"当前问题：{original}")
    if current and current != original:
        fragments.append(f"最新输入：{current}")
    facts = _format_fact_fragments(list(memory.get("confirmed_facts") or [])[:8])
    if facts:
        fragments.append("已确认事实：" + "，".join(facts))
    corrections = [str(item.get("message") or "").strip() for item in _select_compacted_items("user_corrections", list(memory.get("user_corrections") or []), 4) if isinstance(item, dict)]
    if corrections:
        fragments.append("用户纠错：" + "；".join(item for item in corrections if item))
    evidence = [str(item.get("evidence") or item.get("summary") or "").strip() for item in _select_compacted_items("key_evidence", list(memory.get("key_evidence") or []), 5) if isinstance(item, dict)]
    if evidence:
        fragments.append("关键证据：" + "；".join(item for item in evidence if item))
    ruled_out = [str(item.get("root_cause") or item.get("reason") or "").strip() for item in _select_compacted_items("ruled_out_hypotheses", list(memory.get("ruled_out_hypotheses") or []), 4) if isinstance(item, dict)]
    if ruled_out:
        fragments.append("已排除：" + "；".join(item for item in ruled_out if item))
    open_questions = [str(item.get("question") or "").strip() for item in _select_compacted_items("open_questions", list(memory.get("open_questions") or []), 3) if isinstance(item, dict)]
    if open_questions:
        fragments.append("待确认：" + "；".join(item for item in open_questions if item))
    existing = str(memory.get("narrative_summary") or "").strip()
    if existing:
        fragments.append("既有摘要：" + existing)
    return _truncate_summary("；".join(fragment for fragment in fragments if fragment), limit)


def _select_compacted_items(key: str, items: list[Any], limit: int) -> list[Any]:
    normalized_items = [dict(item) for item in items if isinstance(item, dict)]
    protected = [item for item in normalized_items if _is_protected_memory_item(item)]
    selected = _select_memory_items(normalized_items, limit)
    return _select_memory_items(_merge_compaction_lists(key, protected, selected), limit)


def _merge_compaction_lists(key: str, existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for item in [*existing, *incoming]:
        if not isinstance(item, dict):
            continue
        payload = _normalize_item_payload(key, item) if key != "source_refs" else dict(item)
        identity_keys = _identity_keys_for_item(key, payload) if key != "source_refs" else ("ref_type", "ref_id", "field")
        identity = tuple(str(payload.get(identity_key) or "") for identity_key in identity_keys)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(payload)
    return merged


def _select_compacted_source_refs(source_memory: dict[str, Any], compacted: dict[str, Any]) -> list[dict[str, Any]]:
    referenced = _collect_refs_from_memory_items(compacted)
    refs = [dict(item) for item in list(source_memory.get("source_refs") or []) if isinstance(item, dict)]
    selected = [item for item in refs if (str(item.get("ref_type") or ""), str(item.get("ref_id") or "")) in referenced]
    if len(selected) < _COMPACTED_LIST_LIMITS["source_refs"]:
        for item in refs:
            identity = (str(item.get("ref_type") or ""), str(item.get("ref_id") or ""), str(item.get("field") or ""))
            if any((str(existing.get("ref_type") or ""), str(existing.get("ref_id") or ""), str(existing.get("field") or "")) == identity for existing in selected):
                continue
            if str(item.get("source_type") or "") in _PROTECTED_SOURCE_TYPES:
                selected.append(item)
            if len(selected) >= _COMPACTED_LIST_LIMITS["source_refs"]:
                break
    return selected[-_COMPACTED_LIST_LIMITS["source_refs"] :]


def _merge_valid_llm_source_refs(
    source_memory: dict[str, Any],
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed = {
        (str(item.get("ref_type") or ""), str(item.get("ref_id") or ""), str(item.get("field") or ""))
        for item in list(source_memory.get("source_refs") or [])
        if isinstance(item, dict)
    }
    valid_incoming = [
        dict(item)
        for item in incoming
        if (str(item.get("ref_type") or ""), str(item.get("ref_id") or ""), str(item.get("field") or "")) in allowed
    ]
    return _merge_compaction_lists("source_refs", existing, valid_incoming)[-_COMPACTED_LIST_LIMITS["source_refs"] :]


def _collect_refs_from_memory_items(memory: dict[str, Any]) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    for key in _LIST_KEYS:
        if key == "source_refs":
            continue
        for item in list(memory.get(key) or []):
            if not isinstance(item, dict):
                continue
            item_refs = item.get("refs")
            if not isinstance(item_refs, dict):
                continue
            for ref_type, ref_value in item_refs.items():
                if str(ref_type) not in _SOURCE_REF_KEYS:
                    continue
                for value in _iter_ref_values(ref_value):
                    refs.add((str(ref_type), str(value)))
    return refs


def _attach_compaction_metadata(
    compacted: dict[str, Any],
    source_memory: dict[str, Any],
    *,
    trigger: str,
    source: str,
    llm_used: bool,
) -> None:
    input_count = _working_memory_item_count(source_memory)
    preserved_count = _working_memory_item_count(compacted)
    input_tokens = estimate_working_memory_tokens(source_memory)
    output_tokens = max(1, len(json.dumps({key: value for key, value in compacted.items() if key != "compaction"}, ensure_ascii=False, default=str)) // 4)
    previous = dict(source_memory.get("compaction") or {})
    history = [dict(item) for item in list(previous.get("history") or []) if isinstance(item, dict)][-4:]
    previous_without_history = {key: value for key, value in previous.items() if key != "history"}
    if previous_without_history:
        history.append(previous_without_history)
    compacted["compaction"] = {
        "version": _COMPACTION_VERSION,
        "strategy": "structured_working_memory",
        "trigger": trigger,
        "source": source,
        "llm_used": llm_used,
        "compacted_at": utc_now(),
        "input_item_count": input_count,
        "preserved_item_count": preserved_count,
        "dropped_item_count": max(0, input_count - preserved_count),
        "input_approx_tokens": input_tokens,
        "output_approx_tokens": output_tokens,
        "input_signature": _compaction_input_signature(source_memory),
        "protected_source_types": sorted(_PROTECTED_SOURCE_TYPES),
        "preserved_fields": list(_LLM_COMPACTION_SCHEMA_KEYS),
        "history": history[-5:],
    }


def _compaction_input_signature(memory: dict[str, Any]) -> str:
    payload = {
        "task_focus": dict(memory.get("task_focus") or {}),
        "narrative_summary": str(memory.get("narrative_summary") or ""),
        "decision_state": dict(memory.get("decision_state") or {}),
        "lists": {key: list(memory.get(key) or []) for key in _LIST_KEYS},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _working_memory_item_count(memory: dict[str, Any]) -> int:
    return sum(len(list(memory.get(key) or [])) for key in _LIST_KEYS)


def _is_protected_memory_item(item: dict[str, Any]) -> bool:
    return str(item.get("source_type") or "") in _PROTECTED_SOURCE_TYPES or bool(item.get("user_confirmed"))


def _format_fact_fragments(facts: list[Any]) -> list[str]:
    fragments: list[str] = []
    for item in facts:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("key") or "").strip()
        value = item.get("value")
        if label and _has_value(value):
            fragments.append(f"{label}={value}")
    return fragments


def _truncate_summary(value: str, limit: int) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= limit:
        return normalized
    return "…" + normalized[-(limit - 1) :]


def _coerce_compaction_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _extract_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("working memory compaction output must be a JSON object")
    return parsed


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        if isinstance(value, str) and value.strip():
            return int(value)
    except ValueError:
        return None
    return None


def _merge_session_events(memory: dict[str, Any], queue: list[dict[str, Any]], *, now: str) -> None:
    for item in queue[-20:]:
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "")
        message = str(item.get("message") or "").strip()
        metadata = dict(item.get("metadata") or {})
        if event_type in {"correction", "new_issue"} and message:
            _append_unique_item(
                memory,
                "user_corrections",
                {
                    "message": message,
                    "event_type": event_type,
                    "source": item.get("source") or "user_message",
                    "source_type": "user_correction",
                    "confidence": 1.0,
                    "reason_tags": list(metadata.get("reason_tags") or []),
                    "topic_shift_detected": bool(metadata.get("topic_shift_detected")),
                    "refs": {"event_id": item.get("event_id")},
                    "created_at": item.get("created_at") or now,
                    "consumed_at": item.get("consumed_at"),
                },
                identity_keys=("message", "event_type"),
                limit=_MAX_USER_CORRECTIONS,
            )
            prefix = "新问题" if event_type == "new_issue" else "用户纠错"
            _merge_narrative_summary(memory, f"{prefix}：{message}")
        elif event_type == "supplement" and message:
            _append_unique_item(
                memory,
                "key_evidence",
                {
                    "evidence": message,
                    "source": item.get("source") or "user_message",
                    "source_type": "user_reported",
                    "confidence": 0.85,
                    "refs": {"event_id": item.get("event_id")},
                    "created_at": item.get("created_at") or now,
                },
                identity_keys=("evidence",),
                limit=_MAX_KEY_EVIDENCE,
            )
            _merge_narrative_summary(memory, f"用户补充：{message}")


def _apply_explicit_updates(memory: dict[str, Any], updates: dict[str, Any], *, now: str) -> None:
    if _has_value(updates.get("narrative_summary")):
        _merge_narrative_summary(memory, str(updates.get("narrative_summary") or ""))
    for fragment in list(updates.get("summary_fragments") or []):
        if _has_value(fragment):
            _merge_narrative_summary(memory, str(fragment))
    for key in (
        "constraints",
        "open_questions",
        "hypotheses",
        "ruled_out_hypotheses",
        "key_evidence",
        "actions_taken",
        "user_corrections",
    ):
        for item in list(updates.get(key) or []):
            if isinstance(item, dict):
                payload = dict(item)
            else:
                value_key = "evidence" if key == "key_evidence" else "value"
                payload = {value_key: str(item)}
            payload.setdefault("source", "runtime")
            payload.setdefault("source_type", _default_source_type(key, str(payload.get("source") or "")))
            payload.setdefault("created_at", now)
            _append_unique_item(memory, key, payload, limit=_limit_for_key(key))
    for item in list(updates.get("confirmed_facts") or []):
        if isinstance(item, dict):
            fact_key = str(item.get("key") or item.get("label") or item.get("value") or "")
            if fact_key:
                _upsert_fact(
                    memory,
                    key=fact_key,
                    label=str(item.get("label") or fact_key),
                    value=item.get("value"),
                    source=str(item.get("source") or "runtime"),
                    source_type=str(
                        item.get("source_type")
                        or _default_source_type("confirmed_facts", str(item.get("source") or "runtime"))
                    ),
                    confidence=_coerce_confidence(item.get("confidence")),
                    updated_at=now,
                    refs=dict(item.get("refs") or {}),
                )
    for item in list(updates.get("source_refs") or []):
        if isinstance(item, dict):
            _append_source_ref(memory, item, now=now)
    if isinstance(updates.get("decision_state"), dict):
        decision_state = dict(memory.get("decision_state") or {})
        decision_state.update(dict(updates["decision_state"]))
        memory["decision_state"] = decision_state


def _upsert_fact(
    memory: dict[str, Any],
    *,
    key: str,
    label: str,
    value: Any,
    source: str,
    source_type: str,
    updated_at: str,
    confidence: float | None = None,
    refs: dict[str, Any] | None = None,
) -> None:
    facts = list(memory.get("confirmed_facts") or [])
    payload = {
        "key": key,
        "label": label,
        "value": value,
        "source": source,
        "source_type": source_type,
        "updated_at": updated_at,
    }
    confidence = _coerce_confidence(confidence)
    if confidence is not None:
        payload["confidence"] = confidence
    if refs:
        payload["refs"] = dict(refs)
    for index, existing in enumerate(facts):
        if isinstance(existing, dict) and existing.get("key") == key:
            facts[index] = payload
            break
    else:
        facts.append(payload)
    memory["confirmed_facts"] = _select_memory_items(facts, _MAX_CONFIRMED_FACTS)
    _append_source_refs_from_payload(memory, payload, field="confirmed_facts", now=updated_at)


def _append_unique_item(
    memory: dict[str, Any],
    key: str,
    item: dict[str, Any],
    *,
    identity_keys: tuple[str, ...] | None = None,
    limit: int,
) -> None:
    payload = _normalize_item_payload(key, item)
    items = [dict(existing) for existing in list(memory.get(key) or []) if isinstance(existing, dict)]
    identity_keys = identity_keys or _identity_keys_for_item(key, payload)
    item_identity = tuple(str(payload.get(identity_key) or "") for identity_key in identity_keys)
    if any(tuple(str(existing.get(identity_key) or "") for identity_key in identity_keys) == item_identity for existing in items):
        return
    items.append(payload)
    memory[key] = _select_memory_items(items, limit)
    _append_source_refs_from_payload(memory, payload, field=key, now=str(payload.get("created_at") or utc_now()))


def _merge_narrative_summary(memory: dict[str, Any], fragment: str) -> None:
    normalized = " ".join(str(fragment or "").split())
    if not normalized:
        return
    existing = str(memory.get("narrative_summary") or "").strip()
    if normalized in existing:
        return
    merged = f"{existing}；{normalized}" if existing else normalized
    if len(merged) > _MAX_NARRATIVE_SUMMARY_CHARS:
        merged = "…" + merged[-(_MAX_NARRATIVE_SUMMARY_CHARS - 1) :]
    memory["narrative_summary"] = merged


def _append_source_refs_from_payload(memory: dict[str, Any], payload: dict[str, Any], *, field: str, now: str) -> None:
    refs = payload.get("refs")
    if not isinstance(refs, dict):
        return
    for ref_type, ref_value in refs.items():
        if str(ref_type) not in _SOURCE_REF_KEYS:
            continue
        for value in _iter_ref_values(ref_value):
            _append_source_ref(
                memory,
                {
                    "ref_type": str(ref_type),
                    "ref_id": str(value),
                    "field": field,
                    "source": payload.get("source"),
                    "source_type": payload.get("source_type"),
                    "created_at": now,
                },
                now=now,
            )


def _append_source_ref(memory: dict[str, Any], item: dict[str, Any], *, now: str) -> None:
    ref_type = str(item.get("ref_type") or "").strip()
    ref_id = str(item.get("ref_id") or "").strip()
    if not ref_type or not ref_id or ref_id == "None":
        return
    payload = {
        "ref_type": ref_type,
        "ref_id": ref_id,
        "field": str(item.get("field") or ""),
        "source": str(item.get("source") or ""),
        "source_type": str(item.get("source_type") or ""),
        "created_at": str(item.get("created_at") or now),
    }
    refs = [dict(existing) for existing in list(memory.get("source_refs") or []) if isinstance(existing, dict)]
    identity = (payload["ref_type"], payload["ref_id"], payload["field"])
    if any((ref.get("ref_type"), ref.get("ref_id"), ref.get("field")) == identity for ref in refs):
        return
    refs.append(payload)
    memory["source_refs"] = refs[-_MAX_SOURCE_REFS:]


def _trim_memory(memory: dict[str, Any]) -> None:
    limits = {
        "confirmed_facts": _MAX_CONFIRMED_FACTS,
        "constraints": _MAX_CONSTRAINTS,
        "open_questions": _MAX_OPEN_QUESTIONS,
        "hypotheses": _MAX_HYPOTHESES,
        "ruled_out_hypotheses": _MAX_RULED_OUT_HYPOTHESES,
        "key_evidence": _MAX_KEY_EVIDENCE,
        "actions_taken": _MAX_ACTIONS,
        "user_corrections": _MAX_USER_CORRECTIONS,
    }
    for key, limit in limits.items():
        items = [item for item in list(memory.get(key) or []) if item]
        memory[key] = _select_memory_items(items, limit)
    memory["source_refs"] = [item for item in list(memory.get("source_refs") or []) if item][-_MAX_SOURCE_REFS:]
    summary = str(memory.get("narrative_summary") or "")
    if len(summary) > _MAX_NARRATIVE_SUMMARY_CHARS:
        memory["narrative_summary"] = "…" + summary[-(_MAX_NARRATIVE_SUMMARY_CHARS - 1) :]


def _select_memory_items(items: list[Any], limit: int) -> list[Any]:
    if len(items) <= limit:
        return items
    scored = [(_item_priority(item, index), index, item) for index, item in enumerate(items)]
    selected_indexes = {index for _, index, _ in sorted(scored, reverse=True)[:limit]}
    return [item for index, item in enumerate(items) if index in selected_indexes]


def _item_priority(item: Any, index: int) -> float:
    if not isinstance(item, dict):
        return float(index) / 1000
    source_type = str(item.get("source_type") or "")
    score = float(_SOURCE_TYPE_PRIORITY.get(source_type, 50))
    confidence = _coerce_confidence(item.get("confidence"))
    if confidence is not None:
        score += confidence * 10
    evidence_strength = _coerce_confidence(item.get("evidence_strength"))
    if evidence_strength is not None:
        score += evidence_strength * 8
    if isinstance(item.get("refs"), dict) and item.get("refs"):
        score += 5
    score += min(index, 1000) / 1000
    return score


def _limit_for_key(key: str) -> int:
    return {
        "constraints": _MAX_CONSTRAINTS,
        "open_questions": _MAX_OPEN_QUESTIONS,
        "hypotheses": _MAX_HYPOTHESES,
        "ruled_out_hypotheses": _MAX_RULED_OUT_HYPOTHESES,
        "key_evidence": _MAX_KEY_EVIDENCE,
        "actions_taken": _MAX_ACTIONS,
        "user_corrections": _MAX_USER_CORRECTIONS,
    }.get(key, 12)


def _normalize_item_payload(key: str, item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    source = str(payload.get("source") or "runtime")
    payload["source"] = source
    payload.setdefault("source_type", _default_source_type(key, source))
    confidence = _coerce_confidence(payload.get("confidence"))
    if confidence is not None:
        payload["confidence"] = confidence
    return payload


def _identity_keys_for_item(key: str, item: dict[str, Any]) -> tuple[str, ...]:
    configured = tuple(k for k in _DEFAULT_IDENTITY_KEYS.get(key, ()) if _has_value(item.get(k)))
    if configured:
        return configured
    fallback = tuple(k for k in ("value", "evidence", "message", "source") if _has_value(item.get(k)))
    if fallback:
        return fallback
    return tuple(sorted(k for k in item.keys() if k not in {"created_at", "updated_at", "consumed_at"}))


def _default_source_type(key: str, source: str) -> str:
    normalized = source.lower()
    if key == "user_corrections" or normalized in {"feedback", "user_message"} and key != "key_evidence":
        return "user_correction"
    if normalized == "clarification":
        return "user_confirmed"
    if normalized in {"session_state", "context"}:
        return "system_state"
    if normalized in {"verification", "tool", "observation"}:
        return "tool_observed"
    if normalized in {"ranker", "ranker_rejected"}:
        return "ranker_selected"
    if normalized in {"approval_gate", "approval_request", "approved_action", "execution_result"}:
        return "approval_state"
    if "summary" in normalized:
        return "runtime_summary"
    if normalized in {"diagnosis", "finding"}:
        return "llm_inferred"
    if normalized == "user_message" and key == "key_evidence":
        return "user_reported"
    return "runtime_derived"


def _iter_ref_values(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if item not in (None, "")]
    return [value]


def _coerce_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    try:
        if isinstance(value, str) and value.strip():
            return max(0.0, min(float(value), 1.0))
    except ValueError:
        return None
    return None


def _compact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key): item for key, item in dict(value or {}).items() if _has_value(item)}


def _merge_mapping(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in dict(incoming or {}).items():
        if _has_value(value):
            merged[str(key)] = value
    return merged


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True
