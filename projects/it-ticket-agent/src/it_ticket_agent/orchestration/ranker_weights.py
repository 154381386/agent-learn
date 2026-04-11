from __future__ import annotations

import json
import os
import sqlite3
from typing import Any
from uuid import uuid4

from ..session.models import utc_now


DEFAULT_WEIGHTS = {
    "evidence_strength": 0.5,
    "confidence": 0.3,
    "history_match": 0.2,
}


def estimate_adaptive_weights(cases: list[dict[str, Any]] | None) -> dict[str, float]:
    items = list(cases or [])
    if not items:
        return dict(DEFAULT_WEIGHTS)

    success_totals = {key: 0.0 for key in DEFAULT_WEIGHTS}
    failure_totals = {key: 0.0 for key in DEFAULT_WEIGHTS}
    success_count = 0
    failure_count = 0

    for case in items:
        if not bool(case.get("human_verified")):
            continue
        selected_hypothesis_id = str(case.get("selected_hypothesis_id") or "")
        actual_root = str(case.get("actual_root_cause_hypothesis") or "")
        features = dict(case.get("selected_ranker_features") or {})
        if not selected_hypothesis_id or not features:
            continue
        is_success = actual_root == selected_hypothesis_id or (
            not actual_root and float(dict(case.get("hypothesis_accuracy") or {}).get(selected_hypothesis_id, 0.0)) >= 0.8
        )
        target = success_totals if is_success else failure_totals
        if is_success:
            success_count += 1
        else:
            failure_count += 1
        for key in DEFAULT_WEIGHTS:
            target[key] += float(features.get(key, 0.0))

    if success_count <= 0 and failure_count <= 0:
        return dict(DEFAULT_WEIGHTS)

    raw_scores: dict[str, float] = {}
    for key, default_value in DEFAULT_WEIGHTS.items():
        success_avg = success_totals[key] / success_count if success_count > 0 else default_value
        failure_avg = failure_totals[key] / failure_count if failure_count > 0 else 0.0
        raw_scores[key] = max(0.05, default_value + success_avg - 0.5 * failure_avg)

    total = sum(raw_scores.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {key: value / total for key, value in raw_scores.items()}


class RankerWeightsManager:
    def __init__(self, db_path: str, *, auto_activate_threshold: int = 3) -> None:
        self.db_path = db_path
        self.auto_activate_threshold = auto_activate_threshold
        folder = os.path.dirname(db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists ranker_weight_snapshot (
                    version_id text primary key,
                    weights_json text not null,
                    sample_count integer not null,
                    strategy text not null,
                    is_active integer not null default 0,
                    metadata_json text not null default '{}',
                    created_at text not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_ranker_weight_snapshot_active_created
                on ranker_weight_snapshot (is_active, created_at desc, version_id desc)
                """
            )
            conn.commit()

    def list_snapshots(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select version_id, weights_json, sample_count, strategy, is_active, metadata_json, created_at
                from ranker_weight_snapshot
                order by created_at desc, version_id desc
                """
            ).fetchall()
        return [self._row_to_snapshot(row) for row in rows]

    def get_active_snapshot(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select version_id, weights_json, sample_count, strategy, is_active, metadata_json, created_at
                from ranker_weight_snapshot
                where is_active = 1
                order by created_at desc, version_id desc
                limit 1
                """
            ).fetchone()
        return None if row is None else self._row_to_snapshot(row)

    def save_snapshot(
        self,
        weights: dict[str, float],
        *,
        sample_count: int,
        strategy: str,
        metadata: dict[str, Any] | None = None,
        activate: bool = False,
    ) -> dict[str, Any]:
        snapshot = {
            "version_id": str(uuid4()),
            "weights": {key: float(weights[key]) for key in DEFAULT_WEIGHTS},
            "sample_count": int(sample_count),
            "strategy": strategy,
            "is_active": bool(activate),
            "metadata": dict(metadata or {}),
            "created_at": utc_now(),
        }
        with self._connect() as conn:
            if activate:
                conn.execute("update ranker_weight_snapshot set is_active = 0 where is_active = 1")
            conn.execute(
                """
                insert into ranker_weight_snapshot (
                    version_id, weights_json, sample_count, strategy, is_active, metadata_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot["version_id"],
                    json.dumps(snapshot["weights"], ensure_ascii=False),
                    snapshot["sample_count"],
                    snapshot["strategy"],
                    int(snapshot["is_active"]),
                    json.dumps(snapshot["metadata"], ensure_ascii=False),
                    snapshot["created_at"],
                ),
            )
            conn.commit()
        return snapshot

    def activate_snapshot(self, version_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select version_id, weights_json, sample_count, strategy, is_active, metadata_json, created_at
                from ranker_weight_snapshot
                where version_id = ?
                """,
                (version_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute("update ranker_weight_snapshot set is_active = 0 where is_active = 1")
            conn.execute("update ranker_weight_snapshot set is_active = 1 where version_id = ?", (version_id,))
            conn.commit()
        return self.get_active_snapshot()

    def resolve_weights(self, feedback_cases: list[dict[str, Any]] | None) -> dict[str, float]:
        feedback_items = [case for case in list(feedback_cases or []) if bool(case.get("human_verified"))]
        active = self.get_active_snapshot()
        if len(feedback_items) < self.auto_activate_threshold:
            return dict(active["weights"]) if active is not None else dict(DEFAULT_WEIGHTS)

        candidate = estimate_adaptive_weights(feedback_items)
        metadata = {
            "verified_case_count": len(feedback_items),
            "source": "feedback_cases",
        }
        if active is None:
            self.save_snapshot(
                candidate,
                sample_count=len(feedback_items),
                strategy="adaptive_feedback",
                metadata=metadata,
                activate=True,
            )
            return candidate
        if self._is_material_change(active["weights"], candidate):
            self.save_snapshot(
                candidate,
                sample_count=len(feedback_items),
                strategy="adaptive_feedback",
                metadata=metadata,
                activate=True,
            )
            return candidate
        return dict(active["weights"])

    @staticmethod
    def _is_material_change(current: dict[str, float], candidate: dict[str, float], *, tolerance: float = 0.05) -> bool:
        return any(abs(float(current.get(key, 0.0)) - float(candidate.get(key, 0.0))) >= tolerance for key in DEFAULT_WEIGHTS)

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "version_id": row["version_id"],
            "weights": json.loads(row["weights_json"]),
            "sample_count": int(row["sample_count"]),
            "strategy": row["strategy"],
            "is_active": bool(row["is_active"]),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
        }
