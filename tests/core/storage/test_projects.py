"""projects.db (core/storage/projects.py): its schema and ProjectsStore."""
import unittest
import tempfile
import os
import shutil
import sqlite3
from unittest.mock import patch

from core.storage import base
from core.storage.projects import ProjectsStore
from core.util.config import Config


class TestProjectDb(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_projects.db")
        self.store = ProjectsStore(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_db_init_and_crud(self):
        store = self.store
        store.init()

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

        stats = store.upsert_projects(projects)
        self.assertEqual(stats["inserted"], 2)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["unchanged"], 0)

        # Get by UUID ID
        p1 = store.get_project("uuid_alpha_001")
        self.assertIsNotNone(p1)
        self.assertEqual(p1["name"], "Project Alpha")
        self.assertEqual(p1["status"], "executing")
        self.assertEqual(p1["priority"], "🔺")
        self.assertEqual(len(p1["commitments"]), 2)

        # Get by file_path
        p1_by_path = store.get_project("vault/projects/p1.md")
        self.assertEqual(p1_by_path["id"], "uuid_alpha_001")

        # Get by Name
        p2 = store.get_project_by_name("project beta")
        self.assertIsNotNone(p2)
        self.assertEqual(p2["name"], "Project Beta")
        self.assertEqual(p2["status"], "paused")

        # Query by status
        res_exec = store.search(status="executing")
        self.assertEqual(len(res_exec), 1)
        self.assertEqual(res_exec[0]["name"], "Project Alpha")

        # Query by commitment year (checks both primary and child table)
        res_2025 = store.search(commitment_year=2025)
        self.assertEqual(len(res_2025), 2)  # both p1 (multi-year) and p2

        res_2026 = store.search(commitment_year=2026)
        self.assertEqual(len(res_2026), 1)
        self.assertEqual(res_2026[0]["name"], "Project Alpha")

        # Query by min_priority
        res_prio = store.search(min_priority="⏫")
        self.assertEqual(len(res_prio), 1)
        self.assertEqual(res_prio[0]["name"], "Project Alpha")

        # Query by category
        res_cat = store.search(category="Gmail")
        self.assertEqual(len(res_cat), 1)
        self.assertEqual(res_cat[0]["name"], "Project Beta")

        # Query by search term
        res_search = store.search(search_term="alpha")
        self.assertEqual(len(res_search), 1)
        self.assertEqual(res_search[0]["name"], "Project Alpha")

        # Stats
        stats_summary = store.stats()
        self.assertEqual(stats_summary["total_projects"], 2)
        self.assertEqual(stats_summary["status"]["executing"], 1)
        self.assertEqual(stats_summary["status"]["paused"], 1)
        self.assertEqual(stats_summary["commitment_years"]["2025"], 2)
        self.assertEqual(stats_summary["commitment_years"]["2026"], 1)

        # Update a project (e.g. rename file path)
        projects[0]["file_path"] = "vault/projects/new_alpha_location.md"
        projects[0]["name"] = "Project Alpha Modified"
        stats2 = store.upsert_projects(projects)
        self.assertEqual(stats2["inserted"], 0)
        self.assertEqual(stats2["updated"], 1)
        self.assertEqual(stats2["unchanged"], 1)

        p1_mod = store.get_project("uuid_alpha_001")
        self.assertEqual(p1_mod["name"], "Project Alpha Modified")
        self.assertEqual(p1_mod["file_path"], "vault/projects/new_alpha_location.md")

        # Prune deleted projects
        pruned = store.prune_deleted(["uuid_alpha_001"])
        self.assertEqual(pruned, 1)
        self.assertIsNone(store.get_project("uuid_beta_002"))
        self.assertEqual(store.count("project_commitments", {"id": "uuid_beta_002"}), 0)

    def test_read_sql(self):
        self.store.init()

        # Valid SELECT
        res, err = self.store.read_sql("SELECT count(*) as count FROM projects")
        self.assertIsNone(err)
        self.assertEqual(res[0]["count"], 0)

        # Reject mutation
        res_bad, err_bad = self.store.read_sql("DELETE FROM projects")
        self.assertIn("Error: Only read-only SELECT queries are permitted.", err_bad)


def _project(pid, status="executing", commitments=()):
    return {"id": pid, "name": pid, "file_path": f"vault/projects/{pid}.md", "status": status,
            "commitments": [{"year": y} for y in commitments]}


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


class TestProjectsSchema(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = ProjectsStore(os.path.join(tmp.name, "pkm", "projects.db"))

    def names(self, kind):
        with self.store.read_only() as conn:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))}

    def test_init_creates_both_tables_their_indexes_and_the_search_index_once(self):
        self.store.init()
        self.store.init()
        with self.store.connect() as conn:
            self.store.init(conn)
        self.assertTrue({"projects", "project_commitments"} | ({"projects_fts"} if FTS5 else set())
                        <= self.names("table"))
        self.assertEqual({n for n in self.names("index") if not n.startswith("sqlite_")}, {
            "idx_projects_status", "idx_projects_commitment_year", "idx_projects_priority_rank",
            "idx_projects_category", "idx_projects_last_reviewed", "idx_projects_last_updated",
            "idx_projects_file_path", "idx_commitments_id", "idx_commitments_year", "idx_commitments_priority_rank",
        })
        self.assertEqual(self.names("trigger"), {"projects_ai", "projects_ad", "projects_au"} if FTS5 else set())

    def test_without_fts5_the_tables_are_still_created(self):
        real = base.load_schema

        def no_fts5(name):
            return real(name).replace("USING fts5(", "USING no_such_module(") if name == "projects_fts" else real(name)

        with patch.object(base, "load_schema", side_effect=no_fts5), patch("builtins.print") as printed:
            self.store.init()
        self.assertIn("projects_fts.sql", printed.call_args[0][0])
        self.assertEqual(self.names("trigger"), set())
        self.store.upsert_projects([_project("alpha", commitments=[2026])])
        self.assertEqual(self.store.search(commitment_year=2026)[0]["id"], "alpha")

    def test_connecting_leaves_a_table_built_by_someone_else_as_it_is(self):
        with self.store.connect() as conn:
            conn.execute("CREATE TABLE projects (status TEXT)")  # the scheduler preconditions' tests do this
        with self.store.connect() as conn:
            self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(projects)")], ["status"])

    def test_deleting_a_project_deletes_its_commitments(self):
        self.store.init()
        self.store.upsert_projects([_project("alpha", commitments=[2025, 2026]), _project("beta", commitments=[2026])])
        self.assertEqual(self.store.delete("projects", {"id": "alpha"}), 1)  # foreign_keys is on for every connection
        self.assertEqual([r["id"] for r in self.store.select("project_commitments")], ["beta"])

    def test_count_with_status_ignores_the_stored_case(self):
        self.store.init()
        self.store.upsert_projects([_project("a", "executing"), _project("b", "Executing"), _project("c", "planning")])
        with self.store.read_only() as conn:
            self.assertEqual(self.store.count_with_status("executing", conn=conn), 2)
            self.assertEqual(self.store.count_with_status("done", conn=conn), 0)

    def test_the_default_path_is_the_config_one(self):
        config = Config()
        saved = config._projects_db_path  # the override, not the env default
        config.projects_db_path = "/tmp/elsewhere/projects.db"
        try:
            self.assertEqual(ProjectsStore().path, "/tmp/elsewhere/projects.db")
        finally:
            config._projects_db_path = saved

    def test_reads_work_over_a_read_only_connection(self):
        self.store.init()
        self.store.upsert_projects([_project("alpha", commitments=[2026])])
        with self.store.read_only() as conn:
            self.assertEqual([p["id"] for p in self.store.search(status="all", limit=-1, compact=False, conn=conn)],
                             ["alpha"])
            with self.assertRaises(sqlite3.OperationalError):
                self.store.upsert_projects([_project("beta")], conn=conn)


if __name__ == "__main__":
    unittest.main()
