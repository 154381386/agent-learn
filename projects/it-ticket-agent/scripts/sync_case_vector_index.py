from __future__ import annotations

import argparse

from it_ticket_agent.case_vector_indexer import CaseVectorIndexer
from it_ticket_agent.memory.pg_store import PostgresProcessMemoryStoreV2
from it_ticket_agent.memory_store import IncidentCaseStore
from it_ticket_agent.rag_client import RAGServiceClient
from it_ticket_agent.settings import Settings


def build_store(settings: Settings) -> IncidentCaseStore:
    if settings.storage_backend == "postgres" and settings.postgres_dsn:
        return IncidentCaseStore(
            settings.approval_db_path,
            backend=PostgresProcessMemoryStoreV2(settings.postgres_dsn),
        )
    return IncidentCaseStore(settings.approval_db_path)


async def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(description="Sync incident cases into the case vector index.")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    store = build_store(settings)
    indexer = CaseVectorIndexer(settings, store, RAGServiceClient(settings))
    if not indexer.enabled:
        raise RuntimeError("case vector indexer is not enabled; check RAG_ENABLED and RAG service availability")
    count = await indexer.sync_all_cases(limit=args.limit)
    print(f"synced_case_vectors={count}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
