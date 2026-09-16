import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import importlib.util

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Import sync_knowledge.py
script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "sync_knowledge.py"))
spec = importlib.util.spec_from_file_location("sync_knowledge_script", script_path)
sk_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sk_module)


class TestSyncKnowledgeScript(unittest.TestCase):

    def test_parse_args_defaults(self):
        with patch.object(sys, "argv", ["sync_knowledge.py"]):
            args = sk_module.parse_args()
            self.assertIsNone(args.pkm_dir)
            self.assertIsNone(args.db_path)
            self.assertFalse(args.dry_run)
            self.assertFalse(args.force_reindex)
            self.assertFalse(args.verbose)

    def test_parse_args_custom(self):
        with patch.object(sys, "argv", [
            "sync_knowledge.py",
            "--pkm-dir", "/custom/pkm",
            "--db-path", "/custom/.lancedb",
            "--dry-run",
            "--force-reindex",
            "--verbose"
        ]):
            args = sk_module.parse_args()
            self.assertEqual(args.pkm_dir, "/custom/pkm")
            self.assertEqual(args.db_path, "/custom/.lancedb")
            self.assertTrue(args.dry_run)
            self.assertTrue(args.force_reindex)
            self.assertTrue(args.verbose)

    @patch.object(sk_module, "get_knowledge_db_path")
    @patch.object(sk_module, "get_pkm_dir")
    @patch.object(sk_module, "sync_knowledge")
    def test_main_success(self, mock_sync, mock_get_pkm_dir, mock_get_db_path):
        """With no CLI paths, `main` must resolve them through the project's own
        helpers and report the counts it got back.

        Both halves have failed silently before: a wrong default sends the sync
        at an empty directory, and a summary line that quietly drops a count
        makes a no-op sync look like a successful one.
        """
        mock_get_pkm_dir.return_value = "/fake/pkm"
        mock_get_db_path.return_value = "/fake/pkm/.lancedb"
        mock_sync.return_value = {
            "scanned_files": 10,
            "total_chunks": 25,
            "chunks_to_embed": 5,
            "inserted": 5,
            "updated": 0,
            "unchanged": 20,
            "pruned": 0,
            "dry_run": False
        }

        with patch.object(sys, "argv", ["sync_knowledge.py"]), \
             patch("builtins.print") as mock_print:
            sk_module.main()

        mock_sync.assert_called_once_with(
            pkm_dir="/fake/pkm",
            db_path="/fake/pkm/.lancedb",
            dry_run=False,
            force_reindex=False
        )

        output = "\n".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("/fake/pkm", output)
        self.assertIn("/fake/pkm/.lancedb", output)
        self.assertNotIn("DRY-RUN", output)
        self.assertRegex(output, r"Scanned files:\s+10")
        self.assertRegex(output, r"Total chunks in vault:\s+25")
        self.assertRegex(output, r"Chunks embedded:\s+5")
        self.assertRegex(output, r"Inserted into LanceDB:\s+5")
        self.assertRegex(output, r"Updated in LanceDB:\s+0")
        self.assertRegex(output, r"Unchanged in LanceDB:\s+20")
        self.assertRegex(output, r"Pruned deleted files:\s+0")
        self.assertIn("=== Sync Complete ===", output)

    @patch.object(sk_module, "sync_knowledge")
    def test_main_dry_run(self, mock_sync):
        """`--dry-run` must reach the sync, and CLI paths must arrive expanded.

        A `~` handed through unexpanded creates a literal `./~` directory next
        to the repo instead of touching the vault.
        """
        mock_sync.return_value = {
            "scanned_files": 1,
            "total_chunks": 1,
            "chunks_to_embed": 1,
            "inserted": 1,
            "updated": 0,
            "unchanged": 0,
            "pruned": 0,
            "dry_run": True
        }

        with patch.object(sys, "argv", [
            "sync_knowledge.py",
            "--dry-run",
            "--pkm-dir", "~/pkm-under-test",
            "--db-path", "~/pkm-under-test/.lancedb",
        ]), patch("builtins.print") as mock_print:
            sk_module.main()

        expected_pkm = os.path.join(os.path.expanduser("~"), "pkm-under-test")
        mock_sync.assert_called_once_with(
            pkm_dir=expected_pkm,
            db_path=os.path.join(expected_pkm, ".lancedb"),
            dry_run=True,
            force_reindex=False
        )

        output = "\n".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("DRY-RUN", output)

    @patch.object(sk_module, "sync_knowledge")
    def test_main_error_exit(self, mock_sync):
        mock_sync.side_effect = RuntimeError("LanceDB connection error")

        with patch.object(sys, "argv", ["sync_knowledge.py"]), \
             patch("sys.exit") as mock_exit:
            sk_module.main()
            mock_exit.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
