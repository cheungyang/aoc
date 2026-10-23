import os
import sqlite3
import unittest

from core.scheduler.preconditions import PreconditionError, build_precondition, is_untriaged
from tests.core.scheduler.preconditions import TmpDirCase, ctx


class TestStorePreconditions(TmpDirCase):
    def test_is_untriaged(self):
        self.assertTrue(is_untriaged(None, "[]"))
        self.assertTrue(is_untriaged("", '["a/learn"]'))
        self.assertFalse(is_untriaged(None, '["a/learn", "p/aoc"]'))
        self.assertFalse(is_untriaged("high", "[]"))
        self.assertFalse(is_untriaged(None, "#a/x #p/y"))

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
        # Read-only: the hand-built table is all there is, no schema was added.
        self.assertEqual([r[0] for r in conn.execute("SELECT name FROM sqlite_master")], ["tasks"])
        conn.close()
        self.assertTrue(untriaged.evaluate(ctx()))

    def test_task_list_missing_db(self):
        path = self.abs("none.db")
        p = build_precondition(
            {"type": "task_list_non_empty", "db_path": path}
        )
        decision = p.evaluate(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.reason, f"task store {path} does not exist")
        self.assertFalse(os.path.exists(path))

    def test_project_list_missing_db(self):
        path = self.abs("none.db")
        decision = build_precondition({"type": "project_list_non_empty", "db_path": path}).evaluate(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.reason, f"project store {path} does not exist")
        self.assertFalse(os.path.exists(path))

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

    def test_project_list_non_empty_rejects_unknown_status(self):
        with self.assertRaises(PreconditionError) as err:
            build_precondition({"type": "project_list_non_empty", "status": "executng"})
        self.assertIn("executing", str(err.exception))
        # Every canonical status is accepted.
        for status in ("executing", "planning", "considering", "paused", "done", "discontinued"):
            build_precondition({"type": "project_list_non_empty", "status": status})


if __name__ == "__main__":
    unittest.main()
