#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RAG_DIR="$(cd "$ROOT_DIR/../it-ticket-rag-service" && pwd)"
cd "$ROOT_DIR"

cleanup() {
  if [[ -n "${RAG_PID:-}" ]]; then
    kill "$RAG_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "${ORCH_PID:-}" ]]; then
    kill "$ORCH_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "${MCP_PID:-}" ]]; then
    kill "$MCP_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

echo "[0/4] Starting runtime postgres on :5433"
docker compose -f docker-compose.postgres.yml up -d

echo "[1/4] Starting RAG service on :8200"
(
  cd "$RAG_DIR"
  uv run uvicorn it_ticket_rag_service.rag_service:app --reload --port 8200
) &
RAG_PID=$!

echo "[2/4] Starting CICD MCP server on :8900"
(
  cd "$ROOT_DIR/../cicd-mcp-server"
  PYTHONPATH=src python3 -m cicd_mcp_server --host 127.0.0.1 --port 8900
) &
MCP_PID=$!

echo "[3/4] Starting orchestrator with Postgres backend on :8000"
STORAGE_BACKEND=postgres \
POSTGRES_DSN=postgresql://app:app@127.0.0.1:5433/it_ticket_agent_runtime \
uv run uvicorn it_ticket_agent.main:app --reload --port 8000 &
ORCH_PID=$!

echo "[4/4] Open http://localhost:8000"
wait "$ORCH_PID" "$MCP_PID" "$RAG_PID"
