import os
import shutil
import tempfile
import json
import unittest
from unittest.mock import patch, MagicMock

from tools.vault_search import vault_search
from core.knowledge.vector.db import (
    get_db_connection,
    init_knowledge_db,
    upsert_chunks,
)
from core.util.config import Config


class TestVaultSearchTool(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, ".lancedb")
        Config().knowledge_db_path = self.db_path
        Config().embedding_dimensions = 4

        conn = get_db_connection(self.db_path)
        self.table = init_knowledge_db(conn=conn, db_path=self.db_path, dim=4)

        # Populate test knowledge chunks
        chunks = [
            {
                "id": "c1",
                "file_path": "vault/design.md",
                "category": "vault",
                "title": "Design Docs",
                "header_path": "Architecture",
                "tags": '["arch", "backend"]',
                "text": "Title: Design Docs\nCategory: vault\n\nLanceDB acts as a vector database for semantic search.",
                "raw_content": "LanceDB acts as a vector database for semantic search.",
                "vector": [0.1, 0.2, 0.3, 0.4],
                "content_hash": "h1",
                "updated_at": "2026-08-09T00:00:00"
            },
            {
                "id": "c2",
                "file_path": "wiki/ai_synthesis.md",
                "category": "wiki",
                "title": "AI Synthesis",
                "header_path": "Agent Wiki",
                "tags": '["wiki"]',
                "text": "Title: AI Synthesis\nCategory: wiki\n\nSynthesized knowledge on LanceDB vectors.",
                "raw_content": "Synthesized knowledge on LanceDB vectors.",
                "vector": [0.1, 0.2, 0.3, 0.4],
                "content_hash": "h2",
                "updated_at": "2026-08-09T00:00:00"
            }
        ]
        upsert_chunks(self.table, chunks)

    def tearDown(self):
        Config().reset()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_missing_agent_id(self):
        res = vault_search.invoke({"agent_id": "", "query": "LanceDB"})
        self.assertIn("Error: agent_id is required", res)

    def test_missing_query(self):
        res = vault_search.invoke({"agent_id": "test-agent", "query": ""})
        self.assertIn("Error: 'query' parameter is required", res)

    def test_successful_hybrid_search_all_categories(self):
        res = vault_search.invoke({
            "agent_id": "test-agent",
            "query": "LanceDB",
            "search_type": "hybrid",
            "category": "all",
            "limit": 5
        })
        self.assertIn("Found 2 result(s)", res)
        self.assertIn("[VAULT] Design Docs", res)
        self.assertIn("[WIKI] AI Synthesis", res)

    def test_category_filter_vault_only(self):
        res = vault_search.invoke({
            "agent_id": "test-agent",
            "query": "LanceDB",
            "category": "vault",
            "limit": 5
        })
        self.assertIn("Found 1 result(s)", res)
        self.assertIn("[VAULT] Design Docs", res)
        self.assertNotIn("[WIKI]", res)

    def test_category_filter_wiki_only(self):
        res = vault_search.invoke({
            "agent_id": "test-agent",
            "query": "LanceDB",
            "category": "wiki",
            "limit": 5
        })
        self.assertIn("Found 1 result(s)", res)
        self.assertIn("[WIKI] AI Synthesis", res)
        self.assertNotIn("[VAULT]", res)

    def test_semantic_search_mode(self):
        res = vault_search.invoke({
            "agent_id": "test-agent",
            "query": "vector database",
            "search_type": "semantic",
            "limit": 3
        })
        self.assertIn("Found 2 result(s)", res)

    def test_keyword_search_mode(self):
        res = vault_search.invoke({
            "agent_id": "test-agent",
            "query": "LanceDB",
            "search_type": "keyword",
            "limit": 3
        })
        self.assertIn("Found 2 result(s)", res)

    def test_path_filter(self):
        res = vault_search.invoke({
            "agent_id": "test-agent",
            "query": "LanceDB",
            "path_filter": "nonexistent_folder",
            "limit": 3
        })
        self.assertIn("No matching notes found", res)

    def test_empty_vault_message(self):
        empty_dir = tempfile.mkdtemp()
        Config().knowledge_db_path = empty_dir
        # Re-init empty
        init_knowledge_db(db_path=empty_dir, dim=4)

        res = vault_search.invoke({
            "agent_id": "test-agent",
            "query": "anything"
        })
        self.assertIn("Vault index is empty", res)
        shutil.rmtree(empty_dir, ignore_errors=True)

    @patch("core.loaders.tools_loader.ToolsLoader._merge_tool_permissions")
    def test_permission_denied(self, mock_perms):
        mock_perms.return_value = {"vault_search": ["sync"]}
        res = vault_search.invoke({
            "agent_id": "restricted-agent",
            "action": "search",
            "query": "test"
        })
        self.assertIn("Error: Agent restricted-agent does not have permission", res)

    @patch("core.loaders.tools_loader.ToolsLoader._merge_tool_permissions")
    def test_permission_allowed_wildcard(self, mock_perms):
        mock_perms.return_value = {"vault_search": ["*"]}
        res = vault_search.invoke({
            "agent_id": "allowed-agent",
            "action": "search",
            "query": "LanceDB"
        })
        self.assertIn("Found 2 result(s)", res)

    def test_sync_action(self):
        pkm_dir = os.path.join(self.test_dir, "pkm")
        vault_dir = os.path.join(pkm_dir, "vault")
        os.makedirs(vault_dir, exist_ok=True)
        note_file = os.path.join(vault_dir, "sample.md")
        with open(note_file, "w") as f:
            f.write("# Sample Title\nSample content text for indexing.")

        Config().pkm_dir = pkm_dir
        res = vault_search.invoke({
            "agent_id": "test-agent",
            "action": "sync"
        })
        self.assertIn("scanned_files", res)
        self.assertIn("total_chunks", res)

    @patch("tools.vault_search.hybrid_search_vault")
    def test_search_exception_handling(self, mock_search):
        mock_search.side_effect = RuntimeError("LanceDB connection failed")
        res = vault_search.invoke({
            "agent_id": "test-agent",
            "query": "test"
        })
        self.assertIn("Error executing vault_search: LanceDB connection failed", res)


if __name__ == "__main__":
    unittest.main()
