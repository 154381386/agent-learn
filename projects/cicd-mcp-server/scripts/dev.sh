#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHONPATH=src python3 -m cicd_mcp_server --host 0.0.0.0 --port 8900
