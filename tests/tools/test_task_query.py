import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
import shutil
import json

from tools.task_query import task_query
from core.tasks.db import get_connection, init_db, upsert_tasks


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

    @patch("core.tasks.db.get_db_path")
    @patch("tools.task_query.get_db_path")
    def test_search_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        # Search todo tasks
        res = task_query.func(agent_id="day-planner", action="search", status="todo")
        self.assertIn("tt_001", res)
        self.assertNotIn("tt_002", res)

        # Search by tag
        res_tag = task_query.func(agent_id="day-planner", action="search", status="all", tags=["p/team"])
        self.assertIn("tt_002", res_tag)
        self.assertNotIn("tt_001", res_tag)

        # Search by keyword
        res_query = task_query.func(agent_id="day-planner", action="search", status="all", query="MCP")
        self.assertIn("tt_001", res_query)

    @patch("core.tasks.db.get_db_path")
    @patch("tools.task_query.get_db_path")
    def test_get_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        res = task_query.func(agent_id="day-planner", action="get", task_id="tt_001")
        self.assertIn("Adopt MCP tools in LangGraph", res)
        self.assertIn("vault/projects/AOC.md", res)

        # Invalid ID
        res_inv = task_query.func(agent_id="day-planner", action="get", task_id="nonexistent")
        self.assertIn("Error: Task not found with ID 'nonexistent'", res_inv)

    @patch("core.tasks.db.get_db_path")
    @patch("tools.task_query.get_db_path")
    def test_stats_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        res = task_query.func(agent_id="day-planner", action="stats")
        self.assertIn('"total_tasks": 2', res)

    @patch("core.tasks.db.get_db_path")
    @patch("tools.task_query.get_db_path")
    def test_sql_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        # Valid SELECT
        res = task_query.func(agent_id="day-planner", action="sql", sql="SELECT title FROM tasks WHERE id='tt_001'")
        self.assertIn("Adopt MCP tools in LangGraph", res)

        # Rejected mutation
        res_mut = task_query.func(agent_id="day-planner", action="sql", sql="DELETE FROM tasks")
        self.assertIn("Error: Only read-only SELECT queries are permitted.", res_mut)

    def test_missing_agent_id(self):
        res = task_query.func(agent_id="", action="search")
        self.assertIn("Error: agent_id is required.", res)


if __name__ == "__main__":
    unittest.main()
