from __future__ import annotations

from collections import Counter
from typing import Any


class TopicShiftDetector:
    def detect(
        self,
        *,
        previous_message: str,
        current_message: str,
        previous_categories: list[str] | None = None,
        current_categories: list[str] | None = None,
    ) -> dict[str, Any]:
        previous_tokens = self._tokens(previous_message)
        current_tokens = self._tokens(current_message)
        overlap = len(previous_tokens & current_tokens)
        union = len(previous_tokens | current_tokens)
        similarity = overlap / union if union > 0 else 0.0
        previous_set = set(previous_categories or [])
        current_set = set(current_categories or [])
        incremental = sorted(current_set - previous_set)
        shifted = similarity < 0.35 or bool(incremental)
        return {
            "topic_shift_detected": shifted,
            "similarity": similarity,
            "previous_categories": sorted(previous_set),
            "current_categories": sorted(current_set),
            "incremental_skill_categories": incremental,
        }

    @staticmethod
    def _tokens(text: str) -> set[str]:
        lowered = str(text or "").lower().replace("，", " ").replace("。", " ").replace("/", " ")
        parts = [part.strip() for part in lowered.split() if part.strip()]
        if parts:
            return set(parts)
        counts = Counter(ch for ch in lowered if ch.strip())
        return {ch for ch, count in counts.items() if count >= 1}
