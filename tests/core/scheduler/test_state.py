import os
import tempfile
import unittest

from core.scheduler.state import ScheduleState


class TestScheduleState(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = ScheduleState(os.path.join(self._tmp.name, "memory.db"))

    def tearDown(self):
        self._tmp.cleanup()

    def test_unknown_schedule(self):
        self.assertEqual(self.state.get("x"), {})
        self.assertIsNone(self.state.get_last_success("x"))

    def test_success_records_start_time(self):
        self.state.record_success("x", 100.0)
        self.assertEqual(self.state.get_last_success("x"), 100.0)
        self.assertEqual(self.state.get("x")["last_run_at"], 100.0)

    def test_failure_does_not_move_last_success(self):
        self.state.record_success("x", 100.0)
        self.state.record_failure("x", 200.0)
        row = self.state.get("x")
        self.assertEqual(row["last_success_at"], 100.0)
        self.assertEqual(row["last_run_at"], 200.0)

    def test_skip_is_noted_without_touching_success(self):
        self.state.record_success("x", 100.0)
        self.state.record_skip("x", "nothing to do", at=300.0)
        row = self.state.get("x")
        self.assertEqual(row["last_success_at"], 100.0)
        self.assertEqual(row["last_skip_at"], 300.0)
        self.assertEqual(row["last_skip_reason"], "nothing to do")

    def test_schedules_are_independent(self):
        self.state.record_success("a", 1.0)
        self.state.record_success("b", 2.0)
        self.assertEqual(self.state.get_last_success("a"), 1.0)
        self.assertEqual(self.state.get_last_success("b"), 2.0)

    def test_default_path_follows_session_store(self):
        from core.knowledge.memory import sqlite_session_store
        self.assertEqual(ScheduleState().db_path, sqlite_session_store.DEFAULT_DB_PATH)


if __name__ == "__main__":
    unittest.main()
