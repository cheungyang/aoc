import os
import tempfile
import unittest

from core.scheduler import preconditions as pre
from core.scheduler.preconditions import PreconditionError, build_precondition
from core.scheduler.spec import ScheduleContext


class TestSleepLoggedToday(unittest.TestCase):
    """Google Health is patched out; nothing here touches the network."""

    TZ = "America/Los_Angeles"

    def setUp(self):
        from datetime import datetime
        from unittest import mock
        from zoneinfo import ZoneInfo

        self.zone = ZoneInfo(self.TZ)
        self.now = datetime(2026, 10, 13, 7, 20, tzinfo=self.zone)
        patcher = mock.patch.object(pre.SleepLoggedToday, "_tz", staticmethod(lambda: self.zone))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = 0
        self.sleep_data = {}
        self.error = None

        def fake_read():
            self.calls += 1
            if self.error:
                raise self.error
            return self.sleep_data

        patcher = mock.patch.object(pre.SleepLoggedToday, "_read_sleep", staticmethod(fake_read))
        patcher.start()
        self.addCleanup(patcher.stop)

    def ctx(self, last=None):
        return ScheduleContext(schedule_id="a:x", agent_id="a", now=self.now.timestamp(), last_success_at=last)

    def session(self, wake_date="2026-10-13", minutes=420, main=True, end="2026-10-13 07:05"):
        s = {"wake_date": wake_date, "minutes_asleep": minutes, "end": end}
        if main is not None:
            s["main_sleep"] = main
        return s

    def build(self, **kw):
        return build_precondition({"type": "sleep_logged_today", **kw})

    def test_runs_when_slept_and_not_yet_triggered(self):
        self.sleep_data = {"sessions": [self.session()]}
        decision = self.build().evaluate(self.ctx(None))
        self.assertTrue(decision)
        self.assertIn("07:05", decision.reason)

    def test_yesterday_success_does_not_block(self):
        from datetime import timedelta
        self.sleep_data = {"sessions": [self.session()]}
        yesterday = (self.now - timedelta(days=1)).timestamp()
        self.assertTrue(self.build().evaluate(self.ctx(yesterday)))

    def test_already_triggered_today_skips_without_api_call(self):
        from datetime import timedelta
        earlier = (self.now - timedelta(minutes=20)).timestamp()
        decision = self.build().evaluate(self.ctx(earlier))
        self.assertFalse(decision)
        self.assertIn("already triggered today", decision.reason)
        self.assertEqual(self.calls, 0)

    def test_no_session_for_today(self):
        self.sleep_data = {"sessions": [self.session(wake_date="2026-10-12")]}
        self.assertFalse(self.build().evaluate(self.ctx()))
        self.sleep_data = {}
        self.assertFalse(self.build().evaluate(self.ctx()))

    def test_nap_and_short_sleep_ignored(self):
        self.sleep_data = {"sessions": [self.session(main=False)]}
        self.assertFalse(self.build().evaluate(self.ctx()))
        self.sleep_data = {"sessions": [self.session(minutes=90)]}
        self.assertFalse(self.build().evaluate(self.ctx()))

    def test_missing_main_sleep_flag_still_counts(self):
        self.sleep_data = {"sessions": [self.session(main=None)]}
        self.assertTrue(self.build().evaluate(self.ctx()))

    def test_api_error_fails_closed(self):
        from tools.google_health import GoogleHealthError
        self.error = GoogleHealthError("credentials not found")
        decision = self.build().evaluate(self.ctx())
        self.assertFalse(decision)
        self.assertIn("could not read sleep data", decision.reason)

    def test_takes_no_params(self):
        for bad in ({"min_minutes_asleep": 60}, {"main_sleep_only": False}, {"path": "x"}):
            with self.subTest(bad=bad), self.assertRaises(PreconditionError):
                self.build(**bad)

    def test_once_a_day_gate_survives_restart(self):
        """last_success_at lives in SQLite; a fresh ScheduleState (new process) sees it."""
        from datetime import timedelta
        from core.scheduler.state import ScheduleState

        self.sleep_data = {"sessions": [self.session()]}
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory.db")
            ScheduleState(db).record_success("a:x", (self.now - timedelta(minutes=20)).timestamp())
            reloaded = ScheduleState(db).get_last_success("a:x")
            decision = self.build().evaluate(self.ctx(reloaded))
        self.assertFalse(decision)
        self.assertEqual(self.calls, 0)


if __name__ == "__main__":
    unittest.main()
