import unittest
import tempfile
import os
import shutil
import json
from unittest.mock import patch

from tools.project_query import project_query
from tests.helpers import execution_context
from core.knowledge.projects.db import get_connection, init_db, upsert_projects


class TestProjectQueryTool(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_projects.db")
        self.pkm_dir = os.path.join(self.temp_dir, "pkm")
        self.projects_dir = os.path.join(self.pkm_dir, "vault", "projects")
        os.makedirs(self.projects_dir, exist_ok=True)

        # Initialize test data
        conn = get_connection(self.db_path)
        init_db(conn)
        upsert_projects(conn, [
            {
                "id": "vault/projects/alpha.md",
                "name": "Alpha Project",
                "file_path": "vault/projects/alpha.md",
                "status": "executing",
                "raw_status": "s/✊Executing",
                "commitment_year": 2026,
                "priority": "🔺",
                "priority_rank": 1,
                "commitments": [
                    {"year": 2025, "priority": "⏫", "priority_rank": 2},
                    {"year": 2026, "priority": "🔺", "priority_rank": 1}
                ],
                "start_date": "2025-01-01",
                "last_reviewed": "2026-08-01",
                "last_updated": "2026-08-10",
                "category": "🦄 Personal",
                "type": "project",
                "aliases": ["#p/alpha"],
                "tags": ["s/✊Executing", "c/🔺2026", "c/⏫2025", "#p/alpha"],
            },
            {
                "id": "vault/projects/beta.md",
                "name": "Beta Project",
                "file_path": "vault/projects/beta.md",
                "status": "done",
                "raw_status": "s/🟢Done",
                "commitment_year": 2025,
                "priority": "🔽",
                "priority_rank": 4,
                "commitments": [
                    {"year": 2025, "priority": "🔽", "priority_rank": 4}
                ],
                "start_date": "2024-01-01",
                "last_reviewed": "2025-01-01",
                "last_updated": "2025-01-01",
                "category": "💌 Gmail",
                "type": "project",
                "aliases": [],
                "tags": ["s/🟢Done", "c/🔽2025"],
            }
        ])
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    @patch("core.knowledge.projects.db.get_db_path")
    @patch("tools.project_query.get_db_path")
    def test_search_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        with execution_context(agent_id="day-planner"):
            # Search all
            resp = project_query.func(
                action="search",
                status="all"
            )
            self.assertIn('"count": 2', resp)

            # Search by status
            resp_exec = project_query.func(
                action="search",
                status="executing"
            )
            self.assertIn("Alpha Project", resp_exec)
            self.assertNotIn("Beta Project", resp_exec)

            # Search by commitment_year
            resp_year = project_query.func(
                action="search",
                commitment_year=2026
            )
            self.assertIn("Alpha Project", resp_year)
            self.assertNotIn("raw_status", resp_year)

            # Full non-compact search
            resp_full = project_query.func(
                action="search",
                commitment_year=2026,
                compact=False
            )
            self.assertIn("raw_status", resp_full)

    @patch("core.knowledge.projects.db.get_db_path")
    @patch("tools.project_query.get_db_path")
    def test_get_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        with execution_context(agent_id="day-planner"):
            resp = project_query.func(
                action="get",
                name="Alpha Project"
            )
            self.assertIn("Alpha Project", resp)
            self.assertIn("vault/projects/alpha.md", resp)

            # Not found
            resp_nf = project_query.func(
                action="get",
                id="nonexistent"
            )
            self.assertIn("Error: Project not found", resp_nf)

    @patch("core.knowledge.projects.db.get_db_path")
    @patch("tools.project_query.get_db_path")
    def test_stats_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        with execution_context(agent_id="day-planner"):
            resp = project_query.func(
                action="stats"
            )
        self.assertIn('"total_projects": 2', resp)

    @patch("core.knowledge.projects.db.get_db_path")
    @patch("tools.project_query.get_db_path")
    def test_sql_action(self, mock_db_path1, mock_db_path2):
        mock_db_path1.return_value = self.db_path
        mock_db_path2.return_value = self.db_path

        with execution_context(agent_id="day-planner"):
            resp = project_query.func(
                action="sql",
                sql="SELECT name, status FROM projects ORDER BY name"
            )
        self.assertIn("Alpha Project", resp)
        self.assertIn("Beta Project", resp)

    def test_missing_execution_context(self):
        # No ambient ExecutionContext: the tool cannot derive an identity and must refuse.
        resp = project_query.func(
            action="stats"
        )
        self.assertIn("no active execution context", resp)


if __name__ == "__main__":
    unittest.main()
