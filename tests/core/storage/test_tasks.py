"""tasks.db (core/storage/tasks.py): its schema and TasksStore.

The sync's upsert, query and prune basics are covered by
tests/core/knowledge/tasks/test_task_sync.py, and the tool's use by
tests/tools/test_task_query.py; this covers the rest of the store.
"""
import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from core.storage import base
from core.storage.tasks import TasksStore
from core.config.settings import Config

COLUMNS = [
    "id", "title", "raw_title", "status", "priority", "priority_rank", "tags", "scheduled_date", "due_date",
    "completed_date", "dropped_date", "created_date", "source", "source_type", "line_number", "raw_line",
    "content_hash", "updated_date",
]
COMPACT = ["id", "title", "status", "priority", "due_date", "scheduled_date", "tags", "source"]


def _task(tid, title="Task", status="todo", **fields):
    return {"id": tid, "title": title, "status": status, "source": "vault/Inbox.md", "source_type": "vault",
            "line_number": 1, **fields}


def _has_fts5():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


FTS5 = _has_fts5()


class TasksCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = TasksStore(os.path.join(tmp.name, "pkm", "tasks.db"))

    def names(self, kind):
        with self.store.read_only() as conn:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))}


class TestSchema(TasksCase):

    def test_init_creates_the_table_its_indexes_and_the_search_index_once(self):
        self.store.init()
        self.store.init()
        with self.store.connect() as conn:
            self.store.init(conn)
            self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(tasks)")], COLUMNS)
        self.assertEqual({n for n in self.names("index") if not n.startswith("sqlite_")}, {
            "idx_tasks_status", "idx_tasks_priority_rank", "idx_tasks_due_date", "idx_tasks_scheduled_date",
            "idx_tasks_source", "idx_tasks_updated_date",
        })
        self.assertEqual("tasks_fts" in self.names("table"), FTS5)
        self.assertEqual(self.names("trigger"), {"tasks_ai", "tasks_ad", "tasks_au"} if FTS5 else set())

    def test_without_fts5_the_table_is_still_created(self):
        real = base.load_schema

        def no_fts5(name):
            return real(name).replace("USING fts5(", "USING no_such_module(") if name == "tasks_fts" else real(name)

        with patch.object(base, "load_schema", side_effect=no_fts5), patch("builtins.print") as printed:
            self.store.init()
        self.assertIn("tasks_fts.sql", printed.call_args[0][0])
        self.assertEqual((self.names("trigger"), "tasks_fts" in self.names("table")), (set(), False))
        self.store.upsert_tasks([_task("t1")])
        self.assertEqual(self.store.get_task("t1")["title"], "Task")

    def test_connecting_leaves_a_table_built_by_someone_else_as_it_is(self):
        with self.store.connect() as conn:
            conn.execute("CREATE TABLE tasks (status TEXT, priority TEXT, tags TEXT)")  # as the preconditions' tests do
        with self.store.connect() as conn:
            self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(tasks)")], ["status", "priority", "tags"])

    def test_the_default_path_is_the_config_one(self):
        with Config().overriding(tasks_db_path="/tmp/elsewhere/tasks.db"):
            self.assertEqual(TasksStore().path, "/tmp/elsewhere/tasks.db")


class TestTasksStore(TasksCase):

    def setUp(self):
        super().setUp()
        self.store.init()

    def test_upsert_keeps_the_first_of_a_repeated_id_and_counts_every_row_scanned(self):
        stats = self.store.upsert_tasks([_task("t1", "First"), _task("t1", "Second"), _task("t2")])
        self.assertEqual(stats, {"inserted": 2, "updated": 0, "unchanged": 0, "total_scanned": 3})
        self.assertEqual(self.store.get_task("t1")["title"], "First")
        self.assertEqual(self.store.upsert_tasks([_task("t1", "First"), _task("t2")]),
                         {"inserted": 0, "updated": 0, "unchanged": 2, "total_scanned": 2})

    def test_given_a_connection_upsert_and_prune_share_its_transaction(self):
        self.store.upsert_tasks([_task("t1")])
        with self.assertRaises(RuntimeError):
            with self.store.connect() as conn:
                self.store.upsert_tasks([_task("t2")], conn=conn)
                self.assertEqual(self.store.prune_deleted(["t2"], conn=conn), 1)
                raise RuntimeError("boom")
        self.assertEqual([t["id"] for t in self.store.search(status="all")], ["t1"])
        self.assertEqual(self.store.prune_deleted(["t1"]), 0)

    def test_search_over_a_read_only_connection(self):
        self.store.upsert_tasks([_task(f"t{i:02d}", priority_rank=12 - i, tags=["p/aoc"]) for i in range(12)])
        with self.store.read_only() as conn:
            rows = self.store.search(status="all", limit=-1, compact=False, conn=conn)
            self.assertEqual(len(rows), 12, "a negative limit is no limit")
            self.assertEqual(list(rows[0]), COLUMNS)
            self.assertEqual((rows[0]["id"], rows[0]["tags"]), ("t11", ["p/aoc"]))  # by priority_rank; tags decoded
            compact = self.store.search(order_by="id; DROP TABLE tasks", conn=conn)  # unknown: the default order
            self.assertEqual([t["id"] for t in compact][:2], ["t11", "t10"])
            self.assertEqual((len(compact), list(compact[0])), (10, COMPACT))

    def test_stats_count_what_is_overdue_and_what_is_for_today(self):
        today = date.today()
        self.store.upsert_tasks([
            _task("late", due_date=(today - timedelta(days=1)).isoformat(), priority="🔺"),
            _task("due", due_date=today.isoformat()),
            _task("planned", scheduled_date=today.isoformat()),
            _task("done", "Done", "completed", due_date=(today - timedelta(days=3)).isoformat()),
        ])
        stats = self.store.stats()
        self.assertEqual(stats, {"total_tasks": 4, "status": {"todo": 3, "completed": 1}, "priority_todo": {"🔺": 1},
                                 "overdue_todo": 1, "today_scheduled": 1, "today_due": 1})

    def test_read_sql_caps_the_rows_and_returns_an_error_instead_of_raising(self):
        self.store.upsert_tasks([_task(f"t{i}") for i in range(5)])
        rows, err = self.store.read_sql("SELECT id FROM tasks ORDER BY id", limit=2)
        self.assertEqual(([r["id"] for r in rows], err), (["t0", "t1"], None))
        self.assertEqual(self.store.read_sql("SELECT nope FROM tasks"), ([], "no such column: nope"))


if __name__ == "__main__":
    unittest.main()
