from __future__ import annotations

import argparse
import os

from .server import run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the mock CICD MCP server")
    parser.add_argument("--host", default=os.getenv("CICD_MCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CICD_MCP_PORT", "8900")))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_server(host=args.host, port=args.port)
