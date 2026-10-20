"""What a push would send, checked before it leaves.

A task branch is public the moment it lands on GitHub, so the push node checks
what it is about to send. The check must read every file the push changes, and
no others: an old line already on main would otherwise block every task, and
blocking can't unpublish it. It must fold commits GitHub doesn't have back into
the worktree, so a commit made before a fix can't carry a line out underneath
it. And when it can't look, it must say so rather than pass.

These run real git against a local bare "GitHub". The notes and the copied line
are made up.
"""
import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.util.privacy_guard import NotesMissing
from graphs.coding.utils import outgoing

NOTE = "The blue heron waited by the canal until the ferry bell rang twice.\n"
COPIED = 'CAPTION = "waited by the canal until the ferry bell rang"\n'
CLEAN = 'CAPTION = "a made-up caption for a made-up picture"\n'


def git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class OutgoingTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        # No user or system git config: a global excludes file or hook could
        # change what git reports.
        env = patch.dict(os.environ, {
            "HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp),
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
        })
        env.start()
        self.addCleanup(env.stop)

        self.pkm = self.tmp / "pkm"
        (self.pkm / "vault" / "journals").mkdir(parents=True)
        (self.pkm / "vault" / "journals" / "2026-01-02.md").write_text(NOTE, encoding="utf-8")

        # "GitHub": a bare repo whose main already has a README and an old
        # line copied from the notes, public since long before this task.
        self.remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.ws = self.tmp / "ws"
        git(self.tmp, "init", "-q", "-b", "main", str(self.ws))
        git(self.ws, "remote", "add", "origin", str(self.remote))
        self.write("README.md", "# Demo\n")
        self.write("old.py", COPIED)
        self.commit("base")
        git(self.ws, "push", "-q", "-u", "origin", "main")
        self.main_sha = git(self.ws, "rev-parse", "HEAD")
        git(self.ws, "checkout", "-q", "-b", "feat/demo/t1")

    def write(self, rel, text):
        path = self.ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message):
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", message)
        return git(self.ws, "rev-parse", "HEAD")

    def check(self, pkm=None):
        return outgoing.check_outgoing_sync(str(self.ws), "feat/demo/t1", "main", pkm or self.pkm)


class TestWhatIsRead(OutgoingTestCase):
    def test_a_new_branch_is_compared_with_where_it_left_main(self):
        self.write("new.py", CLEAN)

        result = self.check()

        # old.py holds a copied line, but it is on GitHub already: not this push's.
        self.assertEqual(result.files, ["new.py"])
        self.assertEqual(result.hits, [])
        self.assertEqual(result.error, "")

    def test_a_new_file_that_copies_a_note_is_a_hit(self):
        self.write("tests/test_demo.py", "import os\n" + COPIED)

        result = self.check()

        self.assertEqual([(path, line) for path, line, _ in result.hits], [("tests/test_demo.py", 2)])

    def test_committed_staged_and_unstaged_changes_are_all_read(self):
        self.write("committed.py", COPIED)
        self.commit("attempt 1")
        self.write("staged.py", COPIED)
        git(self.ws, "add", "staged.py")
        self.write("README.md", "# Demo\n" + COPIED)

        result = self.check()

        self.assertEqual(sorted({path for path, _, _ in result.hits}),
                         ["README.md", "committed.py", "staged.py"])

    def test_ignored_and_deleted_files_are_not_read(self):
        self.write(".gitignore", "local.py\n")
        self.write("local.py", COPIED)
        os.remove(self.ws / "README.md")

        result = self.check()

        self.assertEqual(result.files, [".gitignore"])
        self.assertEqual(result.hits, [])

    def test_once_the_branch_is_on_github_only_what_is_new_since_is_read(self):
        self.write("earlier.py", COPIED)  # pushed before the check existed
        self.commit("attempt 1")
        git(self.ws, "push", "-q", "-u", "origin", "feat/demo/t1")
        self.write("new.py", CLEAN)

        result = self.check()

        self.assertEqual(result.files, ["new.py"])
        self.assertEqual(result.hits, [])

    def test_the_guards_own_test_file_is_exempt(self):
        self.write("tests/test_repo_privacy.py", COPIED)

        result = self.check()

        self.assertEqual(result.files, [])
        self.assertEqual(result.hits, [])

    def test_with_nothing_to_compare_with_every_file_git_would_push_is_read(self):
        git(self.ws, "remote", "remove", "origin")
        self.write("new.py", CLEAN)

        result = self.check()

        self.assertEqual(result.files, ["README.md", "new.py", "old.py"])
        self.assertEqual([path for path, _, _ in result.hits], ["old.py"])


class TestFold(OutgoingTestCase):
    def test_an_unpushed_commit_is_folded_so_its_line_cannot_ride_out_under_a_fix(self):
        self.write("a.py", COPIED)
        self.commit("attempt 1, push failed")
        self.write("a.py", CLEAN)  # the fix, on top

        result = self.check()

        self.assertEqual(result.hits, [])
        # HEAD is back on what GitHub has, so the next commit replaces attempt 1
        # instead of sitting on top of it, and holds exactly what was checked.
        self.assertEqual(git(self.ws, "rev-parse", "HEAD"), self.main_sha)
        self.commit("attempt 2")  # what push does next: add everything, commit once
        self.assertEqual(git(self.ws, "rev-list", "--count", f"{self.main_sha}..HEAD"), "1")
        self.assertEqual(git(self.ws, "show", "HEAD:a.py") + "\n", CLEAN)

    def test_a_branch_that_diverged_from_github_is_left_alone(self):
        """Its push is refused as non-fast-forward, so nothing can leave through it."""
        self.write("theirs.py", CLEAN)
        self.commit("pushed from the other host")
        git(self.ws, "push", "-q", "-u", "origin", "feat/demo/t1")
        git(self.ws, "reset", "-q", "--hard", self.main_sha)
        self.write("ours.py", CLEAN)
        ours = self.commit("made here")

        result = self.check()

        self.assertEqual(result.error, "")
        self.assertEqual(git(self.ws, "rev-parse", "HEAD"), ours)


class TestWhenItCannotLook(OutgoingTestCase):
    def test_no_notes_raise_before_git_is_touched(self):
        self.write("a.py", COPIED)
        unpushed = self.commit("attempt 1")
        empty = self.tmp / "empty_pkm"
        (empty / "wiki" / "research").mkdir(parents=True)

        with self.assertRaises(NotesMissing):
            self.check(pkm=empty)
        self.assertEqual(git(self.ws, "rev-parse", "HEAD"), unpushed)

    def test_a_folder_that_is_not_a_repo_is_an_error_not_a_pass(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        (plain / "a.py").write_text(COPIED, encoding="utf-8")

        result = outgoing.check_outgoing_sync(str(plain), "feat/demo/t1", "main", self.pkm)

        self.assertNotEqual(result.error, "")
        self.assertEqual(result.hits, [])

    def test_a_checkout_on_another_branch_is_an_error_and_never_folded(self):
        """A local main with unpushed commits must not be soft-reset by a misdirected check."""
        git(self.ws, "checkout", "-q", "main")
        self.write("local.py", CLEAN)
        ahead = self.commit("local work, not pushed")

        result = self.check()

        self.assertIn("not the task branch", result.error)
        self.assertEqual(git(self.ws, "rev-parse", "HEAD"), ahead)

    def test_the_async_wrapper_returns_the_same_result(self):
        self.write("tests/test_demo.py", COPIED)

        result = asyncio.run(outgoing.check_outgoing(str(self.ws), "feat/demo/t1", "main", self.pkm))

        self.assertEqual([(path, line) for path, line, _ in result.hits], [("tests/test_demo.py", 1)])


if __name__ == "__main__":
    unittest.main()
