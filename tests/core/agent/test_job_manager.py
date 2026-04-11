import unittest
import time
from unittest.mock import MagicMock
import sys
import os

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.job_manager import JobManager, Job

class TestJobManager(unittest.TestCase):
    def setUp(self):
        # Reset singleton instance hashes for isolated tests
        JobManager._instance = None
        self.manager = JobManager()

    def test_singleton(self):
        manager2 = JobManager()
        self.assertIs(self.manager, manager2)

    def test_add_job_success(self):
        agent_mock = MagicMock()
        self.manager.add_job("session1", "agent1", "agent2", agent_mock)
        jobs = self.manager.get_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].session_id, "session1")

    def test_add_job_multiple(self):
        agent_mock = MagicMock()
        self.manager.add_job("session1", "agent1", "agent2", agent_mock)
        self.manager.add_job("session1", "agent1", "agent3", agent_mock)
        jobs = self.manager.get_jobs()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].agent_id, "agent2")
        self.assertEqual(jobs[1].agent_id, "agent3")

    def test_remove_job(self):
        agent_mock = MagicMock()
        self.manager.add_job("session1", "agent1", "agent2", agent_mock)
        self.manager.remove_job("session1")
        self.assertEqual(len(self.manager.get_jobs()), 0)

if __name__ == "__main__":
    unittest.main()
