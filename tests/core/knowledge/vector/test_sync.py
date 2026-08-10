import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from core.config import Config
from core.knowledge.sync import (
    scan_knowledge_markdown_files,
    sync_knowledge,
    get_pkm_dir,
)
from core.knowledge.db import init_knowledge_db, get_db_connection


class TestKnowledgeSync(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.pkm_dir = os.path.join(self.test_dir, "pkm")
        self.db_path = os.path.join(self.test_dir, ".lancedb")

        os.makedirs(os.path.join(self.pkm_dir, "vault", "projects"), exist_ok=True)
        os.makedirs(os.path.join(self.pkm_dir, "vault", "assets"), exist_ok=True)
        os.makedirs(os.path.join(self.pkm_dir, "vault", ".obsidian"), exist_ok=True)
        os.makedirs(os.path.join(self.pkm_dir, "wiki", "concepts"), exist_ok=True)
        os.makedirs(os.path.join(self.pkm_dir, "ticktick"), exist_ok=True)
        os.makedirs(os.path.join(self.pkm_dir, "inbox"), exist_ok=True)
        os.makedirs(os.path.join(self.pkm_dir, "agents"), exist_ok=True)
        os.makedirs(os.path.join(self.pkm_dir, "backup"), exist_ok=True)

        Config().pkm_dir = self.pkm_dir
        Config().knowledge_db_path = self.db_path
        Config().embedding_dimensions = 4

    def tearDown(self):
        Config().reset()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_pkm_dir(self):
        self.assertEqual(get_pkm_dir(), self.pkm_dir)

    def test_scan_knowledge_markdown_files_scopes(self):
        # 1. Included notes
        vault_note = os.path.join(self.pkm_dir, "vault", "note1.md")
        vault_proj = os.path.join(self.pkm_dir, "vault", "projects", "proj.md")
        wiki_note = os.path.join(self.pkm_dir, "wiki", "concepts", "ai.md")

        # 2. Excluded notes (ticktick, inbox, agents, backup, assets, hidden)
        ticktick_note = os.path.join(self.pkm_dir, "ticktick", "task.md")
        inbox_note = os.path.join(self.pkm_dir, "inbox", "unread.md")
        agent_mem = os.path.join(self.pkm_dir, "agents", "memory.md")
        hidden_file = os.path.join(self.pkm_dir, "vault", ".hidden.md")
        asset_file = os.path.join(self.pkm_dir, "vault", "assets", "image_note.md")
        backup_file = os.path.join(self.pkm_dir, "backup", "backup_note.md")

        for p in [vault_note, vault_proj, wiki_note, ticktick_note, inbox_note, agent_mem, hidden_file, asset_file, backup_file]:
            with open(p, "w") as f:
                f.write("# Content")

        scanned = scan_knowledge_markdown_files(self.pkm_dir)
        rel_paths = {s[1]: s[2] for s in scanned}

        # Included
        self.assertIn("vault/note1.md", rel_paths)
        self.assertEqual(rel_paths["vault/note1.md"], "vault")
        self.assertIn("vault/projects/proj.md", rel_paths)
        self.assertEqual(rel_paths["vault/projects/proj.md"], "vault")
        self.assertIn("wiki/concepts/ai.md", rel_paths)
        self.assertEqual(rel_paths["wiki/concepts/ai.md"], "wiki")

        # Excluded
        self.assertNotIn("ticktick/task.md", rel_paths)
        self.assertNotIn("inbox/unread.md", rel_paths)
        self.assertNotIn("agents/memory.md", rel_paths)
        self.assertNotIn("vault/.hidden.md", rel_paths)
        self.assertNotIn("vault/assets/image_note.md", rel_paths)
        self.assertNotIn("backup/backup_note.md", rel_paths)

    def test_sync_knowledge_missing_pkm_dir(self):
        with self.assertRaises(FileNotFoundError):
            sync_knowledge(pkm_dir="/nonexistent/path/for/pkm")

    def test_sync_knowledge_dry_run(self):
        note_file = os.path.join(self.pkm_dir, "vault", "note.md")
        with open(note_file, "w") as f:
            f.write("# Dry Run Note\nThis should not be saved in dry run.")

        results = sync_knowledge(
            pkm_dir=self.pkm_dir,
            db_path=self.db_path,
            dry_run=True
        )

        self.assertEqual(results["scanned_files"], 1)
        self.assertEqual(results["vault_files"], 1)
        self.assertEqual(results["wiki_files"], 0)
        self.assertEqual(results["total_chunks"], 1)
        self.assertEqual(results["chunks_to_embed"], 1)
        self.assertTrue(results["dry_run"])

        # LanceDB table should still be empty
        conn = get_db_connection(self.db_path)
        table = init_knowledge_db(conn=conn, db_path=self.db_path, dim=4)
        self.assertEqual(table.count_rows(), 0)

    def test_sync_knowledge_incremental_and_pruning(self):
        # 1. Initial sync with 1 vault note and 1 wiki note
        file1 = os.path.join(self.pkm_dir, "vault", "file1.md")
        file2 = os.path.join(self.pkm_dir, "wiki", "file2.md")
        with open(file1, "w") as f:
            f.write("# File 1\nPersonal core note.")
        with open(file2, "w") as f:
            f.write("# File 2\nAgent wiki knowledge.")

        res1 = sync_knowledge(pkm_dir=self.pkm_dir, db_path=self.db_path)
        self.assertEqual(res1["scanned_files"], 2)
        self.assertEqual(res1["vault_files"], 1)
        self.assertEqual(res1["wiki_files"], 1)
        self.assertEqual(res1["inserted"], 2)
        self.assertEqual(res1["unchanged"], 0)
        self.assertEqual(res1["pruned"], 0)

        # 2. Second sync without modifications -> all unchanged
        res2 = sync_knowledge(pkm_dir=self.pkm_dir, db_path=self.db_path)
        self.assertEqual(res2["scanned_files"], 2)
        self.assertEqual(res2["inserted"], 0)
        self.assertEqual(res2["updated"], 0)
        self.assertEqual(res2["unchanged"], 2)
        self.assertEqual(res2["chunks_to_embed"], 0)

        # 3. Modify file1, delete file2, add file3
        with open(file1, "w") as f:
            f.write("# File 1\nModified content for personal note.")
        os.remove(file2)
        file3 = os.path.join(self.pkm_dir, "vault", "file3.md")
        with open(file3, "w") as f:
            f.write("# File 3\nNew personal note.")

        res3 = sync_knowledge(pkm_dir=self.pkm_dir, db_path=self.db_path)
        self.assertEqual(res3["scanned_files"], 2)
        self.assertEqual(res3["inserted"], 1)  # file3
        self.assertEqual(res3["updated"], 1)   # file1
        self.assertEqual(res3["pruned"], 1)    # file2 pruned

    def test_sync_knowledge_force_reindex(self):
        file1 = os.path.join(self.pkm_dir, "vault", "file1.md")
        with open(file1, "w") as f:
            f.write("# File 1\nSome text.")

        sync_knowledge(pkm_dir=self.pkm_dir, db_path=self.db_path)

        # Force re-index should re-embed all chunks
        res_forced = sync_knowledge(pkm_dir=self.pkm_dir, db_path=self.db_path, force_reindex=True)
        self.assertEqual(res_forced["chunks_to_embed"], 1)


if __name__ == "__main__":
    unittest.main()
