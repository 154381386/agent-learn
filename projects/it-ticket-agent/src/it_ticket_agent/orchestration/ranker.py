from __future__ import annotations

from collections import Counter
from typing import Iterable

from ..state.models import RankedResult, SimilarIncidentCase, VerificationResult


class Ranker:
    def __init__(self, *, w1: float = 0.5, w2: float = 0.3, w3: float = 0.2) -> None:
        self.w1 = w1
        self.w2 = w2
        self.w3 = w3

    def rank(
        self,
        verification_results: Iterable[VerificationResult],
        *,
        similar_cases: list[SimilarIncidentCase] | None = None,
    ) -> RankedResult:
        items = list(verification_results)
        if not items:
            return RankedResult(
                primary=None,
                secondary=[],
                rejected=[],
                ranking_metadata={"scores": [], "weights": self._weights()},
            )

        scores: list[tuple[float, VerificationResult, float]] = []
        for result in items:
            history_match = self._history_match(result.root_cause, similar_cases or [])
            score = (
                self.w1 * float(result.evidence_strength or 0.0)
                + self.w2 * float(result.confidence or 0.0)
                + self.w3 * history_match
            )
            result.metadata["ranker"] = {
                "history_match": history_match,
                "final_score": score,
                "weights": self._weights(),
            }
            scores.append((score, result, history_match))

        scores.sort(key=lambda item: (item[0], item[1].confidence, item[1].evidence_strength), reverse=True)
        primary = scores[0][1]
        secondary = [item[1] for item in scores[1:] if item[0] > 0.4]
        rejected = [item[1] for item in scores[1:] if item[0] <= 0.4]

        return RankedResult(
            primary=primary,
            secondary=secondary,
            rejected=rejected,
            ranking_metadata={
                "weights": self._weights(),
                "scores": [
                    {
                        "hypothesis_id": result.hypothesis_id,
                        "root_cause": result.root_cause,
                        "final_score": score,
                        "history_match": history_match,
                    }
                    for score, result, history_match in scores
                ],
            },
        )

    def _history_match(self, root_cause: str, similar_cases: list[SimilarIncidentCase]) -> float:
        if not similar_cases:
            return 0.0
        root_tokens = self._tokens(root_cause)
        if not root_tokens:
            return 0.0
        best = 0.0
        for case in similar_cases:
            case_tokens = self._tokens(case.root_cause or case.summary or case.symptom)
            if not case_tokens:
                continue
            overlap = len(root_tokens & case_tokens)
            union = len(root_tokens | case_tokens)
            if union <= 0:
                continue
            best = max(best, overlap / union)
        return best

    @staticmethod
    def _tokens(text: str) -> set[str]:
        lowered = str(text or "").lower().replace("，", " ").replace("。", " ").replace("/", " ")
        parts = [part.strip() for part in lowered.split() if part.strip()]
        if parts:
            return set(parts)
        counts = Counter(ch for ch in lowered if ch.strip())
        return {ch for ch, count in counts.items() if count >= 1}

    def _weights(self) -> dict[str, float]:
        return {"evidence_strength": self.w1, "confidence": self.w2, "history_match": self.w3}
