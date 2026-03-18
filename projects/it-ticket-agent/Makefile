.PHONY: install run-agent run-rag-service run-orchestrator dev sync-kb reindex-kb pgvector-up pgvector-down pgvector-ps pgvector-logs

RAG_DIR=../it-ticket-rag-service

install:
	uv pip install -e .

run-rag-service:
	cd $(RAG_DIR) && uv run uvicorn it_ticket_rag_service.rag_service:app --reload --port 8200

run-orchestrator:
	uv run uvicorn it_ticket_agent.main:app --reload --port 8000

run-agent:
	uv run uvicorn it_ticket_agent.sample_agents:app --reload --port 8101

dev:
	./scripts/dev.sh

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
