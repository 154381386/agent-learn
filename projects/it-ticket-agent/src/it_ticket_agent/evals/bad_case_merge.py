from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..bad_case_store import BadCaseCandidateStore
from ..session.models import utc_now


DEFAULT_TARGET_DATASET_PATHS = {
    "tool_mock": "data/evals/tool_mock_cases.json",
    "rag": "data/evals/rag_cases.json",
    "session_flow": "data/evals/session_flow_cases.json",
}


def merge_curated_bad_case_files(
    *,
    input_paths: Sequence[str | Path],
    project_root: str | Path,
    store: BadCaseCandidateStore | None = None,
    dataset_paths: Mapping[str, str | Path] | None = None,
    mark_merged: bool = True,
    allow_placeholders: bool = False,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    resolved_paths = {
        target: Path(project_root) / relative_path
        for target, relative_path in DEFAULT_TARGET_DATASET_PATHS.items()
    }
    if dataset_paths:
        resolved_paths.update({target: Path(path) for target, path in dataset_paths.items()})

    for input_path in input_paths:
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        result = merge_curated_bad_case_payload(
            payload=payload,
            source_path=input_path,
            dataset_paths=resolved_paths,
            store=store,
            mark_merged=mark_merged,
            allow_placeholders=allow_placeholders,
            dry_run=dry_run,
        )
        results.append(result)
    return results


def merge_curated_bad_case_payload(
    *,
    payload: dict[str, Any],
    source_path: str | Path,
    dataset_paths: Mapping[str, Path],
    store: BadCaseCandidateStore | None = None,
    mark_merged: bool = True,
    allow_placeholders: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    target_dataset = str(payload.get("target_dataset") or "").strip()
    if target_dataset not in dataset_paths:
        raise ValueError(f"unsupported target_dataset: {target_dataset or '<empty>'}")

    skeleton = dict(payload.get("eval_skeleton") or {})
    if not allow_placeholders:
        errors = validate_curated_eval_skeleton(skeleton)
        if errors:
            raise ValueError("curated merge validation failed: " + "; ".join(errors))

    dataset_path = Path(dataset_paths[target_dataset])
    dataset_payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = list(dataset_payload.get("cases") or [])
    case_id = str(skeleton.get("case_id") or "").strip()
    existing_index = next(
        (
            index
            for index, item in enumerate(cases)
            if str(dict(item).get("case_id") or "").strip() == case_id
        ),
        None,
    )
    action = "replaced" if existing_index is not None else "appended"
    if existing_index is not None:
        cases[existing_index] = skeleton
    else:
        cases.append(skeleton)
    dataset_payload["cases"] = cases

    if not dry_run:
        dataset_path.write_text(json.dumps(dataset_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        candidate_id = str(payload.get("candidate_id") or "").strip()
        if mark_merged and store is not None and candidate_id:
            existing = store.get(candidate_id)
            export_metadata = dict((existing or {}).get("export_metadata") or {})
            export_metadata.update(
                {
                    "merged_from": str(source_path),
                    "merged_dataset": str(dataset_path),
                    "merged_at": utc_now(),
                    "merged_case_id": case_id,
                    "target_dataset": target_dataset,
                }
            )
            store.update_export_status(
                candidate_id,
                export_status="merged",
                export_metadata=export_metadata,
            )

    return {
        "candidate_id": payload.get("candidate_id"),
        "case_id": case_id,
        "target_dataset": target_dataset,
        "dataset_path": str(dataset_path),
        "action": action,
        "source_path": str(source_path),
        "dry_run": dry_run,
    }


def validate_curated_eval_skeleton(skeleton: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    case_id = str(skeleton.get("case_id") or "").strip()
    description = str(skeleton.get("description") or "").strip()
    if not case_id:
        errors.append("missing case_id")
    elif case_id.startswith("todo_"):
        errors.append(f"case_id still placeholder: {case_id}")
    if not description:
        errors.append("missing description")
    elif "TODO" in description:
        errors.append("description still contains TODO")
    errors.extend(_collect_placeholder_errors(skeleton))
    return errors


def _collect_placeholder_errors(value: Any, *, path: str = "eval_skeleton") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if key == "_todo":
                errors.append(f"{item_path} still present")
            else:
                errors.extend(_collect_placeholder_errors(item, path=item_path))
        return errors
    if isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_collect_placeholder_errors(item, path=f"{path}[{index}]"))
        return errors
    if isinstance(value, str) and "TODO" in value:
        errors.append(f"{path} still contains TODO text")
    return errors
