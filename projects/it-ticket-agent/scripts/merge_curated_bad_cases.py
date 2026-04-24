from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from it_ticket_agent.evals import merge_curated_bad_case_files
from it_ticket_agent.settings import Settings
from it_ticket_agent.storage import StoreProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge curated generated bad-case skeleton files into formal eval datasets."
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Path to a curated generated bad-case JSON file. Can be repeated.",
    )
    parser.add_argument(
        "--input-dir",
        default=str(PROJECT_ROOT / "data" / "evals" / "generated"),
        help="Directory to scan when --input is not provided.",
    )
    parser.add_argument(
        "--pattern",
        default="*.json",
        help="Glob pattern used with --input-dir.",
    )
    parser.add_argument(
        "--allow-placeholders",
        action="store_true",
        help="Skip validation that blocks TODO placeholders and _todo fields.",
    )
    parser.add_argument(
        "--no-mark-merged",
        action="store_true",
        help="Do not update bad_case_candidate export_status to merged.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report merge actions without modifying dataset files or candidate status.",
    )
    parser.add_argument(
        "--db-path",
        default="",
        help="Optional sqlite db path override. By default uses Settings.APPROVAL_DB_PATH.",
    )
    return parser


def resolve_inputs(*, explicit_inputs: list[str], input_dir: str, pattern: str) -> list[Path]:
    if explicit_inputs:
        return [Path(item).resolve() for item in explicit_inputs]
    root = Path(input_dir).resolve()
    return sorted(path.resolve() for path in root.glob(pattern) if path.is_file())


def main() -> int:
    args = build_parser().parse_args()
    input_paths = resolve_inputs(
        explicit_inputs=list(args.input),
        input_dir=args.input_dir,
        pattern=args.pattern,
    )
    if not input_paths:
        print(json.dumps({"count": 0, "results": [], "message": "no curated files selected"}, ensure_ascii=False, indent=2))
        return 1

    settings = Settings()
    if args.db_path:
        settings.approval_db_path = args.db_path
        settings.storage_backend = "sqlite"
    stores = StoreProvider(settings).build()
    results = merge_curated_bad_case_files(
        input_paths=input_paths,
        project_root=PROJECT_ROOT,
        store=stores.bad_case_candidate_store,
        mark_merged=not args.no_mark_merged,
        allow_placeholders=args.allow_placeholders,
        dry_run=args.dry_run,
    )
    print(json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
