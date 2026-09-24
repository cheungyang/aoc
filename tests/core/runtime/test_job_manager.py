import unittest
import os
import sys
import time
import json
import tempfile

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.runtime.job_manager import JobManager, Job
from core.runtime.session_manager import SessionManager


class TestJobManager(unittest.TestCase):
    def setUp(self):
        # Reset singleton instance
        JobManager._instance = None
        # Use temp db path for isolating test executions
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_db_path = os.path.join(self.temp_dir.name, "memory.db")
        self.manager = JobManager(db_path=self.temp_db_path)

    def tearDown(self):
        JobManager._instance = None
        self.temp_dir.cleanup()

    def test_singleton(self):
        manager1 = JobManager()
        manager2 = JobManager()
        self.assertIs(manager1, manager2)

    def test_add_job(self):
        session = SessionManager.get_session(agent_id="agent1", source="discord", channel="general", job_id="job123")
        self.manager.add_job(session=session)
        jobs = self.manager.get_jobs()
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.job_id, "job123")
        self.assertEqual(job.agent_id, "agent1")
        self.assertEqual(job.session_id, "agent1:discord:general")
        self.assertIsInstance(job.started, float)

    def test_add_job_invalid_type_raises_error(self):
        with self.assertRaises(TypeError):
            self.manager.add_job(session="invalid_str_session")

    def test_add_job_with_prompt(self):
        session = SessionManager.get_session(agent_id="agent2", source="discord", channel="general", job_id="job456")
        self.manager.add_job(session=session, prompt="test prompt")
        job = self.manager._jobs["job456"]
        self.assertEqual(job.prompt, "test prompt")

    def test_get_jobs_filter(self):
        sess1 = SessionManager.get_session(agent_id="agent1", source="discord", channel="general", job_id="job1")
        sess2 = SessionManager.get_session(agent_id="agent2", source="discord", channel="general", job_id="job2")
        self.manager.add_job(sess1)
        self.manager.add_job(sess2)
        self.manager.update_job("job1", "completed")
        self.manager.update_job("job2", "running")

        # default allowlist is ["queued", "running", "error", "partial"]
        active = self.manager.get_jobs()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].job_id, "job2")

    def test_get_job(self):
        session = SessionManager.get_session(agent_id="agent_spec", source="discord", channel="general", job_id="job_specific")
        self.manager.add_job(session, prompt="my prompt")
        job = self.manager.get_job("job_specific")
        self.assertIsNotNone(job)
        self.assertEqual(job.job_id, "job_specific")
        self.assertEqual(job.agent_id, "agent_spec")
        self.assertEqual(job.session_id, "agent_spec:discord:general")
        self.assertEqual(job.prompt, "my prompt")

        # Non-existent job
        self.assertIsNone(self.manager.get_job("non_existent_id"))

    def test_sqlite_persistence_across_instances(self):
        session = SessionManager.get_session(agent_id="agent_p", source="discord", channel="general", job_id="persist_job_1")
        self.manager.add_job(session, prompt="persisted prompt")
        self.manager.update_job("persist_job_1", "running")

        # Reset singleton and create new instance pointing to same DB
        JobManager._instance = None
        manager2 = JobManager(db_path=self.temp_db_path)

        loaded_job = manager2.get_job("persist_job_1")
        self.assertIsNotNone(loaded_job)
        self.assertEqual(loaded_job.status, "running")
        self.assertEqual(loaded_job.agent_id, "agent_p")
        self.assertEqual(loaded_job.prompt, "persisted prompt")

    def test_legacy_json_migration(self):
        # Create a mock legacy JSON file
        import tempfile
        legacy_temp_dir = tempfile.TemporaryDirectory()
        legacy_json_path = os.path.join(legacy_temp_dir.name, "jobs.json")
        legacy_db_path = os.path.join(legacy_temp_dir.name, "memory.db")

        legacy_data = {
            "legacy_job_1": {
                "job_id": "legacy_job_1",
                "agent_id": "legacy_agent",
                "session_id": "legacy_session",
                "started": 12345.0,
                "updated": 12346.0,
                "status": "running",
                "prompt": "legacy prompt"
            }
        }
        with open(legacy_json_path, "w") as f:
            json.dump(legacy_data, f)

        # Initialize JobManager on this path
        JobManager._instance = None
        legacy_manager = JobManager(db_path=legacy_db_path)

        migrated_job = legacy_manager.get_job("legacy_job_1")
        self.assertIsNotNone(migrated_job)
        self.assertEqual(migrated_job.agent_id, "legacy_agent")
        self.assertEqual(migrated_job.status, "running")
        self.assertEqual(migrated_job.prompt, "legacy prompt")

        # Verify legacy JSON is removed
        self.assertFalse(os.path.exists(legacy_json_path))

        legacy_temp_dir.cleanup()

    def test_clean_jobs(self):
        from core.runtime.execution_context import ExecutionContext
        # Populate manager with 50 jobs
        for i in range(50):
            jid = ExecutionContext.new_job_id()
            self.manager._job_ids.append(jid)
            self.manager._jobs[jid] = Job(jid, "agent1", "session", time.time(), time.time(), "running")

        # Add one that should be cleaned
        jid_to_clean = ExecutionContext.new_job_id()
        self.manager._job_ids.append(jid_to_clean)
        self.manager._jobs[jid_to_clean] = Job(jid_to_clean, "completed_agent", "session", time.time(), time.time(), "completed")

        self.assertEqual(len(self.manager._job_ids), 51)

        # Trigger clean by adding 52nd job
        session = SessionManager.get_session(agent_id="agent1", source="discord", channel="general", job_id="final_job")
        self.manager.add_job(session)

        # Cleaned job should be gone from both
        self.assertNotIn(jid_to_clean, self.manager._job_ids)
        self.assertNotIn(jid_to_clean, self.manager._jobs)

    def test_update_job(self):
        session = SessionManager.get_session(agent_id="agent_test", source="discord", channel="general", job_id="job_test")
        self.manager.add_job(session)
        job = self.manager._jobs["job_test"]
        old_updated = job.updated

        time.sleep(0.001)  # Ensure timestamp updates
        self.manager.update_job("job_test", "completed")

        self.assertEqual(job.status, "completed")
        self.assertGreater(job.updated, old_updated)

        # Verify persisted in sqlite
        with self.manager._get_connection() as conn:
            cursor = conn.execute("SELECT status FROM jobs WHERE job_id = ?", ("job_test",))
            self.assertEqual(cursor.fetchone()["status"], "completed")

    def test_kill_job(self):
        session = SessionManager.get_session(agent_id="agent_test", source="discord", channel="general", job_id="job_to_kill")
        self.manager.add_job(session)
        self.manager.kill_job("job_to_kill")
        job = self.manager._jobs["job_to_kill"]
        self.assertEqual(job.status, "killing")

        # Verify persisted in sqlite
        with self.manager._get_connection() as conn:
            cursor = conn.execute("SELECT status FROM jobs WHERE job_id = ?", ("job_to_kill",))
            self.assertEqual(cursor.fetchone()["status"], "killing")

    def test_current_execution_context(self):
        from core.runtime.execution_context import current_execution_context
        from core.runtime.session_manager import SessionManager
        self.assertIsNone(current_execution_context.get())

        sess = SessionManager.get_session(agent_id="main", source="discord", channel="general", job_id="job123")
        token = current_execution_context.set(sess)
        self.assertEqual(current_execution_context.get(), sess)

        current_execution_context.reset(token)
        self.assertIsNone(current_execution_context.get())

    # --- Reaper (Phase 0.4) -------------------------------------------------

    def _add(self, job_id, status, age_seconds=0.0, agent_id="agent1"):
        session = SessionManager.get_session(agent_id=agent_id, source="discord", channel="general", job_id=job_id)
        self.manager.add_job(session, prompt=f"prompt {job_id}")
        self.manager.update_job(job_id, status)
        if age_seconds:
            ts = time.time() - age_seconds
            self.manager._jobs[job_id].updated = ts
            with self.manager._get_connection() as conn:
                conn.execute("UPDATE jobs SET updated = ? WHERE job_id = ?", (ts, job_id))
                conn.commit()

    def _db_status(self, job_id):
        with self.manager._get_connection() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            return row["status"] if row else None

    def test_has_stale_false_when_empty_or_fresh(self):
        self.assertFalse(self.manager.has_stale())
        self._add("fresh_run", "running")
        self._add("fresh_queue", "queued")
        self.assertFalse(self.manager.has_stale())

    def test_has_stale_true_for_old_running_or_queued(self):
        self._add("old_run", "running", age_seconds=3600)
        self.assertTrue(self.manager.has_stale())
        self.assertFalse(self.manager.has_stale(max_age_seconds=7200))

        self.manager.update_job("old_run", "completed")
        self._add("old_queue", "queued", age_seconds=3600)
        self.assertTrue(self.manager.has_stale())

    def test_has_stale_ignores_old_terminal_jobs(self):
        for i, status in enumerate(["completed", "error", "partial", "timeout", "killed"]):
            self._add(f"t{i}", status, age_seconds=3600)
        self.assertFalse(self.manager.has_stale())

    def test_reap_stale_marks_timeout_and_returns_ids(self):
        self._add("old_run", "running", age_seconds=3600)
        self._add("old_queue", "queued", age_seconds=3600)
        self._add("old_killing", "killing", age_seconds=3600)
        self._add("fresh_run", "running")
        self._add("old_done", "completed", age_seconds=3600)

        reaped = self.manager.reap_stale()

        self.assertCountEqual(reaped, ["old_run", "old_queue", "old_killing"])
        for jid in reaped:
            self.assertEqual(self.manager._jobs[jid].status, "timeout")
            self.assertEqual(self._db_status(jid), "timeout")
        self.assertEqual(self._db_status("fresh_run"), "running")
        # Recent terminal rows are retained for record-keeping.
        self.assertEqual(self._db_status("old_done"), "completed")
        self.assertFalse(self.manager.has_stale())
        # Idempotent.
        self.assertEqual(self.manager.reap_stale(), [])

    def test_reap_stale_respects_max_age(self):
        self._add("mid_run", "running", age_seconds=600)
        self.assertEqual(self.manager.reap_stale(max_age_seconds=1800), [])
        self.assertEqual(self.manager.reap_stale(max_age_seconds=300), ["mid_run"])

    def test_reap_stale_purges_terminal_jobs_older_than_7_days(self):
        eight_days = 8 * 24 * 3600
        for status in ["completed", "error", "partial", "timeout", "killed"]:
            self._add(f"ancient_{status}", status, age_seconds=eight_days)
        self._add("recent_timeout", "timeout", age_seconds=6 * 24 * 3600)
        self._add("ancient_running", "running", age_seconds=eight_days)

        reaped = self.manager.reap_stale()

        self.assertEqual(reaped, ["ancient_running"])
        for status in ["completed", "error", "partial", "timeout", "killed"]:
            jid = f"ancient_{status}"
            self.assertIsNone(self._db_status(jid))
            self.assertNotIn(jid, self.manager._jobs)
            self.assertNotIn(jid, self.manager._job_ids)
        self.assertEqual(self._db_status("recent_timeout"), "timeout")
        # A freshly-timed-out job is retained, not purged in the same sweep.
        self.assertEqual(self._db_status("ancient_running"), "timeout")

    def test_reap_stale_sees_rows_not_in_memory(self):
        """Rows written by another process (or before load) are still reaped."""
        with self.manager._get_connection() as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, agent_id, session_id, started, updated, status, prompt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("orphan", "a", "s", time.time() - 7200, time.time() - 7200, "running", ""),
            )
            conn.commit()
        self.assertTrue(self.manager.has_stale())
        self.assertEqual(self.manager.reap_stale(), ["orphan"])
        self.assertEqual(self._db_status("orphan"), "timeout")

    def test_startup_does_not_reap(self):
        self._add("old_run", "running", age_seconds=3600)
        JobManager._instance = None
        manager2 = JobManager(db_path=self.temp_db_path)
        self.assertEqual(manager2.get_job("old_run").status, "running")

    def test_get_jobs_default_excludes_timeout_and_killed(self):
        self._add("j_run", "running")
        self._add("j_timeout", "timeout")
        self._add("j_killed", "killed")
        self._add("j_killing", "killing")
        ids = {j.job_id for j in self.manager.get_jobs()}
        self.assertEqual(ids, {"j_run"})
        ids = {j.job_id for j in self.manager.get_jobs(allowlist=["timeout"])}
        self.assertEqual(ids, {"j_timeout"})

    def test_clean_jobs_removes_timeout_and_killed(self):
        self._add("j_run", "running")
        self._add("j_timeout", "timeout")
        self._add("j_killed", "killed")
        self._add("j_err", "error")
        self.manager._clean_jobs()
        self.assertEqual(set(self.manager._jobs), {"j_run"})
        for jid in ["j_timeout", "j_killed", "j_err"]:
            self.assertIsNone(self._db_status(jid))
        self.assertEqual(self._db_status("j_run"), "running")


class TestProductionIsolation(unittest.TestCase):
    """Guards the tests/conftest.py redirection (Phase 0.3)."""

    PROD_SESSIONS = os.path.realpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "sessions"))

    def test_default_job_manager_db_is_not_production(self):
        JobManager._instance = None
        manager = JobManager()
        self.assertFalse(os.path.realpath(manager.db_path).startswith(self.PROD_SESSIONS + os.sep))

    def test_default_session_store_and_checkpointer_are_not_production(self):
        from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
        for store in (SqliteSessionStore(), SqliteCheckpointer()):
            self.assertFalse(
                os.path.realpath(store.db_path).startswith(self.PROD_SESSIONS + os.sep), store.db_path)


if __name__ == "__main__":
    unittest.main()
