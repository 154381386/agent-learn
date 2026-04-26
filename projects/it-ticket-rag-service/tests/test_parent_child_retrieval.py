from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from it_ticket_rag_service.knowledge import KnowledgeBase
from it_ticket_rag_service.settings import Settings


class ParentChildRetrievalTest(unittest.IsolatedAsyncioTestCase):
    async def test_child_recall_returns_parent_context(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory(dir=project_root) as temp_dir:
            temp_path = Path(temp_dir)
            docs_path = temp_path / "kb"
            index_path = temp_path / "index"
            docs_path.mkdir()
            (docs_path / "checkout-timeout.md").write_text(
                "\n".join(
                    [
                        "# Checkout Timeout Runbook",
                        "",
                        "## Dependency timeout",
                        "sentinelalpha checkout upstream read timeout appears in gateway logs and retry counters spike.",
                        "",
                        "Sibling evidence: inspect service mesh egress metrics and compare deployment timestamp before restart. siblingbeta",
                    ]
                ),
                encoding="utf-8",
            )
            settings = Settings(
                rag_vector_backend="local",
                rag_docs_path=str(docs_path),
                rag_index_dir=str(index_path),
                rag_chunk_size=100,
                rag_chunk_overlap=0,
                rag_parent_context_max_chars=2000,
                rag_top_k=1,
                embedding_base_url="",
                embedding_api_key="",
                rerank_base_url="",
                rerank_api_key="",
            )
            kb = KnowledgeBase(settings)

            await kb.reindex(force=True)
            result = await kb.search("sentinelalpha read timeout", top_k=1)

            self.assertEqual(result["index_info"]["parent_child_retrieval"], True)
            self.assertEqual(result["index_info"]["parent_blocks"], 1)
            hit = result["hits"][0]
            self.assertEqual(hit["retrieval_granularity"], "parent")
            self.assertTrue(hit["parent_id"])
            self.assertIn("sentinelalpha", hit["child_snippet"])
            self.assertNotIn("siblingbeta", hit["child_snippet"])
            self.assertIn("siblingbeta", hit["parent_snippet"])
            self.assertIn("siblingbeta", hit["snippet"])

    async def test_legacy_local_index_falls_back_to_chunk_parent(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory(dir=project_root) as temp_dir:
            temp_path = Path(temp_dir)
            docs_path = temp_path / "kb"
            index_path = temp_path / "index"
            docs_path.mkdir()
            index_path.mkdir()
            (docs_path / "legacy.md").write_text("# Legacy\n\nlegacytoken chunk only", encoding="utf-8")
            legacy_chunk = {
                "chunk_id": "legacy-chunk-1",
                "doc_id": "legacy-doc-1",
                "path": "legacy.md",
                "title": "Legacy",
                "section": "摘要",
                "category": "kb",
                "text": "legacytoken chunk only",
                "tokens": ["legacytoken", "chunk", "only"],
                "token_freq": {"legacytoken": 1, "chunk": 1, "only": 1},
                "header_tokens": ["legacy"],
                "length": 3,
                "embedding": None,
            }
            (index_path / "index.json").write_text(
                json.dumps(
                    {
                        "built_at": "2026-01-01T00:00:00+00:00",
                        "source_signature": "stale-but-auto-reindex-disabled",
                        "documents": [],
                        "avgdl": 3.0,
                        "idf": {"legacytoken": 1.0},
                        "embedding_enabled": False,
                        "chunks": [legacy_chunk],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            settings = Settings(
                rag_vector_backend="local",
                rag_docs_path=str(docs_path),
                rag_index_dir=str(index_path),
                rag_auto_reindex_on_boot=False,
                embedding_base_url="",
                embedding_api_key="",
                rerank_base_url="",
                rerank_api_key="",
            )
            kb = KnowledgeBase(settings)

            result = await kb.search("legacytoken", top_k=1)

            self.assertEqual(result["index_info"]["parent_blocks"], 1)
            hit = result["hits"][0]
            self.assertEqual(hit["parent_id"], "legacy-doc-1")
            self.assertEqual(hit["retrieval_granularity"], "parent")
            self.assertIn("legacytoken", hit["parent_snippet"])


if __name__ == "__main__":
    unittest.main()
