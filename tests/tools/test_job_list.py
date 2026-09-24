import os
import sys
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.runtime.job_manager import Job
from tools import job_list as job_list_mod
from tools.job_list import job_list


def _job(job_id, status="running", agent_id="agent1", prompt="", started_ago=60.0):
    now = time.time()
    return Job(job_id, agent_id, f"{agent_id}:discord:general", now - started_ago, now, status, prompt)


def _payload(result: str) -> str:
    start = result.index("<payload>") + len("<payload>")
    return result[start:result.index("</payload>")].strip()


class TestJobListTool(unittest.TestCase):
    def _run(self, jobs):
        with patch("tools.job_list.JobManager") as jm_cls:
            jm = MagicMock()
            jm.get_jobs.return_value = jobs
            jm_cls.return_value = jm
            return job_list.func()

    def test_no_jobs(self):
        self.assertEqual(_payload(self._run([])), "No active jobs.")

    def test_one_compact_line_per_job(self):
        payload = _payload(self._run([
            _job("j1", "running", "planner", "plan my day", started_ago=300),
            _job("j2", "queued", "writer", "", started_ago=30),
        ]))
        lines = payload.splitlines()
        self.assertEqual(lines[0], "2 active job(s):")
        self.assertEqual(len(lines), 3)
        # Newest first.
        self.assertTrue(lines[1].startswith("j2 queued writer "))
        self.assertIn("(30s ago)", lines[1])
        self.assertNotIn('"', lines[1])  # empty prompt is omitted
        self.assertTrue(lines[2].startswith("j1 running planner "))
        self.assertIn("(5m ago)", lines[2])
        self.assertTrue(lines[2].endswith('"plan my day"'))
        # Verbose fields dropped.
        self.assertNotIn("session_id", payload)
        self.assertNotIn("discord:general", payload)

    def test_prompt_truncated_and_whitespace_collapsed(self):
        long_prompt = "word\n" * 100
        payload = _payload(self._run([_job("j1", prompt=long_prompt)]))
        line = payload.splitlines()[1]
        quoted = line[line.index('"') + 1:-1]
        self.assertLessEqual(len(quoted), job_list_mod.PROMPT_MAX_CHARS)
        self.assertTrue(quoted.endswith("..."))
        self.assertNotIn("\n", quoted)

    def test_caps_jobs_and_reports_omitted(self):
        total = job_list_mod.MAX_JOBS + 7
        jobs = [_job(f"j{i}", started_ago=float(i + 1)) for i in range(total)]
        payload = _payload(self._run(jobs))
        lines = payload.splitlines()
        self.assertEqual(len(lines), job_list_mod.MAX_JOBS + 1)
        self.assertIn(f"{total} active job(s)", lines[0])
        self.assertIn("7 older omitted", lines[0])
        # Newest (smallest started_ago) kept, oldest dropped.
        self.assertTrue(lines[1].startswith("j0 "))
        self.assertNotIn(f"j{total - 1} ", payload)

    def test_age_formatting(self):
        self.assertEqual(job_list_mod._age(5), "5s")
        self.assertEqual(job_list_mod._age(125), "2m")
        self.assertEqual(job_list_mod._age(7200), "2h")
        self.assertEqual(job_list_mod._age(3 * 86400), "3d")
        self.assertEqual(job_list_mod._age(-3), "0s")

    def test_uses_real_job_manager_default_filter(self):
        """Integration: timeout/completed jobs never appear (conftest isolates the DB)."""
        from core.runtime.job_manager import JobManager
        from core.runtime.session_manager import SessionManager
        jm = JobManager()
        for jid, status in [("live", "running"), ("dead", "timeout"), ("done", "completed")]:
            jm.add_job(SessionManager.get_session(agent_id="a", source="discord", channel="general", job_id=jid))
            jm.update_job(jid, status)
        payload = _payload(job_list.func())
        self.assertIn("live running a", payload)
        self.assertNotIn("dead", payload)
        self.assertNotIn("done", payload)


if __name__ == "__main__":
    unittest.main()
