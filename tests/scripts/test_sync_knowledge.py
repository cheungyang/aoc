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

    @patch.object(sk_module, "sync_knowledge")
    def test_main_success(self, mock_sync):
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
            mock_sync.assert_called_once()

    @patch.object(sk_module, "sync_knowledge")
    def test_main_dry_run(self, mock_sync):
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

        with patch.object(sys, "argv", ["sync_knowledge.py", "--dry-run"]), \
             patch("builtins.print") as mock_print:
            sk_module.main()
            mock_sync.assert_called_once_with(
                pkm_dir=mock_sync.call_args[1]["pkm_dir"],
                db_path=mock_sync.call_args[1]["db_path"],
                dry_run=True,
                force_reindex=False
            )

    @patch.object(sk_module, "sync_knowledge")
    def test_main_error_exit(self, mock_sync):
        mock_sync.side_effect = RuntimeError("LanceDB connection error")

        with patch.object(sys, "argv", ["sync_knowledge.py"]), \
             patch("sys.exit") as mock_exit:
            sk_module.main()
            mock_exit.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
