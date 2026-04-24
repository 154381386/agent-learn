from __future__ import annotations

from .models import IncidentCase


def merge_incident_case_feedback(*, existing: IncidentCase | None, incoming: IncidentCase) -> IncidentCase:
    if existing is None:
        return incoming

    preserve_feedback = (
        bool(existing.human_verified)
        or bool(str(existing.actual_root_cause_hypothesis or "").strip())
        or bool(existing.hypothesis_accuracy)
        or existing.case_status == "verified"
    )
    if not preserve_feedback:
        return incoming

    incoming_actual_root = str(incoming.actual_root_cause_hypothesis or "").strip()
    incoming_accuracy = dict(incoming.hypothesis_accuracy or {})
    incoming_is_review = incoming.case_status in {"verified", "rejected"} or bool(incoming.reviewed_at)
    should_preserve_human_verified = (
        bool(existing.human_verified)
        and not bool(incoming.human_verified)
        and not incoming_actual_root
        and not incoming_accuracy
        and not incoming_is_review
    )
    should_preserve_case_status = (
        existing.case_status == "verified"
        and incoming.case_status in {"draft", "pending_review"}
        and not incoming_is_review
    )
    should_preserve_review = should_preserve_case_status or (
        bool(existing.reviewed_at)
        and not incoming.reviewed_at
        and not incoming_is_review
    )
    should_preserve_actual_root = not incoming_actual_root and bool(str(existing.actual_root_cause_hypothesis or "").strip())
    should_preserve_accuracy = not incoming_accuracy and bool(existing.hypothesis_accuracy)

    if not any(
        (
            should_preserve_human_verified,
            should_preserve_case_status,
            should_preserve_review,
            should_preserve_actual_root,
            should_preserve_accuracy,
        )
    ):
        return incoming

    return incoming.model_copy(
        update={
            "human_verified": existing.human_verified if should_preserve_human_verified else incoming.human_verified,
            "case_status": existing.case_status if should_preserve_case_status else incoming.case_status,
            "reviewed_by": existing.reviewed_by if should_preserve_review else incoming.reviewed_by,
            "reviewed_at": existing.reviewed_at if should_preserve_review else incoming.reviewed_at,
            "review_note": existing.review_note if should_preserve_review else incoming.review_note,
            "actual_root_cause_hypothesis": (
                existing.actual_root_cause_hypothesis
                if should_preserve_actual_root
                else incoming.actual_root_cause_hypothesis
            ),
            "hypothesis_accuracy": (
                dict(existing.hypothesis_accuracy)
                if should_preserve_accuracy
                else dict(incoming.hypothesis_accuracy)
            ),
        }
    )
