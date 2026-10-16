import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
import shutil
import json

from tools.task_query import task_query
from tests.helpers import execution_context
from core.knowledge.tasks.db import get_connection, init_db, upsert_tasks


class TestTaskQueryTool(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_tasks.db")
        
        # Populate test database
        conn = get_connection(self.db_path)
        init_db(conn)
        tasks = [
            {
                "id": "tt_001",
                "title": "Adopt MCP tools in LangGraph",
                "raw_title": "Adopt MCP tools in LangGraph #p/aoc 🔺 📅 2026-08-25",
                "status": "todo",
                "priority": "🔺",
                "priority_rank": 1,
                "tags": ["p/aoc", "a/code"],
                "scheduled_date": "2026-08-20",
                "due_date": "2026-08-25",
                "completed_date": None,
                "dropped_date": None,
                "created_date": "2026-08-01",
                "source": "vault/projects/AOC.md",
                "source_type": "vault",
                "line_number": 15,
                "raw_line": "- [ ] Adopt MCP tools in LangGraph #p/aoc 🔺 📅 2026-08-25 %% aoc_id tt_001 %%"
            },
            {
                "id": "tt_002",
                "title": "Review team newsletter",
                "raw_title": "Review team newsletter 🔽",
                "status": "completed",
                "priority": "🔽",
                "priority_rank": 4,
                "tags": ["p/team"],
                "scheduled_date": None,
                "due_date": None,
                "completed_date": "2026-08-05",
                "dropped_date": None,
                "created_date": None,
                "source": "ticktick/Inbox.md",
                "source_type": "ticktick",
                "line_number": 8,
                "raw_line": "- [x] Review team newsletter 🔽 ✅ 2026-08-05"
            }
        ]
        upsert_tasks(conn, tasks)
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    @patch("core.knowledge.tasks.db.get_db_path")
    @patch("tools.task_query.get_db_path")
    def test_search_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        with execution_context(agent_id="day-planner"):
            # Search todo tasks
            res = task_query.func(action="search", status="todo")
            self.assertIn("tt_001", res)
            self.assertNotIn("tt_002", res)

            # Search by tag
            res_tag = task_query.func(action="search", status="all", tags=["p/team"])
            self.assertIn("tt_002", res_tag)
            self.assertNotIn("tt_001", res_tag)

            # Search by keyword
            res_query = task_query.func(action="search", status="all", query="MCP")
            self.assertIn("tt_001", res_query)
            self.assertNotIn("content_hash", res_query)
            self.assertNotIn("raw_line", res_query)

            # Full non-compact search
            res_full = task_query.func(action="search", status="all", query="MCP", compact=False)
            self.assertIn("content_hash", res_full)
            self.assertIn("raw_line", res_full)

    @patch("core.knowledge.tasks.db.get_db_path")
    @patch("tools.task_query.get_db_path")
    def test_get_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        with execution_context(agent_id="day-planner"):
            res = task_query.func(action="get", task_id="tt_001")
            self.assertIn("Adopt MCP tools in LangGraph", res)
            self.assertIn("vault/projects/AOC.md", res)

            # Invalid ID
            res_inv = task_query.func(action="get", task_id="nonexistent")
            self.assertIn("Error: Task not found with ID 'nonexistent'", res_inv)

    @patch("core.knowledge.tasks.db.get_db_path")
    @patch("tools.task_query.get_db_path")
    def test_stats_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        with execution_context(agent_id="day-planner"):
            res = task_query.func(action="stats")
        self.assertIn('"total_tasks": 2', res)

    @patch("core.knowledge.tasks.db.get_db_path")
    @patch("tools.task_query.get_db_path")
    def test_sql_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        with execution_context(agent_id="day-planner"):
            # Valid SELECT
            res = task_query.func(action="sql", sql="SELECT title FROM tasks WHERE id='tt_001'")
            self.assertIn("Adopt MCP tools in LangGraph", res)

            # Rejected mutation
            res_mut = task_query.func(action="sql", sql="DELETE FROM tasks")
            self.assertIn("Error: Only read-only SELECT queries are permitted.", res_mut)

    def test_missing_execution_context(self):
        # No ambient ExecutionContext: the tool cannot derive an identity and must refuse.
        res = task_query.func(action="search")
        self.assertIn("no active execution context", res)


class TestTaskQueryCap(unittest.TestCase):
    """A capped result says how many rows it left out.

    Regression: results were cut to `limit` (default 10, though the docstring
    said 50) with nothing to say so. check_recurring_chores read 10 of 60
    completion records as the whole history and computed its windows from them.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir)
        self.db_path = os.path.join(self.temp_dir, "test_tasks.db")
        conn = get_connection(self.db_path)
        init_db(conn)
        upsert_tasks(conn, [
            {
                "id": f"tt_{i:03d}", "title": f"Chore {i}", "raw_title": f"Chore {i} 🔁 every week",
                "status": "todo", "priority": None, "priority_rank": 3, "tags": [],
                "scheduled_date": None, "due_date": f"2026-10-{i:02d}", "completed_date": None,
                "dropped_date": None, "created_date": None, "source": "ticktick/Inbox.md",
                "source_type": "ticktick", "line_number": i, "raw_line": f"- [ ] Chore {i}",
            }
            for i in range(1, 13)
        ])
        conn.close()
        for target in ("core.knowledge.tasks.db.get_db_path", "tools.task_query.get_db_path"):
            patcher = patch(target, return_value=self.db_path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_query(self, **kwargs):
        with execution_context(agent_id="day-planner"):
            res = task_query.func(**kwargs)
        return json.loads(res.split("<payload>")[1].split("</payload>")[0])

    def test_default_search_shows_ten_and_counts_the_rest(self):
        payload = self.run_query(action="search")
        self.assertEqual(payload["count"], 10)
        self.assertEqual(len(payload["tasks"]), 10)
        self.assertEqual(payload["note"], "2 more rows not shown; raise 'limit' or narrow the query.")

    def test_sql_is_capped_too_and_says_so(self):
        payload = self.run_query(action="sql", sql="SELECT id FROM tasks ORDER BY id", limit=5)
        self.assertEqual([r["id"] for r in payload["rows"]], [f"tt_{i:03d}" for i in range(1, 6)])
        self.assertEqual(payload["note"], "7 more rows not shown; raise 'limit' or narrow the query.")

    def test_a_query_that_fits_has_no_note(self):
        payload = self.run_query(action="sql", sql="SELECT id FROM tasks LIMIT 3", limit=3)
        self.assertEqual(payload["count"], 3)
        self.assertNotIn("note", payload)
        payload = self.run_query(action="search", limit=50)
        self.assertEqual(payload["count"], 12)
        self.assertNotIn("note", payload)

    def test_counting_tolerates_semicolons_and_trailing_comments(self):
        for sql in ("SELECT id FROM tasks;", "SELECT id FROM tasks -- every chore"):
            payload = self.run_query(action="sql", sql=sql, limit=11)
            self.assertEqual(payload["note"], "1 more row not shown; raise 'limit' or narrow the query.", sql)

    def test_an_uncountable_overflow_still_says_rows_were_cut(self):
        from tools.task_query import _capped_payload, _count_rows

        conn = get_connection(self.db_path)
        self.addCleanup(conn.close)
        self.assertIsNone(_count_rows(conn, "SELECT nope FROM nowhere"))
        payload = json.loads(_capped_payload("rows", [{"id": 1}], None))
        self.assertEqual(payload["note"], "More rows not shown; raise 'limit' or narrow the query.")
        self.assertEqual(list(payload), ["count", "note", "rows"])


if __name__ == "__main__":
    unittest.main()
