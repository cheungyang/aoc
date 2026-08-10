import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import importlib.util

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Import sync_projects.py script
script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "sync_projects.py"))
spec = importlib.util.spec_from_file_location("sync_projects_script", script_path)
sp_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp_module)


class TestSyncProjectsScript(unittest.TestCase):

    def test_parse_args_defaults(self):
        with patch.object(sys, "argv", ["sync_projects.py"]):
            args = sp_module.parse_args()
            self.assertIsNone(args.pkm_dir)
            self.assertIsNone(args.db_path)
            self.assertIsNone(args.projects_dir)
            self.assertFalse(args.dry_run)
            self.assertFalse(args.verbose)

    def test_parse_args_custom(self):
        with patch.object(sys, "argv", [
            "sync_projects.py",
            "--pkm-dir", "/custom/pkm",
            "--db-path", "/custom/projects.db",
            "--projects-dir", "/custom/pkm/vault/projects",
            "--dry-run",
            "--verbose"
        ]):
            args = sp_module.parse_args()
            self.assertEqual(args.pkm_dir, "/custom/pkm")
            self.assertEqual(args.db_path, "/custom/projects.db")
            self.assertEqual(args.projects_dir, "/custom/pkm/vault/projects")
            self.assertTrue(args.dry_run)
            self.assertTrue(args.verbose)

    @patch.object(sp_module, "sync_projects")
    def test_main_success(self, mock_sync):
        mock_sync.return_value = {
            "status": "success",
            "scanned_files": 10,
            "total_projects_found": 10,
            "inserted": 2,
            "updated": 1,
            "unchanged": 7,
            "pruned": 0,
            "db_path": "/path/projects.db",
            "dry_run": False
        }

        with patch.object(sys, "argv", ["sync_projects.py"]), \
             patch("builtins.print") as mock_print:
            sp_module.main()
            mock_sync.assert_called_once()

    @patch.object(sp_module, "sync_projects")
    def test_main_error_exit(self, mock_sync):
        mock_sync.side_effect = RuntimeError("Directory not found")

        with patch.object(sys, "argv", ["sync_projects.py"]), \
             patch("sys.exit") as mock_exit:
            sp_module.main()
            mock_exit.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
