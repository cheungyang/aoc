import unittest
import time
import sys
import os
import json
import tempfile

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.job_manager import JobManager, Job


class TestJobManager(unittest.TestCase):
    def setUp(self):
        # Reset singleton instance for isolated tests
        JobManager._instance = None
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_db_path = os.path.join(self.temp_dir.name, "memory.db")
        self.manager = JobManager(db_path=self.temp_db_path)

    def tearDown(self):
        JobManager._instance = None
        self.temp_dir.cleanup()

    def test_singleton(self):
        manager2 = JobManager(db_path=self.temp_db_path)
        self.assertIs(self.manager, manager2)

    def test_new_job_id(self):
        job_id = self.manager.new_job_id("agent1")
        self.assertTrue(job_id.startswith("agent1:job:"))
        self.assertEqual(len(job_id.split(":")), 3)

    def test_add_job(self):
        self.manager.add_job("job123", "agent1", "session456")
        jobs = self.manager.get_jobs()
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.job_id, "job123")
        self.assertEqual(job.agent_id, "agent1")
        self.assertEqual(job.session_id, "session456")
        self.assertIsInstance(job.started, float)

    def test_add_job_with_prompt(self):
        self.manager.add_job("job456", "agent2", "session789", initial_prompt="test prompt")
        job = self.manager._jobs["job456"]
        self.assertEqual(job.initial_prompt, "test prompt")

    def test_get_jobs(self):
        self.manager.add_job("job1", "agent1", "session1")
        self.manager.add_job("job2", "agent2", "session2")
        jobs = self.manager.get_jobs()
        self.assertEqual(len(jobs), 2)
        ids = [j.job_id for j in jobs]
        self.assertIn("job1", ids)
        self.assertIn("job2", ids)

    def test_get_job(self):
        self.manager.add_job("job_specific", "agent_spec", "session_spec", initial_prompt="my prompt")
        job = self.manager.get_job("job_specific")
        self.assertIsNotNone(job)
        self.assertEqual(job.agent_id, "agent_spec")
        self.assertEqual(job.session_id, "session_spec")
        self.assertEqual(job.initial_prompt, "my prompt")

        # Nonexistent job
        self.assertIsNone(self.manager.get_job("nonexistent_id"))

    def test_sqlite_persistence_across_instances(self):
        self.manager.add_job("persist_job_1", "agent_p", "session_p", initial_prompt="persisted prompt")
        self.manager.update_job("persist_job_1", "running")

        # Reset singleton and create new instance pointing to same DB
        JobManager._instance = None
        manager2 = JobManager(db_path=self.temp_db_path)

        loaded_job = manager2.get_job("persist_job_1")
        self.assertIsNotNone(loaded_job)
        self.assertEqual(loaded_job.status, "running")
        self.assertEqual(loaded_job.agent_id, "agent_p")
        self.assertEqual(loaded_job.initial_prompt, "persisted prompt")

    def test_legacy_jobs_json_migration(self):
        # Create a fresh temporary directory with legacy jobs.json
        legacy_temp_dir = tempfile.TemporaryDirectory()
        legacy_json_path = os.path.join(legacy_temp_dir.name, "jobs.json")
        legacy_db_path = os.path.join(legacy_temp_dir.name, "memory.db")

        legacy_data = {
            "migrated_job:1": {
                "job_id": "migrated_job:1",
                "agent_id": "legacy_agent",
                "session_id": "legacy_session",
                "started": 1000.0,
                "updated": 1005.0,
                "status": "partial",
                "initial_prompt": "legacy prompt"
            }
        }
        with open(legacy_json_path, "w") as f:
            json.dump(legacy_data, f)

        JobManager._instance = None
        migrated_manager = JobManager(db_path=legacy_db_path)

        # Verify job was migrated to sqlite
        job = migrated_manager.get_job("migrated_job:1")
        self.assertIsNotNone(job)
        self.assertEqual(job.agent_id, "legacy_agent")
        self.assertEqual(job.status, "partial")
        self.assertEqual(job.initial_prompt, "legacy prompt")

        # Verify legacy file was removed
        self.assertFalse(os.path.exists(legacy_json_path))

        legacy_temp_dir.cleanup()

    def test_clean_jobs(self):
        # Populate manager with 50 jobs
        for i in range(50):
            jid = self.manager.new_job_id("agent1")
            self.manager._jobs[jid] = Job(jid, "agent1", "session", time.time(), time.time(), "running")

        # Add one that should be cleaned
        jid_to_clean = self.manager.new_job_id("completed_agent")
        self.manager._jobs[jid_to_clean] = Job(jid_to_clean, "completed_agent", "session", time.time(), time.time(), "completed")

        self.assertEqual(len(self.manager._job_ids), 51)

        # Trigger clean by adding 52nd job
        self.manager.add_job("final_job", "agent1", "session")

        # Cleaned job should be gone from both
        self.assertNotIn(jid_to_clean, self.manager._job_ids)
        self.assertNotIn(jid_to_clean, self.manager._jobs)

    def test_update_job(self):
        self.manager.add_job("job_test", "agent_test", "session_test")
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
        self.manager.add_job("job_to_kill", "agent_test", "session_test")
        self.manager.kill_job("job_to_kill")
        job = self.manager._jobs["job_to_kill"]
        self.assertEqual(job.status, "killing")

        # Verify persisted in sqlite
        with self.manager._get_connection() as conn:
            cursor = conn.execute("SELECT status FROM jobs WHERE job_id = ?", ("job_to_kill",))
            self.assertEqual(cursor.fetchone()["status"], "killing")

    def test_current_job_id(self):
        from core.agent.job_manager import current_job_id
        self.assertIsNone(current_job_id.get())

        token = current_job_id.set("test_job")
        self.assertEqual(current_job_id.get(), "test_job")

        current_job_id.reset(token)
        self.assertIsNone(current_job_id.get())

    def test_current_agent_id(self):
        from core.agent.job_manager import current_agent_id
        self.assertIsNone(current_agent_id.get())

        token = current_agent_id.set("main")
        self.assertEqual(current_agent_id.get(), "main")

        current_agent_id.reset(token)
        self.assertIsNone(current_agent_id.get())


if __name__ == "__main__":
    unittest.main()
