from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


def summarize_case_memory_recall(case_recall: Mapping[str, Any] | None) -> dict[str, Any]:
    recall = dict(case_recall or {})
    tool_failures = [
        dict(item)
        for item in list(recall.get("tool_failures") or [])
        if isinstance(item, Mapping)
    ]
    reason = _first_non_empty(
        recall.get("case_memory_reason"),
        _first_failure_error(tool_failures),
        recall.get("prefetch_reason"),
    )
    summary = {
        "state": "skipped",
        "reason": reason,
        "prefetch_status": str(recall.get("prefetch_status") or ""),
        "prefetch_reason": str(recall.get("prefetch_reason") or ""),
        "prefetched_case_count": _safe_int(recall.get("prefetched_case_count")),
        "tool_search_count": _safe_int(recall.get("tool_search_count")),
        "last_tool_status": str(recall.get("last_tool_status") or ""),
        "last_tool_hit_count": _safe_int(recall.get("last_tool_hit_count")),
        "tool_failure_count": len(tool_failures),
        "tool_failures": tool_failures,
        "raw": recall,
    }
    summary["state"] = classify_case_memory_recall(summary)
    if not summary["reason"]:
        summary["reason"] = _default_reason_for_state(summary)
    return summary


def classify_case_memory_recall(case_recall_or_summary: Mapping[str, Any] | None) -> str:
    data = dict(case_recall_or_summary or {})
    prefetch_status = str(data.get("prefetch_status") or "")
    last_tool_status = str(data.get("last_tool_status") or "")
    tool_failure_count = _safe_int(data.get("tool_failure_count"))
    if tool_failure_count == 0 and isinstance(data.get("tool_failures"), Sequence):
        tool_failure_count = len([item for item in list(data.get("tool_failures") or []) if isinstance(item, Mapping)])

    if prefetch_status == "error" or last_tool_status == "error" or tool_failure_count > 0:
        return "failed"

    prefetched_case_count = _safe_int(data.get("prefetched_case_count"))
    last_tool_hit_count = _safe_int(data.get("last_tool_hit_count"))
    tool_added_case_hits = _safe_int(data.get("tool_added_case_hits"))
    added_case_hits = _safe_int(data.get("added_case_hits"))
    if any(value > 0 for value in (prefetched_case_count, last_tool_hit_count, tool_added_case_hits, added_case_hits)):
        return "hit"

    tool_search_count = _safe_int(data.get("tool_search_count"))
    if prefetch_status == "completed" or tool_search_count > 0:
        return "empty"

    return "skipped"


def build_case_memory_reason_codes(case_recall: Mapping[str, Any] | None) -> list[str]:
    if not case_recall:
        return []
    summary = summarize_case_memory_recall(case_recall)
    state = str(summary.get("state") or "")
    reason = str(summary.get("reason") or "").strip()
    reason_slug = _slug_reason(reason)
    codes: list[str] = []
    if state == "failed":
        codes.append("case_memory_failed")
        if reason_slug:
            codes.append(f"case_memory_failed_{reason_slug}")
    elif state == "empty":
        codes.append("case_memory_empty")
    elif state == "skipped" and reason_slug:
        codes.append(f"case_memory_skipped_{reason_slug}")
    return _dedupe(codes)


def _first_failure_error(tool_failures: Sequence[Mapping[str, Any]]) -> str:
    for item in tool_failures:
        value = str(item.get("error") or item.get("error_type") or "").strip()
        if value:
            return value
    return ""


def _default_reason_for_state(summary: Mapping[str, Any]) -> str:
    state = str(summary.get("state") or "")
    if state == "empty":
        return "case_memory_empty"
    if state == "skipped":
        return "case_memory_not_attempted"
    if state == "hit":
        return "case_memory_hit"
    return ""


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _slug_reason(reason: str) -> str:
    normalized = str(reason or "").strip().lower().replace(":", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized[:80]


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _dedupe(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
