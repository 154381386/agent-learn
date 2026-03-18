.PHONY: install run sync-kb reindex-kb pgvector-up pgvector-down pgvector-ps pgvector-logs

install:
	uv pip install -e .

run:
	uv run uvicorn it_ticket_rag_service.rag_service:app --reload --port 8200

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
