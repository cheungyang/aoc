import json
import os
import time
import unittest

from core.scheduler import preconditions as pre
from core.scheduler.preconditions import build_precondition, first_changed_path, first_existing_file
from tests.core.scheduler.preconditions import TmpDirCase, ctx


class TestFileHelpers(TmpDirCase):
    def test_resolve_path_relative_and_absolute(self):
        self.assertEqual(pre.resolve_path("pkm/x", self.root), os.path.join(self.root, "pkm/x"))
        self.assertEqual(pre.resolve_path("/abs/x", self.root), "/abs/x")

    def test_first_existing_file_skips_hidden(self):
        self.write("inbox/.DS_Store")
        self.assertIsNone(first_existing_file(["inbox/**"], self.root))
        found = self.write("inbox/sub/note.md")
        self.assertEqual(first_existing_file(["inbox/**"], self.root), found)

    def test_first_changed_path_respects_since_and_suffix(self):
        old = time.time() - 1000
        self.write("vault/a.md", mtime=old)
        self.write("vault/b.txt")
        os.utime(self.abs("vault"), (old, old))
        # The .txt file is new but filtered out by suffix; the directory is old.
        self.assertIsNone(first_changed_path(["vault"], old + 10, self.root, suffixes=[".md"]))
        self.assertIsNotNone(first_changed_path(["vault"], old + 10, self.root))

    def test_directory_mtime_counts_for_deletions(self):
        old = time.time() - 1000
        self.write("vault/a.md", mtime=old)
        # Directory touched now (as a deletion would) while every file is old.
        now = time.time()
        os.utime(self.abs("vault"), (now, now))
        self.assertEqual(
            first_changed_path(["vault"], old + 10, self.root, suffixes=[".md"]),
            self.abs("vault"),
        )


class TestFilePreconditions(TmpDirCase):
    def test_files_changed_since(self):
        p = build_precondition({"type": "files_changed_since", "paths": [self.abs("d")]})
        self.assertTrue(p.evaluate(ctx(None)), "never succeeded -> run")
        old = time.time() - 1000
        self.write("d/a.md", mtime=old)
        os.utime(self.abs("d"), (old, old))
        self.assertFalse(p.evaluate(ctx(old + 10)))
        self.write("d/b.md")
        self.assertTrue(p.evaluate(ctx(old + 10)))

    def test_files_exist(self):
        p = build_precondition({"type": "files_exist", "paths": [self.abs("inbox/**")]})
        self.assertFalse(p.evaluate(ctx()))
        self.write("inbox/a.md")
        self.assertTrue(p.evaluate(ctx()))

    def test_file_matches(self):
        p = build_precondition(
            {"type": "file_matches", "path": self.abs("q.md"), "pattern": "<[A-Za-z_][^>]*>"}
        )
        self.assertFalse(p.evaluate(ctx()), "missing file")
        self.write("q.md", "# Requests\n")
        self.assertFalse(p.evaluate(ctx()))
        self.write("q.md", "<request><topic>X</topic></request>")
        self.assertTrue(p.evaluate(ctx()))

    def test_json_non_empty(self):
        p = build_precondition(
            {"type": "json_non_empty", "path": self.abs("l.json"), "keys": ["a", "b"]}
        )
        self.assertFalse(p.evaluate(ctx()))
        self.write("l.json", json.dumps({"a": [], "b": {}, "c": [1]}))
        self.assertFalse(p.evaluate(ctx()))
        self.write("l.json", json.dumps({"a": [], "b": [1]}))
        self.assertTrue(p.evaluate(ctx()))
        self.write("l.json", json.dumps([1]))
        self.assertTrue(p.evaluate(ctx()), "unexpected shape -> let the agent judge")


if __name__ == "__main__":
    unittest.main()
