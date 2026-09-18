"""Contract tests for the vault knowledge store.

The assertions live in `_VectorStoreContract`, which is not a TestCase, and are
run once per backend by the concrete subclasses at the bottom. Writing them once
is the point: the backends only serve their purpose if they are interchangeable,
and the cheapest way to keep them that way is to refuse to describe their
behaviour twice.
"""

import os
import shutil
import tempfile
import unittest

from core.util.config import Config
from core.knowledge.vector import backends, probe
from core.knowledge.vector.db import (
    get_knowledge_db_path,
    get_db_connection,
    get_active_backend_name,
    init_knowledge_db,
    upsert_chunks,
    prune_deleted_files,
    hybrid_search_vault,
    build_fts_index,
    get_existing_hashes,
    add_chunks,
    count_chunks,
    scan_by_category,
    flush,
)


def _chunk(cid, file_path, category, text, content_hash, vector=None, tags="[]",
           title="Note", updated_at="2026-08-09T00:00:00"):
    return {
        "id": cid,
        "file_path": file_path,
        "category": category,
        "title": title,
        "header_path": "General",
        "tags": tags,
        "text": text,
        "raw_content": text,
        "vector": vector or [0.1, 0.2, 0.3, 0.4],
        "content_hash": content_hash,
        "updated_at": updated_at,
    }


class _VectorStoreContract:
    """Behaviour every backend must exhibit identically."""

    backend_name = None

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, ".lancedb")
        Config().knowledge_db_path = self.db_path
        Config().knowledge_backend = self.backend_name
        backends.reset()
        self.conn = get_db_connection(self.db_path)
        self.table = init_knowledge_db(conn=self.conn, db_path=self.db_path, dim=4)

    def tearDown(self):
        try:
            flush(self.table)
        except Exception:
            pass
        Config().reset()
        backends.reset()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -- wiring ---------------------------------------------------------

    def test_active_backend_is_the_one_under_test(self):
        self.assertEqual(get_active_backend_name(), self.backend_name)
        self.assertEqual(self.table.backend.name, self.backend_name)

    def test_get_knowledge_db_path(self):
        self.assertEqual(get_knowledge_db_path(), self.db_path)

    def test_init_knowledge_db_reopen(self):
        table2 = init_knowledge_db(conn=self.conn, db_path=self.db_path, dim=4)
        self.assertEqual(table2.name, "vault_chunks")

    def test_new_store_is_empty(self):
        self.assertEqual(count_chunks(self.table), 0)

    # -- upsert ---------------------------------------------------------

    def test_upsert_empty_chunks(self):
        stats = upsert_chunks(self.table, [])
        self.assertEqual(stats["inserted"], 0)
        self.assertEqual(stats["total_scanned"], 0)

    def test_upsert_deduplicate_ids_in_batch(self):
        chunk = _chunk("c_dup", "vault/dup.md", "vault", "Dup note content", "h_dup")
        stats = upsert_chunks(self.table, [chunk, chunk])
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(count_chunks(self.table), 1)

    def test_incremental_upsert(self):
        chunks = [_chunk("chunk_1", "vault/ai.md", "vault", "Initial text content", "hash_1")]
        upsert_chunks(self.table, chunks)

        # Same hash -> unchanged
        stats_unchanged = upsert_chunks(self.table, chunks)
        self.assertEqual(stats_unchanged["inserted"], 0)
        self.assertEqual(stats_unchanged["updated"], 0)
        self.assertEqual(stats_unchanged["unchanged"], 1)

        # Modified hash -> updated, and the row is replaced rather than added
        modified = [_chunk("chunk_1", "vault/ai.md", "vault", "Updated text content",
                           "hash_1_modified", vector=[0.2, 0.3, 0.4, 0.5])]
        stats_updated = upsert_chunks(self.table, modified)
        self.assertEqual(stats_updated["inserted"], 0)
        self.assertEqual(stats_updated["updated"], 1)
        self.assertEqual(stats_updated["unchanged"], 0)
        self.assertEqual(count_chunks(self.table), 1)

        hashes = get_existing_hashes(self.table)
        self.assertEqual(hashes["chunk_1"], "hash_1_modified")

    def test_add_chunks_appends_in_bulk(self):
        add_chunks(self.table, [
            _chunk("bulk_1", "vault/one.md", "vault", "first bulk row", "b1"),
            _chunk("bulk_2", "vault/two.md", "vault", "second bulk row", "b2"),
        ])
        self.assertEqual(count_chunks(self.table), 2)

    def test_add_chunks_empty_is_noop(self):
        add_chunks(self.table, [])
        self.assertEqual(count_chunks(self.table), 0)

    # -- search ---------------------------------------------------------

    def _seed_two_categories(self):
        chunks = [
            _chunk(
                "chunk_1", "vault/ai.md", "vault",
                "Title: Personal AI Notes\nCategory: vault\n\n"
                "Portmanteau provides hybrid vector search with BM25 indexing.",
                "hash_1", tags='["ai", "agents"]', title="Personal AI Notes",
            ),
            _chunk(
                "chunk_2", "wiki/concepts.md", "wiki",
                "Title: Synthesized AI Wiki\nCategory: wiki\n\n"
                "Portmanteau synthesis article for agent understanding.",
                "hash_2", tags='["wiki", "ai"]', title="Synthesized AI Wiki",
            ),
        ]
        stats = upsert_chunks(self.table, chunks)
        return stats

    def test_upsert_and_hybrid_search_with_category(self):
        stats = self._seed_two_categories()
        self.assertEqual(stats["inserted"], 2)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["unchanged"], 0)

        hashes = get_existing_hashes(self.table)
        self.assertEqual(hashes.get("chunk_1"), "hash_1")
        self.assertEqual(hashes.get("chunk_2"), "hash_2")

        vault_only = hybrid_search_vault(self.table, query="Portmanteau",
                                         category="vault", search_type="keyword")
        self.assertEqual(len(vault_only), 1)
        self.assertEqual(vault_only[0]["id"], "chunk_1")
        self.assertEqual(vault_only[0]["category"], "vault")

        wiki_only = hybrid_search_vault(self.table, query="Portmanteau",
                                        category="wiki", search_type="keyword")
        self.assertEqual(len(wiki_only), 1)
        self.assertEqual(wiki_only[0]["id"], "chunk_2")
        self.assertEqual(wiki_only[0]["category"], "wiki")

        all_results = hybrid_search_vault(self.table, query="Portmanteau",
                                          category="all", search_type="keyword")
        self.assertEqual(len(all_results), 2)

    def test_search_result_shape(self):
        self._seed_two_categories()
        result = hybrid_search_vault(self.table, query="Portmanteau",
                                     category="vault", search_type="keyword")[0]

        for key in ("id", "file_path", "category", "title", "header_path",
                    "tags", "text", "raw_content", "score", "updated_at"):
            self.assertIn(key, result)

        # tags are stored as a JSON string and handed back as a list
        self.assertEqual(result["tags"], ["ai", "agents"])
        self.assertIsInstance(result["score"], float)

    def test_search_respects_path_filter(self):
        self._seed_two_categories()
        results = hybrid_search_vault(self.table, query="Portmanteau",
                                      path_filter="wiki/", search_type="keyword")
        self.assertEqual([r["id"] for r in results], ["chunk_2"])

    def test_search_respects_limit(self):
        self._seed_two_categories()
        results = hybrid_search_vault(self.table, query="Portmanteau",
                                      limit=1, search_type="keyword")
        self.assertEqual(len(results), 1)

    def test_semantic_search_orders_by_vector_proximity(self):
        upsert_chunks(self.table, [
            _chunk("near", "vault/near.md", "vault", "alpha bravo", "hn", vector=[1.0, 0.0, 0.0, 0.0]),
            _chunk("far", "vault/far.md", "vault", "charlie delta", "hf", vector=[0.0, 1.0, 0.0, 0.0]),
        ])
        results = hybrid_search_vault(self.table, query="alpha",
                                      query_vector=[1.0, 0.0, 0.0, 0.0],
                                      search_type="semantic")
        self.assertEqual(results[0]["id"], "near")

    def test_hybrid_search_returns_both_signals(self):
        upsert_chunks(self.table, [
            _chunk("kw", "vault/kw.md", "vault", "alpha bravo", "h1", vector=[0.0, 1.0, 0.0, 0.0]),
            _chunk("vec", "vault/vec.md", "vault", "charlie delta", "h2", vector=[1.0, 0.0, 0.0, 0.0]),
        ])
        # 'kw' wins on BM25, 'vec' wins on vector distance; a fusion should surface both.
        results = hybrid_search_vault(self.table, query="alpha",
                                      query_vector=[1.0, 0.0, 0.0, 0.0],
                                      search_type="hybrid")
        self.assertEqual({r["id"] for r in results}, {"kw", "vec"})

    def test_hybrid_without_vector_falls_back_to_keyword(self):
        self._seed_two_categories()
        results = hybrid_search_vault(self.table, query="Portmanteau",
                                      query_vector=None, search_type="hybrid")
        self.assertEqual(len(results), 2)

    def test_search_empty_table(self):
        empty_dir = tempfile.mkdtemp()
        try:
            empty_table = init_knowledge_db(db_path=empty_dir, dim=4)
            self.assertEqual(
                hybrid_search_vault(empty_table, query="test", search_type="hybrid"), []
            )
            self.assertEqual(
                hybrid_search_vault(empty_table, query="test", search_type="keyword"), []
            )
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

    def test_search_no_match_returns_empty(self):
        self._seed_two_categories()
        results = hybrid_search_vault(self.table, query="zzzznotpresentzzzz",
                                      search_type="keyword")
        self.assertEqual(results, [])

    def test_build_fts_index_is_idempotent(self):
        self._seed_two_categories()
        build_fts_index(self.table)
        build_fts_index(self.table)
        results = hybrid_search_vault(self.table, query="Portmanteau", search_type="keyword")
        self.assertEqual(len(results), 2)

    # -- scan -----------------------------------------------------------

    def test_scan_by_category_returns_vectors(self):
        self._seed_two_categories()
        rows = scan_by_category(self.table, "wiki")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "chunk_2")
        # wiki_scanner averages these, so the vector must survive the round trip.
        self.assertEqual(len(rows[0]["vector"]), 4)
        self.assertAlmostEqual(rows[0]["vector"][0], 0.1, places=5)

    def test_scan_without_category_returns_everything(self):
        self._seed_two_categories()
        rows = scan_by_category(self.table, None)
        self.assertEqual({r["id"] for r in rows}, {"chunk_1", "chunk_2"})

    # -- prune ----------------------------------------------------------

    def test_prune_deleted_files(self):
        upsert_chunks(self.table, [
            _chunk("chunk_1", "vault/active.md", "vault", "Active note text", "h1"),
            _chunk("chunk_2", "wiki/deleted.md", "wiki", "Deleted note text", "h2"),
        ])

        pruned = prune_deleted_files(self.table, current_file_paths=["vault/active.md"])
        self.assertEqual(pruned, 1)

        remaining = scan_by_category(self.table, None)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["file_path"], "vault/active.md")

    def test_prune_counts_files_not_rows(self):
        # Two chunks from the same deleted file must count as one pruned file.
        upsert_chunks(self.table, [
            _chunk("keep", "vault/active.md", "vault", "kept", "h0"),
            _chunk("gone_a", "vault/gone.md", "vault", "gone a", "h1"),
            _chunk("gone_b", "vault/gone.md", "vault", "gone b", "h2"),
        ])
        self.assertEqual(
            prune_deleted_files(self.table, current_file_paths=["vault/active.md"]), 1
        )
        self.assertEqual(count_chunks(self.table), 1)

    def test_prune_nothing_to_do(self):
        upsert_chunks(self.table, [_chunk("keep", "vault/active.md", "vault", "kept", "h0")])
        self.assertEqual(
            prune_deleted_files(self.table, current_file_paths=["vault/active.md"]), 0
        )
        self.assertEqual(count_chunks(self.table), 1)

    def test_prune_empty_store(self):
        self.assertEqual(prune_deleted_files(self.table, current_file_paths=[]), 0)

    # -- durability -----------------------------------------------------

    def test_data_survives_reopen(self):
        self._seed_two_categories()
        flush(self.table)

        backends.reset()
        reopened = init_knowledge_db(db_path=self.db_path, dim=4)
        self.assertEqual(count_chunks(reopened), 2)
        self.assertEqual(get_existing_hashes(reopened)["chunk_1"], "hash_1")
        results = hybrid_search_vault(reopened, query="Portmanteau", search_type="keyword")
        self.assertEqual(len(results), 2)

    def test_force_recreate_clears_the_store(self):
        self._seed_two_categories()
        flush(self.table)

        recreated = init_knowledge_db(db_path=self.db_path, dim=4, force_recreate=True)
        self.assertEqual(count_chunks(recreated), 0)


class TestNumpyStoreContract(_VectorStoreContract, unittest.TestCase):
    backend_name = "numpy"


@unittest.skipUnless(
    probe.lancedb_usable(),
    "lancedb cannot be imported on this machine (CPU lacks the instructions its "
    "wheels assume); the numpy backend is what would run here anyway.",
)
class TestLanceDBStoreContract(_VectorStoreContract, unittest.TestCase):
    backend_name = "lancedb"


if __name__ == "__main__":
    unittest.main()
