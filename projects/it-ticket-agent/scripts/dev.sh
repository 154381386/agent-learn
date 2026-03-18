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
  if [[ -n "${AGENT_PID:-}" ]]; then
    kill "$AGENT_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

echo "[1/3] Starting RAG service on :8200"
(
  cd "$RAG_DIR"
  uv run uvicorn it_ticket_rag_service.rag_service:app --reload --port 8200
) &
RAG_PID=$!

echo "[2/3] Starting sample agent runtime on :8101"
uv run uvicorn it_ticket_agent.sample_agents:app --reload --port 8101 &
AGENT_PID=$!

echo "[3/3] Starting orchestrator on :8000"
uv run uvicorn it_ticket_agent.main:app --reload --port 8000 &
ORCH_PID=$!

echo "Open http://localhost:8000"
wait "$ORCH_PID" "$AGENT_PID" "$RAG_PID"
