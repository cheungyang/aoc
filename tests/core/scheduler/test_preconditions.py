import json
import os
import sqlite3
import tempfile
import time
import unittest

from core.scheduler import preconditions as pre
from core.scheduler.preconditions import (
    PreconditionError,
    build_precondition,
    first_changed_path,
    first_existing_file,
    is_untriaged,
)
from core.scheduler.spec import ScheduleContext


def ctx(last=None):
    return ScheduleContext(schedule_id="a:x", agent_id="a", now=time.time(), last_success_at=last)


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, content="x", mtime=None):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def abs(self, rel):
        return os.path.join(self.root, rel)


class TestHelpers(TmpDirCase):
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

    def test_is_untriaged(self):
        self.assertTrue(is_untriaged(None, "[]"))
        self.assertTrue(is_untriaged("", '["a/learn"]'))
        self.assertFalse(is_untriaged(None, '["a/learn", "p/aoc"]'))
        self.assertFalse(is_untriaged("high", "[]"))
        self.assertFalse(is_untriaged(None, "#a/x #p/y"))


class TestBuild(unittest.TestCase):
    def test_unknown_type(self):
        with self.assertRaises(PreconditionError) as cm:
            build_precondition({"type": "nope"})
        self.assertIn("unknown precondition type", str(cm.exception))

    def test_missing_type_and_non_dict(self):
        with self.assertRaises(PreconditionError):
            build_precondition({})
        with self.assertRaises(PreconditionError):
            build_precondition("always")

    def test_always_requires_reason(self):
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "always"})
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "always", "reason": "  "})
        decision = build_precondition({"type": "always", "reason": "external"}).evaluate(ctx())
        self.assertTrue(decision)
        self.assertIn("external", decision.reason)

    def test_unknown_params_rejected(self):
        with self.assertRaises(PreconditionError) as cm:
            build_precondition({"type": "files_exist", "paths": ["x"], "path": "y"})
        self.assertIn("does not take", str(cm.exception))

    def test_required_params(self):
        for bad in (
            {"type": "files_exist"},
            {"type": "files_changed_since", "paths": []},
            {"type": "file_matches", "path": "x"},
            {"type": "file_matches", "path": "x", "pattern": "("},
            {"type": "json_non_empty", "path": "x"},
            {"type": "task_list_non_empty", "filter": "someday"},
        ):
            with self.subTest(bad=bad), self.assertRaises(PreconditionError):
                build_precondition(bad)

    def test_registry_is_complete(self):
        self.assertEqual(
            set(pre.REGISTRY),
            {
                "always", "files_changed_since", "files_exist", "file_matches",
                "json_non_empty", "task_list_non_empty", "project_list_non_empty",
            },
        )


class TestEvaluate(TmpDirCase):
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

    def _tasks_db(self, rows):
        path = self.abs("tasks.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE tasks (status TEXT, priority TEXT, tags TEXT)")
        conn.executemany("INSERT INTO tasks VALUES (?, ?, ?)", rows)
        conn.commit()
        conn.close()
        return path

    def test_task_list_non_empty(self):
        db = self._tasks_db([
            ("todo", "high", "[]"),
            ("completed", None, "[]"),
        ])
        todo = build_precondition({"type": "task_list_non_empty", "db_path": db})
        untriaged = build_precondition(
            {"type": "task_list_non_empty", "filter": "untriaged", "db_path": db}
        )
        self.assertTrue(todo.evaluate(ctx()))
        self.assertFalse(untriaged.evaluate(ctx()))

        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO tasks VALUES ('todo', NULL, '[\"a/learn\"]')")
        conn.commit()
        conn.close()
        self.assertTrue(untriaged.evaluate(ctx()))

    def test_task_list_missing_db(self):
        p = build_precondition(
            {"type": "task_list_non_empty", "db_path": self.abs("none.db")}
        )
        self.assertFalse(p.evaluate(ctx()))

    def test_project_list_non_empty(self):
        path = self.abs("projects.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE projects (status TEXT)")
        conn.execute("INSERT INTO projects VALUES ('planning')")
        conn.commit()
        conn.close()
        p = build_precondition({"type": "project_list_non_empty", "db_path": path})
        self.assertFalse(p.evaluate(ctx()))
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO projects VALUES ('Executing')")
        conn.commit()
        conn.close()
        self.assertTrue(p.evaluate(ctx()))


if __name__ == "__main__":
    unittest.main()
