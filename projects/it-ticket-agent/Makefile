.PHONY: install run-agent run-orchestrator dev sync-kb reindex-kb pgvector-up pgvector-down pgvector-ps pgvector-logs

install:
	uv pip install -e .

run-orchestrator:
	uv run uvicorn it_ticket_agent.main:app --reload --port 8000

run-agent:
	uv run uvicorn it_ticket_agent.sample_agents:app --reload --port 8101

dev:
	./scripts/dev.sh

sync-kb:
	uv run python scripts/reindex_kb.py

reindex-kb:
	uv run python scripts/reindex_kb.py --force

pgvector-up:
	docker compose -f docker-compose.pgvector.yml up -d

pgvector-down:
	docker compose -f docker-compose.pgvector.yml down

pgvector-ps:
	docker compose -f docker-compose.pgvector.yml ps

pgvector-logs:
	docker compose -f docker-compose.pgvector.yml logs -f pgvector
