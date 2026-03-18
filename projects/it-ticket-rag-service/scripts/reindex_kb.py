import argparse
import asyncio
import json

import httpx

from it_ticket_rag_service.settings import get_settings


async def main(force: bool, base_url: str | None) -> None:
    settings = get_settings()
    target_base_url = (base_url or f"http://localhost:{settings.port}").rstrip("/")
    endpoint = "/api/v1/rag/reindex" if force else "/api/v1/rag/sync"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{target_base_url}{endpoint}")
        response.raise_for_status()
        result = response.json()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="force full rebuild instead of incremental sync")
    parser.add_argument("--base-url", help="rag service base url, default http://localhost:8200")
    args = parser.parse_args()
    asyncio.run(main(force=args.force, base_url=args.base_url))
