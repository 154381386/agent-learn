#!/usr/bin/env python3
import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

SKILL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SKILL_DIR / "backends"

DEFAULT_MX_DATA = Path(os.environ.get("WYCKOFF_MX_DATA_PATH", BACKEND_DIR / "mx_data.py"))
DEFAULT_MX_XUANGU = Path(os.environ.get("WYCKOFF_MX_XUANGU_PATH", BACKEND_DIR / "mx_xuangu.py"))
DEFAULT_MX_SEARCH = Path(os.environ.get("WYCKOFF_MX_SEARCH_PATH", BACKEND_DIR / "mx_search.py"))
DEFAULT_STOCK_DATA = Path(os.environ.get("WYCKOFF_STOCK_DATA_PATH", BACKEND_DIR / "stock_data" / "stock_data.py"))


def file_ok(path: Path) -> bool:
    return path.exists() and path.is_file()


def can_run(path: Path) -> bool:
    return file_ok(path) and shutil.which("python3") is not None


def has_mx() -> bool:
    return bool(os.environ.get("MX_APIKEY"))


def run_command(command: List[str]) -> int:
    print("[router] backend:", shlex.join(command), file=sys.stderr)
    result = subprocess.run(command)
    return result.returncode


def resolved_paths() -> Tuple[Path, Path, Path, Path]:
    return (
        DEFAULT_MX_DATA,
        DEFAULT_MX_XUANGU,
        DEFAULT_MX_SEARCH,
        DEFAULT_STOCK_DATA,
    )


def backend_status() -> List[Tuple[str, bool, str]]:
    mx_data_path, mx_xuangu_path, mx_search_path, stock_data_path = resolved_paths()
    items = []
    items.append(("mx-data", can_run(mx_data_path) and has_mx(), f"script={mx_data_path} MX_APIKEY={'yes' if has_mx() else 'no'}"))
    items.append(("mx-xuangu", can_run(mx_xuangu_path) and has_mx(), f"script={mx_xuangu_path} MX_APIKEY={'yes' if has_mx() else 'no'}"))
    items.append(("mx-search", can_run(mx_search_path) and has_mx(), f"script={mx_search_path} MX_APIKEY={'yes' if has_mx() else 'no'}"))
    items.append(("stock_data", can_run(stock_data_path), f"script={stock_data_path}"))
    return items


def print_availability() -> int:
    print("Data source availability")
    print("=" * 72)
    for name, ok, detail in backend_status():
        print(f"{name:12} {'OK' if ok else 'MISSING/UNAVAILABLE':18} {detail}")
    print("=" * 72)
    print("This skill is self-contained. Backends are vendored inside the skill folder.")
    print("Recommended stack:")
    print("- 一级行业/板块资金流与板块行情: mx-data")
    print("- 细分板块成分股/板块内筛选: mx-xuangu")
    print("- 个股实时与日K: stock_data")
    print("- 新闻/政策/催化验证: mx-search")
    print("Environment overrides:")
    print("- WYCKOFF_MX_DATA_PATH")
    print("- WYCKOFF_MX_XUANGU_PATH")
    print("- WYCKOFF_MX_SEARCH_PATH")
    print("- WYCKOFF_STOCK_DATA_PATH")
    return 0


def run_mx_data(query: str) -> int:
    mx_data_path, _, _, _ = resolved_paths()
    if not can_run(mx_data_path):
        print(f"mx-data script not found: {mx_data_path}", file=sys.stderr)
        return 2
    if not has_mx():
        print("MX_APIKEY is not set; mx-data cannot run", file=sys.stderr)
        return 2
    return run_command(["python3", str(mx_data_path), query])


def run_mx_xuangu(query: str) -> int:
    _, mx_xuangu_path, _, _ = resolved_paths()
    if not can_run(mx_xuangu_path):
        print(f"mx-xuangu script not found: {mx_xuangu_path}", file=sys.stderr)
        return 2
    if not has_mx():
        print("MX_APIKEY is not set; mx-xuangu cannot run", file=sys.stderr)
        return 2
    return run_command(["python3", str(mx_xuangu_path), query])


def run_mx_search(query: str) -> int:
    _, _, mx_search_path, _ = resolved_paths()
    if not can_run(mx_search_path):
        print(f"mx-search script not found: {mx_search_path}", file=sys.stderr)
        return 2
    if not has_mx():
        print("MX_APIKEY is not set; mx-search cannot run", file=sys.stderr)
        return 2
    return run_command(["python3", str(mx_search_path), query])


def run_stock_data(subcommand: str, symbol: str, datalen: Optional[int], scale: Optional[int], use_json: bool) -> int:
    _, _, _, stock_data_path = resolved_paths()
    if not can_run(stock_data_path):
        print(f"stock_data script not found: {stock_data_path}", file=sys.stderr)
        return 2
    command = ["python3", str(stock_data_path), subcommand, symbol]
    if subcommand == "kline":
        if datalen is not None:
            command.extend(["-n", str(datalen)])
        if scale is not None:
            command.extend(["--scale", str(scale)])
    if use_json:
        command.append("--json")
    return run_command(command)


def main() -> int:
    parser = argparse.ArgumentParser(description="Route live market-data requests across vendored backends.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("availability", help="Show which backends are currently usable")

    sector_flow = subparsers.add_parser("sector-flow", help="Query一级行业/板块资金流")
    sector_flow.add_argument("--query", required=True)

    sector_spot = subparsers.add_parser("sector-spot", help="Query板块/行业实时行情")
    sector_spot.add_argument("--query", required=True)

    sector_members = subparsers.add_parser("sector-members", help="Query板块成分股或细分板块筛选")
    sector_members.add_argument("--query", required=True)

    stock_quote = subparsers.add_parser("stock-quote", help="Query个股实时行情")
    stock_quote.add_argument("symbol")
    stock_quote.add_argument("--json", action="store_true")

    stock_kline = subparsers.add_parser("stock-kline", help="Query个股K线")
    stock_kline.add_argument("symbol")
    stock_kline.add_argument("-n", "--datalen", type=int, default=60)
    stock_kline.add_argument("--scale", type=int, default=240)
    stock_kline.add_argument("--json", action="store_true")

    stock_news = subparsers.add_parser("stock-news", help="Query新闻/催化/政策")
    stock_news.add_argument("--query", required=True)

    args = parser.parse_args()

    if args.action == "availability":
        return print_availability()
    if args.action == "sector-flow":
        return run_mx_data(args.query)
    if args.action == "sector-spot":
        return run_mx_data(args.query)
    if args.action == "sector-members":
        return run_mx_xuangu(args.query)
    if args.action == "stock-quote":
        return run_stock_data("quote", args.symbol, None, None, args.json)
    if args.action == "stock-kline":
        return run_stock_data("kline", args.symbol, args.datalen, args.scale, args.json)
    if args.action == "stock-news":
        return run_mx_search(args.query)

    print("Unknown action", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
