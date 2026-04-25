from __future__ import annotations

import re
from typing import Any

from .case_retrieval import infer_failure_mode
from .memory_store import DiagnosisPlaybookStore
from .state.models import DiagnosisPlaybookCard


def infer_service_type(*, service: str = "", message: str = "") -> str:
    text = f"{service} {message}".lower()
    if any(token in text for token in ("mysql", "postgres", "database", "数据库", "慢查询", "连接池")):
        return "database"
    if any(token in text for token in ("quota", "配额", "bootstrap", "provision")):
        return "platform_resource"
    if str(service or "").strip():
        return "k8s_service"
    return ""


class PlaybookRetriever:
    def __init__(self, store: DiagnosisPlaybookStore) -> None:
        self.store = store
        self.last_recall_metadata: dict[str, Any] = {}

    async def recall(
        self,
        *,
        service: str,
        cluster: str,
        namespace: str,
        environment: str,
        message: str,
        limit: int = 2,
    ) -> list[DiagnosisPlaybookCard]:
        failure_mode = infer_failure_mode(message)
        service_type = infer_service_type(service=service, message=message)
        self.last_recall_metadata = {
            "status": "started",
            "reason": "playbook_recall_started",
            "service": service,
            "service_type": service_type,
            "cluster": cluster,
            "namespace": namespace,
            "environment": environment,
            "failure_mode": failure_mode,
            "top_k": limit,
        }
        try:
            candidates = self.store.list_playbooks(
                status="verified",
                human_verified=True,
                limit=100,
            )
        except Exception as exc:
            self.last_recall_metadata.update(
                {
                    "status": "error",
                    "reason": "playbook_store_failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "hit_count": 0,
                }
            )
            return []

        scored: list[tuple[float, DiagnosisPlaybookCard]] = []
        for row in candidates:
            score, card = self._score_candidate(
                row,
                service=service,
                service_type=service_type,
                cluster=cluster,
                namespace=namespace,
                environment=environment,
                message=message,
                failure_mode=failure_mode,
            )
            if score >= 0.3:
                scored.append((score, card))
        scored.sort(key=lambda item: (item[0], item[1].playbook_id), reverse=True)
        cards = [card for _, card in scored[: max(1, limit)]]
        self.last_recall_metadata.update(
            {
                "status": "completed",
                "reason": "playbook_recall_completed" if cards else "no_verified_playbook_matched",
                "candidate_count": len(candidates),
                "hit_count": len(cards),
                "playbook_ids": [card.playbook_id for card in cards],
            }
        )
        return cards

    def _score_candidate(
        self,
        row: dict[str, Any],
        *,
        service: str,
        service_type: str,
        cluster: str,
        namespace: str,
        environment: str,
        message: str,
        failure_mode: str,
    ) -> tuple[float, DiagnosisPlaybookCard]:
        lowered = str(message or "").lower()
        score = 0.0
        reasons: list[str] = []
        matched_failure_modes: list[str] = []
        matched_signals: list[str] = []

        negative_conditions = _as_text_list(row.get("negative_conditions"))
        if any(_pattern_matches(item, lowered) for item in negative_conditions):
            return 0.0, self._to_card(row, 0.0, "negative_condition_matched", [], [])

        failure_modes = _as_text_list(row.get("failure_modes"))
        if failure_mode and failure_mode in failure_modes:
            score += 0.35
            matched_failure_modes.append(failure_mode)
            reasons.append(f"failure_mode:{failure_mode}")
        for mode in failure_modes:
            if mode and _pattern_matches(mode, lowered) and mode not in matched_failure_modes:
                score += 0.2
                matched_failure_modes.append(mode)
                reasons.append(f"failure_mode_keyword:{mode}")

        signal_patterns = _as_text_list(row.get("signal_patterns"))
        trigger_conditions = _as_text_list(row.get("trigger_conditions"))
        for signal in signal_patterns + trigger_conditions:
            if signal and _pattern_matches(signal, lowered) and signal not in matched_signals:
                score += 0.12
                matched_signals.append(signal)
                reasons.append(f"signal:{signal}")
            if len(matched_signals) >= 4:
                break

        row_service_type = str(row.get("service_type") or "").strip()
        if row_service_type and service_type and row_service_type == service_type:
            score += 0.12
            reasons.append(f"service_type:{service_type}")
        elif row_service_type in {"", "generic", "any"}:
            score += 0.04
            reasons.append("service_type:generic")

        environments = _as_text_list(row.get("environments"))
        environment_values = {value.lower() for value in (environment, cluster, namespace) if value}
        if environments and environment_values.intersection({item.lower() for item in environments}):
            score += 0.06
            reasons.append("environment_match")
        elif not environments:
            score += 0.03
            reasons.append("environment:generic")

        required_entities = {item.lower() for item in _as_text_list(row.get("required_entities"))}
        if "service" in required_entities and service:
            score += 0.03
        if "cluster" in required_entities and cluster:
            score += 0.02
        if "namespace" in required_entities and namespace:
            score += 0.02

        success_count = int(row.get("success_count") or 0)
        failure_count = int(row.get("failure_count") or 0)
        if success_count or failure_count:
            score += max(0.0, min(0.08, 0.08 * success_count / max(1, success_count + failure_count)))
        if row.get("last_eval_passed") is True:
            score += 0.04
            reasons.append("last_eval_passed")

        card = self._to_card(
            row,
            round(min(score, 0.99), 4),
            ",".join(reasons) or "metadata_match",
            matched_failure_modes,
            matched_signals,
        )
        return card.recall_score, card

    @staticmethod
    def _to_card(
        row: dict[str, Any],
        score: float,
        reason: str,
        matched_failure_modes: list[str],
        matched_signals: list[str],
    ) -> DiagnosisPlaybookCard:
        return DiagnosisPlaybookCard(
            playbook_id=str(row.get("playbook_id") or ""),
            version=int(row.get("version") or 1),
            title=str(row.get("title") or ""),
            service_type=str(row.get("service_type") or ""),
            failure_modes=_as_text_list(row.get("failure_modes")),
            matched_failure_modes=matched_failure_modes,
            matched_signals=matched_signals,
            diagnostic_goal=str(row.get("diagnostic_goal") or ""),
            recommended_steps=_compact_steps(row.get("diagnostic_steps")),
            evidence_requirements=_as_text_list(row.get("evidence_requirements"))[:6],
            guardrails=_as_text_list(row.get("guardrails"))[:6],
            common_false_positives=_as_text_list(row.get("common_false_positives"))[:4],
            recall_score=score,
            recall_reason=reason,
        )


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _pattern_matches(pattern: str, text: str) -> bool:
    normalized = str(pattern or "").strip().lower()
    if not normalized:
        return False
    if normalized in text:
        return True
    parts = [part for part in re.split(r"[\s,+/|;:_\-]+", normalized) if len(part) >= 2]
    if not parts:
        return False
    return any(part in text for part in parts)


def _compact_steps(value: Any) -> list[dict[str, Any]]:
    steps = value if isinstance(value, list) else []
    compacted: list[dict[str, Any]] = []
    for item in steps:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or item.get("tool") or "").strip()
        purpose = str(item.get("purpose") or item.get("reason") or item.get("description") or "").strip()
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        compacted.append(
            {
                "tool_name": tool_name,
                "purpose": purpose,
                "params": dict(params),
            }
        )
        if len(compacted) >= 5:
            break
    return compacted
