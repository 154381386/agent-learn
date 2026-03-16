import argparse
import asyncio
import json

from it_ticket_agent.knowledge import KnowledgeBase
from it_ticket_agent.settings import get_settings


async def main(force: bool) -> None:
    result = await KnowledgeBase(get_settings()).reindex(force=force)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="force full rebuild instead of incremental sync")
    args = parser.parse_args()
    asyncio.run(main(force=args.force))
