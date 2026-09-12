import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import tempfile
import importlib.util

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Import regenerate_lancedb.py
script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "regenerate_lancedb.py"))
spec = importlib.util.spec_from_file_location("regenerate_lancedb_script", script_path)
rl_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl_module)


class TestRegenerateLanceDBScript(unittest.TestCase):

    def test_parse_args_defaults(self):
        with patch.object(sys, "argv", ["regenerate_lancedb.py"]):
            args = rl_module.parse_args()
            self.assertIsNone(args.pkm_dir)
            self.assertIsNone(args.db_path)
            self.assertTrue(args.clean)
            self.assertFalse(args.skip_embedding)
            self.assertFalse(args.dry_run)
            self.assertEqual(args.batch_size, 64)
            self.assertEqual(args.test_query, "project")

    def test_parse_args_custom(self):
        with patch.object(sys, "argv", [
            "regenerate_lancedb.py",
            "--pkm-dir", "/custom/pkm",
            "--db-path", "/custom/.lancedb",
            "--no-clean",
            "--skip-embedding",
            "--model", "gemini-embedding-001",
            "--batch-size", "128",
            "--write-batch-size", "250",
            "--test-query", "test search",
            "--dry-run",
            "--verbose"
        ]):
            args = rl_module.parse_args()
            self.assertEqual(args.pkm_dir, "/custom/pkm")
            self.assertEqual(args.db_path, "/custom/.lancedb")
            self.assertFalse(args.clean)
            self.assertTrue(args.skip_embedding)
            self.assertEqual(args.model, "gemini-embedding-001")
            self.assertEqual(args.batch_size, 128)
            self.assertEqual(args.write_batch_size, 250)
            self.assertEqual(args.test_query, "test search")
            self.assertTrue(args.dry_run)
            self.assertTrue(args.verbose)

    def test_resolve_paths_explicit(self):
        pkm, db = rl_module.resolve_paths("/custom/pkm", "/custom/lancedb")
        self.assertEqual(pkm, os.path.abspath("/custom/pkm"))
        self.assertEqual(db, os.path.abspath("/custom/lancedb"))

    @patch.object(rl_module, "regenerate_lancedb")
    def test_main_success(self, mock_regen):
        mock_regen.return_value = {
            "pkm_dir": "/mock/pkm",
            "db_path": "/mock/.lancedb",
            "scanned_files": 10,
            "total_chunks": 30,
            "rows_in_db": 30,
            "verified": True,
            "dry_run": False
        }

        with patch.object(sys, "argv", ["regenerate_lancedb.py"]), \
             patch("builtins.print") as mock_print:
            rl_module.main()
            mock_regen.assert_called_once()

    @patch.object(rl_module, "regenerate_lancedb")
    def test_main_error_exit(self, mock_regen):
        mock_regen.side_effect = RuntimeError("PKM directory not found")

        with patch.object(sys, "argv", ["regenerate_lancedb.py"]), \
             patch("sys.exit") as mock_exit:
            rl_module.main()
            mock_exit.assert_called_once_with(1)

    def test_e2e_regenerate_temporary_vault(self):
        """End-to-end integration test creating notes and verifying LanceDB generation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkm_dir = os.path.join(tmpdir, "pkm")
            db_path = os.path.join(tmpdir, "pkm", ".lancedb")
            vault_dir = os.path.join(pkm_dir, "vault")
            wiki_dir = os.path.join(pkm_dir, "wiki")
            os.makedirs(vault_dir, exist_ok=True)
            os.makedirs(wiki_dir, exist_ok=True)

            # Create sample vault note
            with open(os.path.join(vault_dir, "personal_goal.md"), "w") as f:
                f.write("---\ntitle: Personal Goals\ntags: [life, roadmap]\n---\n# Life Goals\nFocus on AI pair programming and LanceDB vector databases.\n")

            # Create sample wiki note
            with open(os.path.join(wiki_dir, "lancedb_architecture.md"), "w") as f:
                f.write("---\ntitle: LanceDB Architecture\ntags: [tech, vectors]\n---\n# LanceDB Architecture\nLanceDB provides hybrid search with Tantivy BM25 full-text indexing.\n")

            # Run dry-run first
            dry_results = rl_module.regenerate_lancedb(
                pkm_dir=pkm_dir,
                db_path=db_path,
                dry_run=True,
                skip_embedding=True
            )
            self.assertTrue(dry_results["dry_run"])
            self.assertEqual(dry_results["scanned_files"], 2)
            self.assertEqual(dry_results["vault_files"], 1)
            self.assertEqual(dry_results["wiki_files"], 1)
            self.assertTrue(dry_results["total_chunks"] >= 2)

            # Run full regeneration with skip_embedding (offline deterministic)
            results = rl_module.regenerate_lancedb(
                pkm_dir=pkm_dir,
                db_path=db_path,
                clean=True,
                dry_run=False,
                skip_embedding=True,
                test_query="LanceDB"
            )
            self.assertFalse(results["dry_run"])
            self.assertEqual(results["scanned_files"], 2)
            self.assertEqual(results["rows_in_db"], results["total_chunks"])
            self.assertTrue(results["verified"])
            self.assertTrue(os.path.exists(db_path))


if __name__ == "__main__":
    unittest.main()
