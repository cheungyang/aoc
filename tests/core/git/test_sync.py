"""Unit tests for Git Sync (PKM Obsidian Vault and Main Codebase)."""

import os
import shutil
import tempfile
import unittest
import subprocess
from unittest.mock import patch, MagicMock

from core.git.sync import (
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
    GitSyncResult,
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
        with patch("core.git.sync.get_unmerged_files", return_value=["conflicted_file_1.md", "conflicted_file_2.md"]), \
             patch("core.git.sync.resolve_conflicts_with_theirs", return_value=(False, ["conflicted_file_1.md", "conflicted_file_2.md"])), \
             patch("core.git.sync.run_git_cmd") as mock_git:
            
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

    # --- prefer="local": the machine you edit on keeps its own edits -----------

    def _git(self, cwd, *args, check=True):
        return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)

    def _write(self, repo, rel_path, content):
        path = os.path.join(repo, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _read(self, repo, rel_path):
        with open(os.path.join(repo, rel_path), encoding="utf-8") as f:
            return f.read()

    def _clone_other(self):
        """A second clone, standing in for the other host (the NAS)."""
        other = os.path.join(self.temp_dir, "pkm_other")
        self._git(self.temp_dir, "clone", "-q", self.remote_dir, other)
        self._git(other, "config", "user.name", "Other User")
        self._git(other, "config", "user.email", "other@example.com")
        return other

    def _commit_and_push(self, repo, message):
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", message)
        self._git(repo, "push", "-q", "origin", "main")

    def _remote_file(self, rel_path):
        """The file as the remote's main has it, or None when it is absent there.
        --git-dir, because git may be set to refuse implicit bare repositories."""
        res = self._git(self.temp_dir, f"--git-dir={self.remote_dir}", "show", f"main:{rel_path}", check=False)
        return res.stdout if res.returncode == 0 else None

    def test_prefer_local_keeps_the_local_hunk_and_merges_the_rest(self):
        """A hunk both sides changed keeps the local edit; the remote's other
        changes still arrive, and the result is pushed."""
        other = self._clone_other()
        self._write(other, "README.md", "# PKM Vault\nRemote edit\n")
        self._write(other, "remote_only.md", "From the NAS\n")
        self._commit_and_push(other, "Remote edits")

        self._write(self.pkm_local, "README.md", "# PKM Vault\nLocal edit\n")

        result = sync_pkm_vault(pkm_dir=self.pkm_local, prefer="local")
        self.assertEqual(result.status, "success", result.error)
        self.assertTrue(result.pushed)
        self.assertEqual(self._read(self.pkm_local, "README.md"), "# PKM Vault\nLocal edit\n")
        self.assertEqual(self._read(self.pkm_local, "remote_only.md"), "From the NAS\n")
        self.assertEqual(self._remote_file("README.md"), "# PKM Vault\nLocal edit\n")

    def test_prefer_local_keeps_a_local_edit_to_a_file_the_remote_deleted(self):
        """modify/delete is beyond -X ours, so resolve_conflicts_with_ours settles it.
        The non-ASCII name checks that conflicted paths reach git verbatim."""
        name = "ticktick/💪Alva.md"
        self._write(self.pkm_local, name, "- [ ] task\n")
        self._commit_and_push(self.pkm_local, "Add list")
        other = self._clone_other()
        self._git(other, "rm", "-q", name)
        self._commit_and_push(other, "Remote deletes the list")

        self._write(self.pkm_local, name, "- [ ] task\n- [ ] added on the Mac\n")

        result = sync_pkm_vault(pkm_dir=self.pkm_local, prefer="local")
        self.assertEqual(result.status, "success", result.error)
        self.assertIn("added on the Mac", self._read(self.pkm_local, name))
        self.assertIn("added on the Mac", self._remote_file(name) or "")

    def test_prefer_local_keeps_a_local_deletion_of_a_file_the_remote_edited(self):
        other = self._clone_other()
        self._write(other, "README.md", "# PKM Vault\nRemote edit\n")
        self._commit_and_push(other, "Remote edit")

        os.remove(os.path.join(self.pkm_local, "README.md"))

        result = sync_pkm_vault(pkm_dir=self.pkm_local, prefer="local")
        self.assertEqual(result.status, "success", result.error)
        self.assertFalse(os.path.exists(os.path.join(self.pkm_local, "README.md")))
        self.assertIsNone(self._remote_file("README.md"))

    def test_sync_leaves_an_unfinished_merge_alone(self):
        """A conflicted merge left in the vault (by hand, or by Obsidian Git) is not
        staged: that would commit, and then push, its conflict markers."""
        other = self._clone_other()
        self._write(other, "README.md", "# PKM Vault\nRemote edit\n")
        self._commit_and_push(other, "Remote edit")
        self._write(self.pkm_local, "README.md", "# PKM Vault\nLocal edit\n")
        self._git(self.pkm_local, "commit", "-qam", "Local edit")
        self._git(self.pkm_local, "fetch", "-q", "origin")
        self._git(self.pkm_local, "merge", "origin/main", check=False)  # stops on the conflict
        head = self._git(self.pkm_local, "rev-parse", "HEAD").stdout

        for prefer in ("remote", "local"):
            result = sync_pkm_vault(pkm_dir=self.pkm_local, prefer=prefer)
            self.assertEqual(result.status, "conflict")
            self.assertEqual(result.conflicted_files, ["README.md"])
        self.assertEqual(self._git(self.pkm_local, "rev-parse", "HEAD").stdout, head)
        self.assertIn("<<<<<<<", self._read(self.pkm_local, "README.md"))
        self.assertEqual(self._remote_file("README.md"), "# PKM Vault\nRemote edit\n")

    def test_sync_does_not_commit_on_a_detached_head(self):
        self._git(self.pkm_local, "checkout", "-q", "--detach")
        self._write(self.pkm_local, "note.md", "Written while detached\n")

        result = sync_pkm_vault(pkm_dir=self.pkm_local)
        self.assertEqual(result.status, "error")
        self.assertIn("detached", result.error)
        self.assertIn("?? note.md", self._git(self.pkm_local, "status", "--porcelain").stdout)

    def test_prefer_rejects_an_unknown_side(self):
        with self.assertRaises(ValueError):
            sync_pkm_vault(pkm_dir=self.pkm_local, prefer="newest")

    def test_sync_all_passes_prefer_to_the_vault_sync(self):
        done = GitSyncResult(target="PKM Obsidian Vault", path=self.pkm_local, status="success")
        with patch("core.git.sync.sync_pkm_vault", return_value=done) as vault_sync:
            sync_all(pkm_dir=self.pkm_local, skip_codebase=True, prefer="local")
        self.assertEqual(vault_sync.call_args.kwargs["prefer"], "local")


if __name__ == '__main__':
    unittest.main()
