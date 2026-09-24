import unittest
from unittest.mock import MagicMock, patch

from scripts import reap_jobs


class TestReapJobs(unittest.TestCase):
    def test_schedulable_by_the_scheduler(self):
        from core.scheduler.script_runner import check_script
        self.assertEqual(check_script("reap_jobs.py"), [])

    def test_has_work_asks_job_manager(self):
        jm = MagicMock()
        with patch.object(reap_jobs, "JobManager", return_value=jm):
            jm.has_stale.return_value = False
            has, reason = reap_jobs.has_work(MagicMock())
            self.assertFalse(has)
            self.assertIn("no stale jobs", reason)
            jm.has_stale.assert_called_with(max_age_seconds=reap_jobs.DEFAULT_STALE_SECONDS)

            jm.has_stale.return_value = True
            self.assertTrue(reap_jobs.has_work(MagicMock())[0])

    def test_reap_reports_only_when_something_was_reaped(self):
        jm = MagicMock()
        with patch.object(reap_jobs, "JobManager", return_value=jm):
            jm.reap_stale.return_value = []
            self.assertEqual(reap_jobs.reap(60), "")

            jm.reap_stale.return_value = ["j1", "j2"]
            self.assertEqual(reap_jobs.reap(60), "Marked 2 stale job(s) as timeout: j1, j2")
        jm.reap_stale.assert_called_with(max_age_seconds=60)

    def test_main_prints_nothing_when_idle(self):
        with patch.object(reap_jobs, "reap", return_value="") as reap, \
             patch("builtins.print") as printed:
            reap_jobs.main(["--max-age", "5"])
        reap.assert_called_once_with(5)
        printed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
