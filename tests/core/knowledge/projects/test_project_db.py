import unittest
import tempfile
import os
import shutil
import sqlite3

from core.knowledge.projects.db import (
    get_connection,
    init_db,
    upsert_projects,
    prune_deleted_projects,
    query_projects_db,
    get_project_by_id,
    get_project_by_name,
    get_project_stats,
    execute_read_sql,
)


class TestProjectDb(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_projects.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_db_init_and_crud(self):
        conn = get_connection(self.db_path)
        init_db(conn)

        projects = [
            {
                "id": "uuid_alpha_001",
                "name": "Project Alpha",
                "file_path": "vault/projects/p1.md",
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
                "tags": ["s/✊Executing", "c/🔺2026", "c/⏫2025"],
            },
            {
                "id": "uuid_beta_002",
                "name": "Project Beta",
                "file_path": "vault/projects/p2.md",
                "status": "paused",
                "raw_status": "s/⏸️Pause",
                "commitment_year": 2025,
                "priority": "🔽",
                "priority_rank": 4,
                "commitments": [
                    {"year": 2025, "priority": "🔽", "priority_rank": 4}
                ],
                "start_date": "2024-06-01",
                "last_reviewed": "2025-01-01",
                "last_updated": "2025-01-01",
                "category": "💌 Gmail",
                "type": "project",
                "aliases": [],
                "tags": ["s/⏸️Pause", "c/🔽2025"],
            }
        ]

        stats = upsert_projects(conn, projects)
        self.assertEqual(stats["inserted"], 2)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["unchanged"], 0)

        # Get by UUID ID
        p1 = get_project_by_id(conn, "uuid_alpha_001")
        self.assertIsNotNone(p1)
        self.assertEqual(p1["name"], "Project Alpha")
        self.assertEqual(p1["status"], "executing")
        self.assertEqual(p1["priority"], "🔺")
        self.assertEqual(len(p1["commitments"]), 2)

        # Get by file_path
        p1_by_path = get_project_by_id(conn, "vault/projects/p1.md")
        self.assertEqual(p1_by_path["id"], "uuid_alpha_001")

        # Get by Name
        p2 = get_project_by_name(conn, "project beta")
        self.assertIsNotNone(p2)
        self.assertEqual(p2["name"], "Project Beta")
        self.assertEqual(p2["status"], "paused")

        # Query by status
        res_exec = query_projects_db(conn, status="executing")
        self.assertEqual(len(res_exec), 1)
        self.assertEqual(res_exec[0]["name"], "Project Alpha")

        # Query by commitment year (checks both primary and child table)
        res_2025 = query_projects_db(conn, commitment_year=2025)
        self.assertEqual(len(res_2025), 2)  # both p1 (multi-year) and p2

        res_2026 = query_projects_db(conn, commitment_year=2026)
        self.assertEqual(len(res_2026), 1)
        self.assertEqual(res_2026[0]["name"], "Project Alpha")

        # Query by min_priority
        res_prio = query_projects_db(conn, min_priority="⏫")
        self.assertEqual(len(res_prio), 1)
        self.assertEqual(res_prio[0]["name"], "Project Alpha")

        # Query by category
        res_cat = query_projects_db(conn, category="Gmail")
        self.assertEqual(len(res_cat), 1)
        self.assertEqual(res_cat[0]["name"], "Project Beta")

        # Query by search term
        res_search = query_projects_db(conn, search_term="alpha")
        self.assertEqual(len(res_search), 1)
        self.assertEqual(res_search[0]["name"], "Project Alpha")

        # Stats
        stats_summary = get_project_stats(conn)
        self.assertEqual(stats_summary["total_projects"], 2)
        self.assertEqual(stats_summary["status"]["executing"], 1)
        self.assertEqual(stats_summary["status"]["paused"], 1)
        self.assertEqual(stats_summary["commitment_years"]["2025"], 2)
        self.assertEqual(stats_summary["commitment_years"]["2026"], 1)

        # Update a project (e.g. rename file path)
        projects[0]["file_path"] = "vault/projects/new_alpha_location.md"
        projects[0]["name"] = "Project Alpha Modified"
        stats2 = upsert_projects(conn, projects)
        self.assertEqual(stats2["inserted"], 0)
        self.assertEqual(stats2["updated"], 1)
        self.assertEqual(stats2["unchanged"], 1)

        p1_mod = get_project_by_id(conn, "uuid_alpha_001")
        self.assertEqual(p1_mod["name"], "Project Alpha Modified")
        self.assertEqual(p1_mod["file_path"], "vault/projects/new_alpha_location.md")

        # Prune deleted projects
        pruned = prune_deleted_projects(conn, ["uuid_alpha_001"])
        self.assertEqual(pruned, 1)
        self.assertIsNone(get_project_by_id(conn, "uuid_beta_002"))

        conn.close()

    def test_execute_read_sql(self):
        conn = get_connection(self.db_path)
        init_db(conn)

        # Valid SELECT
        res, err = execute_read_sql(conn, "SELECT count(*) as count FROM projects")
        self.assertIsNone(err)
        self.assertEqual(res[0]["count"], 0)

        # Reject mutation
        res_bad, err_bad = execute_read_sql(conn, "DELETE FROM projects")
        self.assertIn("Error: Only read-only SELECT queries are permitted.", err_bad)

        conn.close()


if __name__ == "__main__":
    unittest.main()
