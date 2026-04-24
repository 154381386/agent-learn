from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from it_ticket_agent.evals import export_bad_case_candidates
from it_ticket_agent.settings import Settings
from it_ticket_agent.storage import StoreProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export bad case candidates into eval skeleton files for manual curation."
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "data" / "evals" / "generated"),
        help="Directory to write generated eval skeleton files.",
    )
    parser.add_argument(
        "--candidate-id",
        action="append",
        default=[],
        help="Export only the specified candidate_id. Can be repeated.",
    )
    parser.add_argument(
        "--export-status",
        default="pending",
        help="Filter candidates by export_status when --candidate-id is not provided.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of candidates to export.",
    )
    parser.add_argument(
        "--mark-exported",
        action="store_true",
        help="Update exported candidates to export_status=exported after files are written.",
    )
    parser.add_argument(
        "--db-path",
        default="",
        help="Optional sqlite db path override. By default uses Settings.APPROVAL_DB_PATH.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = Settings()
    if args.db_path:
        settings.approval_db_path = args.db_path
        settings.storage_backend = "sqlite"
    stores = StoreProvider(settings).build()
    results = export_bad_case_candidates(
        stores.bad_case_candidate_store,
        output_dir=args.output_dir,
        candidate_ids=args.candidate_id,
        export_status=args.export_status or None,
        limit=max(1, int(args.limit)),
        mark_exported=args.mark_exported,
    )
    print(
        json.dumps(
            {
                "count": len(results),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
