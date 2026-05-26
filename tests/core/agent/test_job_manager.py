import unittest
import time
from unittest.mock import MagicMock, patch
import sys
import os

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.job_manager import JobManager, Job

class TestJobManager(unittest.TestCase):
    def setUp(self):
        # Reset singleton instance for isolated tests
        JobManager._instance = None
        
        import tempfile
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_jobs_file = os.path.join(self.temp_dir.name, "jobs.json")
        
        self.patcher = patch("core.agent.job_manager.JOBS_FILE", self.temp_jobs_file)
        self.patcher.start()
        
        self.manager = JobManager()

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_singleton(self):
        manager2 = JobManager()
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

    def test_get_jobs(self):
        self.manager.add_job("job1", "agent1", "session1")
        self.manager.add_job("job2", "agent2", "session2")
        jobs = self.manager.get_jobs()
        self.assertEqual(len(jobs), 2)
        ids = [j.job_id for j in jobs]
        self.assertIn("job1", ids)
        self.assertIn("job2", ids)

    def test_clean_jobs(self):

        # Populate manager with 50 jobs
        from core.agent.job_manager import Job
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
        
        time.sleep(0.001) # Ensure timestamp updates
        self.manager.update_job("job_test", "completed")
        
        self.assertEqual(job.status, "completed")
        self.assertGreater(job.updated, old_updated)

    def test_kill_job(self):
        self.manager.add_job("job_to_kill", "agent_test", "session_test")
        self.manager.kill_job("job_to_kill")
        job = self.manager._jobs["job_to_kill"]
        self.assertEqual(job.status, "killing")

    def test_current_job_id(self):
        from core.agent.job_manager import current_job_id
        self.assertIsNone(current_job_id.get())
        
        token = current_job_id.set("test_job")
        self.assertEqual(current_job_id.get(), "test_job")
        
        current_job_id.reset(token)
        self.assertIsNone(current_job_id.get())

if __name__ == "__main__":

    unittest.main()
