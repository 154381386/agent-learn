from __future__ import annotations

from .models import IncidentCase


def merge_incident_case_feedback(*, existing: IncidentCase | None, incoming: IncidentCase) -> IncidentCase:
    if existing is None:
        return incoming

    preserve_feedback = (
        bool(existing.human_verified)
        or bool(str(existing.actual_root_cause_hypothesis or "").strip())
        or bool(existing.hypothesis_accuracy)
    )
    if not preserve_feedback:
        return incoming

    incoming_actual_root = str(incoming.actual_root_cause_hypothesis or "").strip()
    incoming_accuracy = dict(incoming.hypothesis_accuracy or {})
    should_preserve_human_verified = (
        bool(existing.human_verified)
        and not bool(incoming.human_verified)
        and not incoming_actual_root
        and not incoming_accuracy
    )
    should_preserve_actual_root = not incoming_actual_root and bool(str(existing.actual_root_cause_hypothesis or "").strip())
    should_preserve_accuracy = not incoming_accuracy and bool(existing.hypothesis_accuracy)

    if not any((should_preserve_human_verified, should_preserve_actual_root, should_preserve_accuracy)):
        return incoming

    return incoming.model_copy(
        update={
            "human_verified": existing.human_verified if should_preserve_human_verified else incoming.human_verified,
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
