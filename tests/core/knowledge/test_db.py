import os
import shutil
import tempfile
import unittest
from core.config import Config
from core.knowledge.db import (
    get_knowledge_db_path,
    get_db_connection,
    init_knowledge_db,
    upsert_chunks,
    prune_deleted_files,
    hybrid_search_vault,
    build_fts_index,
    get_existing_hashes,
)


class TestKnowledgeDB(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, ".lancedb")
        Config().knowledge_db_path = self.db_path
        self.conn = get_db_connection(self.db_path)
        self.table = init_knowledge_db(conn=self.conn, db_path=self.db_path, dim=4)

    def tearDown(self):
        Config().reset()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_knowledge_db_path(self):
        self.assertEqual(get_knowledge_db_path(), self.db_path)

    def test_init_knowledge_db_reopen(self):
        # Opening table when already initialized
        table2 = init_knowledge_db(conn=self.conn, db_path=self.db_path, dim=4)
        self.assertEqual(table2.name, "vault_chunks")

    def test_upsert_empty_chunks(self):
        stats = upsert_chunks(self.table, [])
        self.assertEqual(stats["inserted"], 0)
        self.assertEqual(stats["total_scanned"], 0)

    def test_upsert_deduplicate_ids_in_batch(self):
        chunk = {
            "id": "c_dup",
            "file_path": "vault/dup.md",
            "category": "vault",
            "title": "Dup Note",
            "header_path": "General",
            "tags": '[]',
            "text": "Dup note content",
            "raw_content": "Dup note content",
            "vector": [0.1, 0.2, 0.3, 0.4],
            "content_hash": "h_dup",
            "updated_at": "2026-08-09T00:00:00"
        }
        stats = upsert_chunks(self.table, [chunk, chunk])
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(self.table.count_rows(), 1)

    def test_upsert_and_hybrid_search_with_category(self):
        chunks = [
            {
                "id": "chunk_1",
                "file_path": "vault/ai.md",
                "category": "vault",
                "title": "Personal AI Notes",
                "header_path": "Architecture",
                "tags": '["ai", "agents"]',
                "text": "Title: Personal AI Notes\nCategory: vault\n\nLanceDB provides hybrid vector search with BM25 indexing.",
                "raw_content": "LanceDB provides hybrid vector search with BM25 indexing.",
                "vector": [0.1, 0.2, 0.3, 0.4],
                "content_hash": "hash_1",
                "updated_at": "2026-08-09T00:00:00"
            },
            {
                "id": "chunk_2",
                "file_path": "wiki/concepts.md",
                "category": "wiki",
                "title": "Synthesized AI Wiki",
                "header_path": "Architecture",
                "tags": '["wiki", "ai"]',
                "text": "Title: Synthesized AI Wiki\nCategory: wiki\n\nLanceDB synthesis article for agent understanding.",
                "raw_content": "LanceDB synthesis article for agent understanding.",
                "vector": [0.1, 0.2, 0.3, 0.4],
                "content_hash": "hash_2",
                "updated_at": "2026-08-09T00:00:00"
            }
        ]

        stats = upsert_chunks(self.table, chunks)
        self.assertEqual(stats["inserted"], 2)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["unchanged"], 0)

        # Get existing hashes
        hashes = get_existing_hashes(self.table)
        self.assertEqual(hashes.get("chunk_1"), "hash_1")
        self.assertEqual(hashes.get("chunk_2"), "hash_2")

        # Category filter = 'vault' (personal notes only)
        vault_only = hybrid_search_vault(self.table, query="LanceDB", category="vault", search_type="keyword")
        self.assertEqual(len(vault_only), 1)
        self.assertEqual(vault_only[0]["id"], "chunk_1")
        self.assertEqual(vault_only[0]["category"], "vault")

        # Category filter = 'wiki' (agent synthesized wiki only)
        wiki_only = hybrid_search_vault(self.table, query="LanceDB", category="wiki", search_type="keyword")
        self.assertEqual(len(wiki_only), 1)
        self.assertEqual(wiki_only[0]["id"], "chunk_2")
        self.assertEqual(wiki_only[0]["category"], "wiki")

        # Category filter = 'all' (both included)
        all_results = hybrid_search_vault(self.table, query="LanceDB", category="all", search_type="keyword")
        self.assertEqual(len(all_results), 2)

    def test_search_empty_table(self):
        empty_dir = tempfile.mkdtemp()
        empty_table = init_knowledge_db(db_path=empty_dir, dim=4)
        results = hybrid_search_vault(empty_table, query="test", search_type="hybrid")
        self.assertEqual(results, [])
        shutil.rmtree(empty_dir, ignore_errors=True)

    def test_incremental_upsert(self):
        chunks = [
            {
                "id": "chunk_1",
                "file_path": "vault/ai.md",
                "category": "vault",
                "title": "AI Agents",
                "header_path": "Architecture",
                "tags": '["ai"]',
                "text": "Initial text content",
                "raw_content": "Initial text content",
                "vector": [0.1, 0.2, 0.3, 0.4],
                "content_hash": "hash_1",
                "updated_at": "2026-08-09T00:00:00"
            }
        ]
        upsert_chunks(self.table, chunks)

        # Same hash -> unchanged
        stats_unchanged = upsert_chunks(self.table, chunks)
        self.assertEqual(stats_unchanged["inserted"], 0)
        self.assertEqual(stats_unchanged["updated"], 0)
        self.assertEqual(stats_unchanged["unchanged"], 1)

        # Modified hash -> updated
        chunks_modified = [
            {
                "id": "chunk_1",
                "file_path": "vault/ai.md",
                "category": "vault",
                "title": "AI Agents",
                "header_path": "Architecture",
                "tags": '["ai"]',
                "text": "Updated text content",
                "raw_content": "Updated text content",
                "vector": [0.2, 0.3, 0.4, 0.5],
                "content_hash": "hash_1_modified",
                "updated_at": "2026-08-09T01:00:00"
            }
        ]
        stats_updated = upsert_chunks(self.table, chunks_modified)
        self.assertEqual(stats_updated["inserted"], 0)
        self.assertEqual(stats_updated["updated"], 1)
        self.assertEqual(stats_updated["unchanged"], 0)

    def test_prune_deleted_files(self):
        chunks = [
            {
                "id": "chunk_1",
                "file_path": "vault/active.md",
                "category": "vault",
                "title": "Active Note",
                "header_path": "General",
                "tags": '[]',
                "text": "Active note text",
                "raw_content": "Active note text",
                "vector": [0.1, 0.2, 0.3, 0.4],
                "content_hash": "h1",
                "updated_at": "2026-08-09T00:00:00"
            },
            {
                "id": "chunk_2",
                "file_path": "wiki/deleted.md",
                "category": "wiki",
                "title": "Deleted Note",
                "header_path": "General",
                "tags": '[]',
                "text": "Deleted note text",
                "raw_content": "Deleted note text",
                "vector": [0.1, 0.2, 0.3, 0.4],
                "content_hash": "h2",
                "updated_at": "2026-08-09T00:00:00"
            }
        ]
        upsert_chunks(self.table, chunks)

        pruned = prune_deleted_files(self.table, current_file_paths=["vault/active.md"])
        self.assertEqual(pruned, 1)

        # Verify only active file remains
        remaining = self.table.search().limit(10).to_list()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["file_path"], "vault/active.md")


if __name__ == "__main__":
    unittest.main()
