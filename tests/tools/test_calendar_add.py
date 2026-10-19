import json
import re
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tests.helpers import execution_context
from tools import calendar_add as calendar_add_module
from core.util.local_time import LocalTimeError
from tools.calendar_add import calendar_add, event_times

LA = ZoneInfo("America/Los_Angeles")
CREATED = {"event": {"id": "evt123", "htmlLink": "https://www.google.com/calendar/event?eid=evt123", "summary": "x"}}


def payload(result):
    return json.loads(re.search(r"<payload>(.*)</payload>", result, re.DOTALL).group(1))


@patch("tools.calendar_add.get_local_timezone", return_value=LA)
@patch("tools.calendar_add.ToolsLoader.check_permission", return_value=True)
@patch("tools.gog.create_event", return_value=CREATED)
class TestCalendarAdd(unittest.TestCase):
    """gog.create_event is stubbed in every test: nothing reaches the real calendar."""

    def call(self, *args, **kwargs):
        with execution_context(agent_id="excursion-planner"):
            return calendar_add.func(*args, **kwargs)

    def test_converts_local_time_to_utc_after_the_dst_change(self, create, _perm, _tz):
        result = payload(self.call("2026-11-07 10:00", "2026-11-07 14:00", "Street fair", location="Springfield"))
        create.assert_called_once_with(
            "primary", "Street fair", "2026-11-07T18:00:00Z", "2026-11-07T22:00:00Z",
            all_day=False, location="Springfield", description="",
        )
        self.assertEqual(result["when"], "Sat Nov 7, 10:00–14:00 PST")
        self.assertEqual(result["utc"], "2026-11-07T18:00:00Z → 2026-11-07T22:00:00Z")
        self.assertEqual((result["id"], result["link"]), ("evt123", CREATED["event"]["htmlLink"]))

    def test_before_the_change_it_is_seven_hours(self, create, _perm, _tz):
        self.call("2026-10-24 10:00", "2026-10-24 14:00", "Hike")
        self.assertEqual(create.call_args.args[2:4], ("2026-10-24T17:00:00Z", "2026-10-24T21:00:00Z"))

    def test_all_day_sends_an_exclusive_end_date(self, create, _perm, _tz):
        result = payload(self.call("2026-10-24", "2026-10-25", "Camping"))
        self.assertEqual(create.call_args.args[2:4], ("2026-10-24", "2026-10-26"))
        self.assertTrue(create.call_args.kwargs["all_day"])
        self.assertNotIn("utc", result)

    def test_dst_notes_are_returned(self, create, _perm, _tz):
        result = payload(self.call("2026-11-01 01:30", "2026-11-01 03:00", "Night sky tour"))
        self.assertEqual(len(result["notes"]), 2)

    def test_refused_without_permission(self, create, perm, _tz):
        perm.return_value = False
        result = self.call("2026-10-24 10:00", "2026-10-24 14:00", "Hike")
        perm.assert_called_once()
        self.assertEqual(perm.call_args.args[1:3], ("calendar_add", "create"))
        self.assertIn("may not create calendar events", result)
        create.assert_not_called()

    def test_bad_times_never_reach_gog(self, create, _perm, _tz):
        for start, end in (("2026-10-24 14:00", "2026-10-24 10:00"), ("2027-03-14 02:30", "2027-03-14 04:00"), ("Saturday", "Sunday")):
            with self.subTest(start=start):
                self.assertIn("Error in calendar_add", self.call(start, end, "Hike"))
        self.assertIn("'title' is required", self.call("2026-10-24 10:00", "2026-10-24 14:00", " "))
        create.assert_not_called()

    def test_needs_an_execution_context(self, create, _perm, _tz):
        self.assertIn("no active execution context", calendar_add.func("2026-10-24 10:00", "2026-10-24 14:00", "Hike"))
        create.assert_not_called()

    def test_declares_no_read_actions_so_a_read_only_replay_refuses_it(self, create, _perm, _tz):
        self.assertEqual(tuple(calendar_add_module.READ_ACTIONS), ())


class TestEventTimes(unittest.TestCase):

    def test_daylight_time_is_seven_hours_behind_utc(self):
        times = event_times("2026-10-24 09:00", "2026-10-24 13:00", LA)
        self.assertEqual((times.start, times.end), ("2026-10-24T16:00:00Z", "2026-10-24T20:00:00Z"))
        self.assertEqual(times.when, "Sat Oct 24, 09:00–13:00 PDT")
        self.assertFalse(times.all_day)
        self.assertEqual(times.notes, ())

    def test_after_nov_1_standard_time_is_eight_hours_behind(self):
        times = event_times("2026-11-07 09:00", "2026-11-07 13:00", LA)
        self.assertEqual((times.start, times.end), ("2026-11-07T17:00:00Z", "2026-11-07T21:00:00Z"))
        self.assertEqual(times.when, "Sat Nov 7, 09:00–13:00 PST")

    def test_an_event_across_the_change_lasts_its_real_length_and_says_so(self):
        times = event_times("2026-10-31 22:00", "2026-11-01 03:00", LA)
        # 22:00 PDT to 03:00 PST is six hours, not five.
        self.assertEqual((times.start, times.end), ("2026-11-01T05:00:00Z", "2026-11-01T11:00:00Z"))
        self.assertIn("clocks fall back 1 h at 02:00 (PDT → PST)", times.notes[0])

    def test_all_day_end_is_inclusive_for_the_caller_and_exclusive_for_google(self):
        times = event_times("2026-10-24", "2026-10-25", LA)
        self.assertTrue(times.all_day)
        self.assertEqual((times.start, times.end), ("2026-10-24", "2026-10-26"))
        self.assertEqual(times.when, "Sat Oct 24 – Sun Oct 25 (all day)")

    def test_one_all_day_date(self):
        times = event_times("2026-10-24", "2026-10-24", LA)
        self.assertEqual((times.start, times.end), ("2026-10-24", "2026-10-25"))

    def test_refusals(self):
        cases = [
            ("2026-10-24 13:00", "2026-10-24 09:00", "must be after"),
            ("2026-10-24 09:00", "2026-10-24 09:00", "must be after"),
            ("2026-10-25", "2026-10-24", "before the first"),
            ("2026-10-24", "2026-10-24 13:00", "both as times"),
            ("2027-03-14 02:30", "2027-03-14 04:00", "spring forward"),
        ]
        for start, end, message in cases:
            with self.subTest(start=start, end=end), self.assertRaisesRegex(LocalTimeError, message):
                event_times(start, end, LA)


if __name__ == "__main__":
    unittest.main()
