.PHONY: install run-cicd-mcp run-rag-service run-orchestrator run-orchestrator-pg dev dev-pg migrate-runtime-pg test-runtime-pg sync-kb reindex-kb pg-up pg-down pg-ps pg-logs pgvector-up pgvector-down pgvector-ps pgvector-logs eval-agent eval-rag eval-world eval-session-flow eval-session-flow-live eval-regression

RAG_DIR=../it-ticket-rag-service
MCP_DIR=../cicd-mcp-server

install:
	uv pip install -e .

run-rag-service:
	cd $(RAG_DIR) && uv run uvicorn it_ticket_rag_service.rag_service:app --reload --port 8200

run-orchestrator:
	uv run uvicorn it_ticket_agent.main:app --reload --port 8000

run-orchestrator-pg:
	STORAGE_BACKEND=postgres \
	POSTGRES_DSN=postgresql://app:app@127.0.0.1:5433/it_ticket_agent_runtime \
	uv run uvicorn it_ticket_agent.main:app --reload --port 8000

run-cicd-mcp:
	cd $(MCP_DIR) && PYTHONPATH=src python3 -m cicd_mcp_server --host 0.0.0.0 --port 8900

dev:
	./scripts/dev.sh

dev-pg:
	./scripts/dev-pg.sh

migrate-runtime-pg:
	uv run python scripts/migrate_sqlite_to_postgres.py \
		--sqlite-path ./data/approvals.db \
		--postgres-dsn postgresql://app:app@127.0.0.1:5433/it_ticket_agent_runtime

test-runtime-pg:
	POSTGRES_TEST_DSN=postgresql://app:app@127.0.0.1:5433/it_ticket_agent_runtime \
	uv run python -m unittest tests.test_postgres_storage_smoke -q

eval-agent:
	uv run python scripts/run_agent_eval.py

eval-rag:
	uv run python scripts/run_agent_eval.py --dataset ./data/evals/rag_cases.json

eval-world:
	uv run python scripts/run_agent_eval.py --dataset ./data/evals/world_cases.json

eval-session-flow:
	uv run python scripts/run_agent_eval.py --dataset ./data/evals/session_flow_cases.json --allow-llm-disabled

eval-session-flow-live:
	uv run python scripts/run_agent_eval.py --dataset ./data/evals/session_flow_live_cases.json

eval-regression:
	$(MAKE) eval-agent
	$(MAKE) eval-rag
	$(MAKE) eval-world
	$(MAKE) eval-session-flow
	$(MAKE) eval-session-flow-live

pg-up:
	docker compose -f docker-compose.postgres.yml up -d

pg-down:
	docker compose -f docker-compose.postgres.yml down

pg-ps:
	docker compose -f docker-compose.postgres.yml ps

pg-logs:
	docker compose -f docker-compose.postgres.yml logs -f runtime-postgres

sync-kb:
	$(MAKE) -C $(RAG_DIR) sync-kb

reindex-kb:
	$(MAKE) -C $(RAG_DIR) reindex-kb

pgvector-up:
	$(MAKE) -C $(RAG_DIR) pgvector-up

pgvector-down:
	$(MAKE) -C $(RAG_DIR) pgvector-down

pgvector-ps:
	$(MAKE) -C $(RAG_DIR) pgvector-ps

pgvector-logs:
	$(MAKE) -C $(RAG_DIR) pgvector-logs
