from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence

from ..bad_case_store import BadCaseCandidateStore
from ..session.models import utc_now


def select_bad_case_candidates(
    store: BadCaseCandidateStore,
    *,
    candidate_ids: Sequence[str] | None = None,
    export_status: str | None = "pending",
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not candidate_ids:
        return store.list_candidates(export_status=export_status, limit=limit)
    selected: list[dict[str, Any]] = []
    for candidate_id in candidate_ids:
        item = store.get(str(candidate_id))
        if item is not None:
            selected.append(item)
    return selected[:limit]


def classify_bad_case_candidate(candidate: dict[str, Any]) -> str:
    retrieval_expansion = dict(candidate.get("retrieval_expansion") or {})
    conversation_turns = [dict(item) for item in list(candidate.get("conversation_turns") or []) if isinstance(item, dict)]
    system_events = [dict(item) for item in list(candidate.get("system_events") or []) if isinstance(item, dict)]
    source = str(candidate.get("source") or "").strip()
    retrieval_signals = bool(
        list(retrieval_expansion.get("subqueries") or [])
        or int(retrieval_expansion.get("added_rag_hits") or 0) > 0
        or int(retrieval_expansion.get("added_case_hits") or 0) > 0
    )
    if retrieval_signals:
        return "rag"
    if source in {"feedback_reopen", "feedback_negative"}:
        return "session_flow"
    user_turn_count = sum(1 for item in conversation_turns if str(item.get("role") or "") == "user")
    if user_turn_count > 1:
        return "session_flow"
    if any(
        str(item.get("event_type") or "") in {"conversation.resumed", "feedback.reopened", "approval.approved", "approval.rejected"}
        for item in system_events
    ):
        return "session_flow"
    return "tool_mock"


def build_bad_case_export_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    target_dataset = classify_bad_case_candidate(candidate)
    request_payload = dict(candidate.get("request_payload") or {})
    response_payload = dict(candidate.get("response_payload") or {})
    observations = [dict(item) for item in list(candidate.get("observations") or []) if isinstance(item, dict)]
    retrieval_expansion = dict(candidate.get("retrieval_expansion") or {})
    focus_tools = _distinct_tool_names(observations)
    retrieval_queries = [
        str(dict(item).get("query") or "").strip()
        for item in list(retrieval_expansion.get("subqueries") or [])
        if isinstance(item, dict) and str(dict(item).get("query") or "").strip()
    ]
    return {
        "candidate_id": candidate.get("candidate_id"),
        "source": candidate.get("source"),
        "severity": candidate.get("severity"),
        "reason_codes": list(candidate.get("reason_codes") or []),
        "target_dataset": target_dataset,
        "request": _build_eval_request(request_payload),
        "mock_boundary_suggestions": _build_mock_boundary_suggestions(
            candidate=candidate,
            target_dataset=target_dataset,
            focus_tools=focus_tools,
            retrieval_queries=retrieval_queries,
        ),
        "eval_skeleton": _build_eval_skeleton(
            candidate=candidate,
            target_dataset=target_dataset,
            request=_build_eval_request(request_payload),
            focus_tools=focus_tools,
            retrieval_queries=retrieval_queries,
        ),
        "todo": _build_todo_items(
            candidate=candidate,
            target_dataset=target_dataset,
            focus_tools=focus_tools,
            retrieval_queries=retrieval_queries,
            response_payload=response_payload,
        ),
        "source_snapshot": {
            "response_payload": response_payload,
            "human_feedback": dict(candidate.get("human_feedback") or {}),
            "incident_state_snapshot": dict(candidate.get("incident_state_snapshot") or {}),
            "context_snapshot": dict(candidate.get("context_snapshot") or {}),
            "observation_count": len(observations),
        },
    }


def export_bad_case_candidates(
    store: BadCaseCandidateStore,
    *,
    output_dir: str,
    candidate_ids: Sequence[str] | None = None,
    export_status: str | None = "pending",
    limit: int = 50,
    mark_exported: bool = False,
) -> list[dict[str, Any]]:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for candidate in select_bad_case_candidates(
        store,
        candidate_ids=candidate_ids,
        export_status=export_status,
        limit=limit,
    ):
        payload = build_bad_case_export_payload(candidate)
        filename = _build_export_filename(candidate, target_dataset=str(payload.get("target_dataset") or "tool_mock"))
        output_path = output_root / filename
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {
            "candidate_id": candidate.get("candidate_id"),
            "target_dataset": payload.get("target_dataset"),
            "output_path": str(output_path),
        }
        results.append(result)
        if mark_exported:
            store.update_export_status(
                str(candidate.get("candidate_id") or ""),
                export_status="exported",
                export_metadata={
                    "output_path": str(output_path),
                    "target_dataset": payload.get("target_dataset"),
                    "exported_at": utc_now(),
                },
            )
    return results


def _build_eval_request(request_payload: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "user_id",
        "message",
        "service",
        "environment",
        "host_identifier",
        "db_name",
        "db_type",
        "cluster",
        "namespace",
        "channel",
    ]
    return {
        key: value
        for key, value in ((key, request_payload.get(key)) for key in keys)
        if value not in (None, "", [], {})
    }


def _build_mock_boundary_suggestions(
    *,
    candidate: dict[str, Any],
    target_dataset: str,
    focus_tools: list[str],
    retrieval_queries: list[str],
) -> dict[str, Any]:
    request_payload = dict(candidate.get("request_payload") or {})
    notes = [
        f"优先围绕 {', '.join(focus_tools[:3]) or '关键工具'} 固定 mock，先保证主搜索路径可复现。",
    ]
    if request_payload.get("mock_world_state"):
        notes.append("当前线上样本已经带 world_state，若工具间信号需要保持一致，可优先导成 world_state 驱动样本。")
    if target_dataset == "rag":
        notes.append("补齐初始 RAG 命中、query rewrite 子查询，以及每个子查询的新增命中边界。")
    if target_dataset == "session_flow":
        notes.append("把多轮 turn 和 resume 输入拆成 step，避免直接把整段会话压成单轮样本。")
    return {
        "primary_boundary": (
            "mock_rag_context / mock_rag_context_by_query / mock_similar_cases_by_query"
            if target_dataset == "rag"
            else "session steps + mock_tool_responses"
            if target_dataset == "session_flow"
            else "mock_tool_responses"
        ),
        "secondary_boundary": "mock_world_state" if request_payload.get("mock_world_state") else "tool_profile / case profile",
        "focus_tools": focus_tools[:5],
        "retrieval_queries": retrieval_queries[:5],
        "notes": notes,
    }


def _build_eval_skeleton(
    *,
    candidate: dict[str, Any],
    target_dataset: str,
    request: dict[str, Any],
    focus_tools: list[str],
    retrieval_queries: list[str],
) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "candidate")
    response_payload = dict(candidate.get("response_payload") or {})
    diagnosis = dict(response_payload.get("diagnosis") or {})
    route = str(diagnosis.get("route") or "react_tool_first")
    status = str(response_payload.get("status") or "completed")
    if target_dataset == "rag":
        return {
            "case_id": f"todo_{candidate_id[:8]}",
            "description": "TODO: 提炼 retrieval bad case 的问题、预期路径和正确主因。",
            "request": request,
            "setup": {
                "mock_rag_context": {},
                "mock_rag_context_by_query": {
                    query: {}
                    for query in retrieval_queries[:3]
                },
                "mock_similar_cases_by_query": {
                    query: []
                    for query in retrieval_queries[:3]
                },
                "mock_retrieval_expansion": {
                    "subqueries": [
                        {
                            "query": str(dict(item).get("query") or ""),
                            "target": dict(item).get("target") or "both",
                            "reason": dict(item).get("reason") or "",
                            "failure_mode": dict(item).get("failure_mode") or "",
                            "root_cause_taxonomy": dict(item).get("root_cause_taxonomy") or "",
                        }
                        for item in list(dict(candidate.get("retrieval_expansion") or {}).get("subqueries") or [])
                        if isinstance(item, dict)
                    ]
                },
            },
            "expect": {
                "status": status,
                "route": route,
                "min_retrieval_subquery_count": len(retrieval_queries),
                "retrieval_query_contains": retrieval_queries[:3],
                "_todo": [
                    "补每个 query 的 mock 命中和 added_* expectation",
                    "补最终主因方向和 missing_evidence 断言",
                ],
            },
        }
    if target_dataset == "session_flow":
        feedback_payload = dict(candidate.get("human_feedback") or {})
        return {
            "case_id": f"todo_{candidate_id[:8]}",
            "description": "TODO: 提炼多轮会话 bad case，包括触发条件、恢复输入和最终期望。",
            "setup": {
                "llm_mode": "live",
            },
            "steps": [
                {
                    "step_id": "start_case",
                    "action": "start_conversation",
                    "request": request,
                    "expect": {
                        "response_status": status,
                        "route": route,
                        "case_exists": True,
                    },
                },
                {
                    "step_id": "followup_or_resume",
                    "action": "resume_conversation" if feedback_payload else "post_message",
                    "request": (
                        {
                            "interrupt_id": "<fill-interrupt-id>",
                            "answer_payload": feedback_payload,
                        }
                        if feedback_payload
                        else {
                            "message": "<fill-followup-message>",
                        }
                    ),
                    "expect": {
                        "response_status": status,
                        "_todo": [
                            "补 pending_interrupt / message_event_type / new_system_event_types",
                        ],
                    },
                },
            ],
        }
    return {
        "case_id": f"todo_{candidate_id[:8]}",
        "description": "TODO: 提炼单轮诊断 bad case，固定关键工具边界并补 expect。",
        "request": request,
        "setup": {
            "mock_tool_responses": {
                tool_name: {}
                for tool_name in focus_tools[:3]
            },
        },
        "expect": {
            "status": status,
            "route": route,
            "required_any_tools": focus_tools[:3],
            "required_any_tools_min_matches": min(2, len(focus_tools[:3])) if focus_tools[:3] else 0,
            "_todo": [
                "补每个工具的 summary/payload/evidence mock",
                "补 evidence_contains / tool_calls 上下界 / forbidden_tools",
            ],
        },
    }


def _build_todo_items(
    *,
    candidate: dict[str, Any],
    target_dataset: str,
    focus_tools: list[str],
    retrieval_queries: list[str],
    response_payload: dict[str, Any],
) -> list[str]:
    todos = [
        "把 case_id 和 description 改成可读业务语义，不要保留自动导出的占位名。",
        "人工确认真实正确答案，并补充最终 expect，暂时不要直接并入正式 gate 数据集。",
    ]
    if focus_tools:
        todos.append(f"优先根据 observations 回填这些工具的 mock：{', '.join(focus_tools[:3])}。")
    if target_dataset == "rag" and retrieval_queries:
        todos.append(f"按 query 逐条补齐知识 mock：{', '.join(retrieval_queries[:3])}。")
    if target_dataset == "session_flow":
        todos.append("把 turns / system events 拆成 start、resume、post_message 等明确步骤。")
    if str(response_payload.get("status") or "") == "failed":
        todos.append("这个样本是 failed 终态，需补充是否应落入 approval/人工介入或 recovery 路径。")
    return todos


def _distinct_tool_names(observations: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in observations:
        tool_name = str(item.get("tool_name") or "").strip()
        if tool_name and tool_name not in names:
            names.append(tool_name)
    return names


def _build_export_filename(candidate: dict[str, Any], *, target_dataset: str) -> str:
    candidate_id = str(candidate.get("candidate_id") or "candidate")
    source = _sanitize_filename(str(candidate.get("source") or "runtime"))
    return f"{target_dataset}__{source}__{candidate_id[:12]}.json"


def _sanitize_filename(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or "candidate"
