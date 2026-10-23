import os
import sqlite3
import unittest

from core.storage.tasks import TasksStore
from core.tooling import DataProblem, Report
from tests.agents.goal_setter.lib.coaching_fixture import CoachingPkm
from agents.goal_setter.lib.snapshot import load_snapshot


class TestSnapshot(unittest.TestCase):

    def setUp(self):
        self.pkm = CoachingPkm()
        self.addCleanup(self.pkm.cleanup)

    def test_reads_every_row_with_json_columns_decoded(self):
        report = Report()
        snapshot = self.pkm.snapshot(report)
        self.assertEqual(len(snapshot.projects), 5)
        self.assertEqual(len(snapshot.tasks), 15)
        run = next(p for p in snapshot.projects if p["id"] == "4c36ea3c2444")
        self.assertEqual(run["aliases"], ["#p/10k"])
        self.assertEqual(next(t for t in snapshot.tasks if t["id"] == "a3")["tags"], ["p/alpha/spec"])
        self.assertEqual(snapshot.pkm_dir, self.pkm.pkm_dir)
        self.assertEqual([(os.path.basename(r.source), r.records) for r in report.reads],
                         [("projects.db", 5), ("tasks.db", 15)])

    def test_reads_config_paths_by_default(self):
        self.pkm.use_as_config()
        self.assertEqual(len(load_snapshot(Report()).projects), 5)

    def test_a_missing_store_is_required_and_is_not_created(self):
        self.pkm.use_as_config()
        missing = os.path.join(self.pkm.root, "nope", "projects.db")
        report = Report()
        with self.assertRaises(DataProblem):
            load_snapshot(report, pkm_dir=self.pkm.pkm_dir, projects_db=missing, tasks_db=self.pkm.tasks_db)
        self.assertEqual(report.status, "failed")
        self.assertIn("projects.db: not found; run its sync first", report.issue_lines()[0])
        self.assertFalse(os.path.exists(os.path.dirname(missing)))

    def test_a_store_that_cant_be_read_is_required(self):
        broken = self.pkm.write("broken.db", "not a database")
        report = Report()
        with self.assertRaises(DataProblem):
            load_snapshot(report, pkm_dir=self.pkm.pkm_dir, projects_db=self.pkm.projects_db, tasks_db=broken)
        self.assertIn("can't be read (file is not a database)", report.issue_lines()[0])

    def test_the_connection_is_read_only(self):
        with TasksStore(self.pkm.tasks_db).read_only() as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM tasks")


if __name__ == "__main__":
    unittest.main()
