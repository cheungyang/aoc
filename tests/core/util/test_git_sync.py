"""Unit tests for Git Sync (PKM Obsidian Vault and Main Codebase)."""

import os
import shutil
import tempfile
import unittest
import subprocess
from unittest.mock import patch, MagicMock

from core.util.git_sync import (
    is_git_repo,
    get_current_branch,
    get_uncommitted_files,
    get_unmerged_files,
    resolve_conflicts_with_theirs,
    sync_pkm_vault,
    sync_main_codebase,
    sync_all,
    run_git_cmd,
    GitSyncConflictError,
    GitSyncResult
)


class TestGitSync(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.remote_dir = os.path.join(self.temp_dir, "remote.git")
        self.pkm_local = os.path.join(self.temp_dir, "pkm_local")
        self.codebase_local = os.path.join(self.temp_dir, "codebase_local")

        # Initialize bare remote repo
        subprocess.run(["git", "init", "--bare", self.remote_dir], check=True, capture_output=True)

        # Initialize PKM local repo with initial commit and push to remote
        os.makedirs(self.pkm_local, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.pkm_local, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.pkm_local, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.pkm_local, check=True, capture_output=True)
        
        with open(os.path.join(self.pkm_local, "README.md"), "w") as f:
            f.write("# PKM Vault\nInitial line\n")
        
        subprocess.run(["git", "add", "-A"], cwd=self.pkm_local, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.pkm_local, check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", self.remote_dir], cwd=self.pkm_local, check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=self.pkm_local, check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_is_git_repo_and_branch(self):
        self.assertTrue(is_git_repo(self.pkm_local))
        self.assertFalse(is_git_repo(os.path.join(self.temp_dir, "nonexistent")))
        self.assertEqual(get_current_branch(self.pkm_local), "main")

    def test_pkm_vault_sync_clean(self):
        """Test sync when PKM vault has no changes and remote is up to date."""
        result = sync_pkm_vault(pkm_dir=self.pkm_local)
        self.assertTrue(result.is_success)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.committed_files), 0)

    def test_pkm_vault_sync_with_new_files_and_commit(self):
        """Test sync when PKM vault has new and modified files."""
        new_note = os.path.join(self.pkm_local, "vault", "journals", "2026-08-09.md")
        os.makedirs(os.path.dirname(new_note), exist_ok=True)
        with open(new_note, "w") as f:
            f.write("Today was productive.\n")

        with open(os.path.join(self.pkm_local, "README.md"), "a") as f:
            f.write("Updated line.\n")

        result = sync_pkm_vault(pkm_dir=self.pkm_local)
        self.assertTrue(result.is_success)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.committed_files), 2)
        self.assertTrue(result.pushed)

        # Verify git status is clean now
        status_res = subprocess.run(["git", "status", "--porcelain"], cwd=self.pkm_local, capture_output=True, text=True)
        self.assertEqual(status_res.stdout.strip(), "")

    def test_pkm_vault_sync_conflict_auto_resolved_with_theirs(self):
        """Test merge conflict in PKM vault where remote changes are favored automatically."""
        # Create a second clone to push a change to remote
        other_clone = os.path.join(self.temp_dir, "pkm_other")
        subprocess.run(["git", "clone", self.remote_dir, other_clone], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Other User"], cwd=other_clone, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "other@example.com"], cwd=other_clone, check=True, capture_output=True)

        other_file = os.path.join(other_clone, "note.md")
        with open(other_file, "w") as f:
            f.write("Remote line 1\nRemote line 2\n")
        subprocess.run(["git", "add", "note.md"], cwd=other_clone, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Remote edit"], cwd=other_clone, check=True, capture_output=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=other_clone, check=True, capture_output=True)

        # In local pkm, write conflicting edit
        local_file = os.path.join(self.pkm_local, "note.md")
        with open(local_file, "w") as f:
            f.write("Local line 1\nLocal line 2\n")

        result = sync_pkm_vault(pkm_dir=self.pkm_local)
        self.assertTrue(result.is_success)
        self.assertEqual(result.status, "success")

        # Verify content took remote version
        with open(local_file, "r") as f:
            content = f.read()
        self.assertIn("Remote line 1", content)

    def test_main_codebase_sync_pull_only(self):
        """Test Main Codebase sync pulls updates without committing or pushing local changes."""
        # Setup local codebase clone
        subprocess.run(["git", "clone", self.remote_dir, self.codebase_local], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Dev"], cwd=self.codebase_local, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=self.codebase_local, check=True, capture_output=True)

        # Initially up to date
        res = sync_main_codebase(codebase_dir=self.codebase_local)
        self.assertTrue(res.is_success)
        self.assertEqual(res.status, "up_to_date")

        # Push an update to remote from another clone
        other_clone = os.path.join(self.temp_dir, "dev_other")
        subprocess.run(["git", "clone", self.remote_dir, other_clone], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Dev 2"], cwd=other_clone, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "dev2@example.com"], cwd=other_clone, check=True, capture_output=True)
        with open(os.path.join(other_clone, "code.py"), "w") as f:
            f.write("print('new code')\n")
        subprocess.run(["git", "add", "code.py"], cwd=other_clone, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add code.py"], cwd=other_clone, check=True, capture_output=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=other_clone, check=True, capture_output=True)

        # Sync main codebase
        res2 = sync_main_codebase(codebase_dir=self.codebase_local)
        self.assertTrue(res2.is_success)
        self.assertTrue(res2.pulled)
        self.assertFalse(res2.pushed)
        self.assertTrue(os.path.exists(os.path.join(self.codebase_local, "code.py")))

    def test_sync_all_orchestration(self):
        """Test sync_all runs both PKM and codebase sync and generates summary."""
        subprocess.run(["git", "clone", self.remote_dir, self.codebase_local], check=True, capture_output=True)

        output = sync_all(pkm_dir=self.pkm_local, codebase_dir=self.codebase_local)
        self.assertTrue(output["success"])
        self.assertIn("=== Git Sync Summary ===", output["summary"])
        self.assertIn("PKM Obsidian Vault", output["summary"])
        self.assertIn("Main Codebase", output["summary"])
        self.assertIn("=== Sync Complete ===", output["summary"])

    def test_unresolvable_conflict_error_reporting(self):
        """Test that unresolvable conflicts abort merge and produce structured error message with files."""
        # Create a mock failure in resolve_conflicts_with_theirs
        with patch("core.util.git_sync.get_unmerged_files", return_value=["conflicted_file_1.md", "conflicted_file_2.md"]), \
             patch("core.util.git_sync.resolve_conflicts_with_theirs", return_value=(False, ["conflicted_file_1.md", "conflicted_file_2.md"])), \
             patch("core.util.git_sync.run_git_cmd") as mock_git:
            
            # Setup mock git return for merge failing
            def git_side_effect(args, cwd=None, **kwargs):
                cmd_res = MagicMock()
                if "rev-parse" in args and "--is-inside-work-tree" in args:
                    cmd_res.returncode = 0
                    cmd_res.stdout = "true"
                elif "remote" in args:
                    cmd_res.returncode = 0
                    cmd_res.stdout = "origin"
                elif "branch" in args or ("rev-parse" in args and "HEAD" in args):
                    cmd_res.returncode = 0
                    cmd_res.stdout = "main"
                elif "fetch" in args:
                    cmd_res.returncode = 0
                    cmd_res.stdout = ""
                elif "merge" in args:
                    cmd_res.returncode = 1
                    cmd_res.stderr = "Automatic merge failed; fix conflicts and then commit the result."
                else:
                    cmd_res.returncode = 0
                    cmd_res.stdout = ""
                    cmd_res.stderr = ""
                return cmd_res

            mock_git.side_effect = git_side_effect

            result = sync_pkm_vault(pkm_dir=self.pkm_local)
            self.assertFalse(result.is_success)
            self.assertEqual(result.status, "conflict")
            self.assertIn("conflicted_file_1.md", result.conflicted_files)
            self.assertIn("conflicted_file_2.md", result.conflicted_files)
            self.assertIn("conflicted_file_1.md", result.error)


if __name__ == '__main__':
    unittest.main()
