import unittest
import os
import sys
import time
import json
import tempfile

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.job_manager import JobManager, Job
from core.agent.session_manager import SessionManager


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
        from core.agent.session_identifier import SessionIdentifier
        # Populate manager with 50 jobs
        for i in range(50):
            jid = SessionIdentifier.new_job_id()
            self.manager._job_ids.append(jid)
            self.manager._jobs[jid] = Job(jid, "agent1", "session", time.time(), time.time(), "running")

        # Add one that should be cleaned
        jid_to_clean = SessionIdentifier.new_job_id()
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

    def test_current_session_identifier(self):
        from core.agent.job_manager import current_session_identifier
        from core.agent.session_manager import SessionManager
        self.assertIsNone(current_session_identifier.get())

        sess = SessionManager.get_session(agent_id="main", source="discord", channel="general", job_id="job123")
        token = current_session_identifier.set(sess)
        self.assertEqual(current_session_identifier.get(), sess)

        current_session_identifier.reset(token)
        self.assertIsNone(current_session_identifier.get())


if __name__ == "__main__":
    unittest.main()
