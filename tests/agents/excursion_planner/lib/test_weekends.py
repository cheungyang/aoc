import unittest
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from core.tooling import OK, PARTIAL, Report
from agents.excursion_planner.lib.events import CalendarEvent
from agents.excursion_planner.lib.weekends import (
    FreeTime,
    day_availability,
    free_time,
    free_windows,
    gaps,
    query_range,
    upcoming_saturdays,
    weekend_availability,
)

LA = ZoneInfo("America/Los_Angeles")
FRIDAY_EVENING = datetime(2026, 10, 16, 17, 56, tzinfo=LA)
HOUR = timedelta(hours=1)
DEFAULTS = free_time({}, Report())  # RULES.md's defaults: 08:00–22:00, gaps of 1 h or more


def timed(title, start, end, busy=True):
    return CalendarEvent(title, busy, start=start.astimezone(timezone.utc), end=end.astimezone(timezone.utc))


def all_day(title, first, last, busy=False):
    return CalendarEvent(title, busy, first_day=first, end_day=last + timedelta(days=1))


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LA)


class TestWhichWeekends(unittest.TestCase):

    def test_from_a_weekday_this_weekend_comes_first(self):
        self.assertEqual(upcoming_saturdays(date(2026, 10, 16), 3), [date(2026, 10, 17), date(2026, 10, 24), date(2026, 10, 31)])

    def test_on_saturday_this_weekend_still_counts(self):
        self.assertEqual(upcoming_saturdays(date(2026, 10, 17), 1), [date(2026, 10, 17)])

    def test_on_sunday_next_weekend_comes_first(self):
        self.assertEqual(upcoming_saturdays(date(2026, 10, 18), 1), [date(2026, 10, 24)])

    def test_query_range_spans_saturday_to_the_monday_after_the_last_weekend(self):
        start, end = query_range(date(2026, 10, 16), 3, LA)
        self.assertEqual(start, datetime(2026, 10, 17, 7, tzinfo=timezone.utc))  # 00:00 PDT
        self.assertEqual(end, datetime(2026, 11, 2, 8, tzinfo=timezone.utc))     # 00:00 PST


class TestFreeWindows(unittest.TestCase):

    def test_gaps_between_overlapping_busy_blocks(self):
        day = date(2026, 10, 24)
        busy = [(at(day, 9), at(day, 11)), (at(day, 10), at(day, 12)), (at(day, 15), at(day, 15, 30))]
        windows = free_windows(busy, at(day, 8), at(day, 22), HOUR)
        self.assertEqual(windows, [(at(day, 8), at(day, 9)), (at(day, 12), at(day, 15)), (at(day, 15, 30), at(day, 22))])

    def test_gaps_under_the_shortest_gap_are_dropped(self):
        day = date(2026, 10, 24)
        self.assertEqual(free_windows([(at(day, 8, 30), at(day, 21, 30))], at(day, 8), at(day, 22), HOUR), [])
        self.assertEqual(gaps([(at(day, 8, 30), at(day, 21, 30))], at(day, 8), at(day, 22)),
                         [(at(day, 8), at(day, 8, 30)), (at(day, 21, 30), at(day, 22))])

    def test_a_shortest_gap_of_zero_keeps_every_gap(self):
        day = date(2026, 10, 24)
        windows = free_windows([(at(day, 8, 30), at(day, 21, 30))], at(day, 8), at(day, 22), timedelta(0))
        self.assertEqual(windows, [(at(day, 8), at(day, 8, 30)), (at(day, 21, 30), at(day, 22))])


class TestFreeTime(unittest.TestCase):

    def test_the_defaults_are_todays_values(self):
        report = Report()
        free = free_time({}, report)
        self.assertEqual(free, FreeTime(time(8, 0), time(22, 0), timedelta(hours=1)))
        self.assertEqual(free.describe(), "08:00–22:00, gaps of 1 h or more")
        self.assertEqual(report.status, OK)

    def test_the_users_values(self):
        report = Report()
        free = free_time({"free_from": "09:30", "free_until": "21:00", "min_gap_hours": 1.5}, report)
        self.assertEqual(free, FreeTime(time(9, 30), time(21, 0), timedelta(minutes=90)))
        self.assertEqual(free.describe(), "09:30–21:00, gaps of 1.5 h or more")
        self.assertEqual(report.status, OK)

    def test_short_forms_of_a_time(self):
        report = Report()
        free = free_time({"free_from": "9", "free_until": " 21:05 "}, report)
        self.assertEqual((free.start, free.end), (time(9), time(21, 5)))
        self.assertEqual(report.status, OK)

    def test_a_time_that_doesnt_read_keeps_its_default(self):
        for raw in ("8am", "25:00", "08:75", "noon", ""):
            with self.subTest(raw=raw):
                report = Report()
                self.assertEqual(free_time({"free_until": raw}, report), DEFAULTS)
                self.assertEqual(report.status, PARTIAL)
                self.assertEqual(report.issue_lines(),
                                 [f"RULES.md: free_until: '{raw}' isn't a time like 08:00; using 22:00"])

    def test_a_window_that_ends_before_it_starts_uses_both_defaults(self):
        report = Report()
        self.assertEqual(free_time({"free_from": "22:00", "free_until": "22:00"}, report, "pkm/agents/x/RULES.md"), DEFAULTS)
        self.assertEqual(report.issue_lines(),
                         ["pkm/agents/x/RULES.md: free_from 22:00 isn't before free_until 22:00; using 08:00–22:00"])

    def test_a_gap_outside_0_to_24_hours_uses_the_default(self):
        for hours in (-1, 25, float("nan"), float("inf")):
            with self.subTest(hours=hours):
                report = Report()
                self.assertEqual(free_time({"min_gap_hours": hours}, report).min_gap, HOUR)
                self.assertEqual(report.status, PARTIAL)
                self.assertIn("isn't between 0 and 24; using 1", report.issue_lines()[0])


class TestWeekendAvailability(unittest.TestCase):

    def plan(self, events, now=FRIDAY_EVENING, weeks=3, free=DEFAULTS, report=None):
        return weekend_availability(events, now, weeks, free, report or Report())

    def test_shape_and_labels(self):
        result = self.plan([])
        self.assertEqual(result["timezone"], "America/Los_Angeles")
        self.assertEqual(result["free_time"], "08:00–22:00, gaps of 1 h or more")
        self.assertEqual([w["weekend"] for w in result["weekends"]], ["Oct 17–18", "Oct 24–25", "Oct 31–Nov 1"])
        self.assertEqual([w["when"] for w in result["weekends"]], ["this weekend", "next weekend", "in 2 weeks"])
        saturday = result["weekends"][0]["days"][0]
        self.assertEqual(saturday, {"date": "2026-10-17", "day": "Sat Oct 17", "free": ["08:00–22:00"], "free_hours": 14.0})
        self.assertEqual(set(result), {"timezone", "free_time", "weekends"})

    def test_on_sunday_the_first_weekend_is_next_weekend(self):
        result = self.plan([], now=datetime(2026, 10, 18, 8, tzinfo=LA), weeks=1)
        self.assertEqual((result["weekends"][0]["weekend"], result["weekends"][0]["when"]), ("Oct 24–25", "next weekend"))

    def test_booked_events_split_the_day(self):
        saturday = date(2026, 10, 24)
        result = self.plan([timed("Soccer", at(saturday, 9), at(saturday, 11)), timed("Market", at(saturday, 13), at(saturday, 14), busy=False)])
        day = result["weekends"][1]["days"][0]
        self.assertEqual(day["events"], ["09:00–11:00 Soccer", "13:00–14:00 Market (free)"])
        self.assertEqual(day["free"], ["08:00–09:00", "11:00–22:00"])
        self.assertEqual(day["free_hours"], 12.0)

    def test_the_users_hours_and_shortest_gap_change_the_windows(self):
        saturday = date(2026, 10, 24)
        events = [timed("Soccer", at(saturday, 9), at(saturday, 11)), timed("Lunch", at(saturday, 12), at(saturday, 13, 30)),
                  timed("Call", at(saturday, 15), at(saturday, 15, 30))]
        result = self.plan(events, free=FreeTime(time(10), time(20), timedelta(hours=2)))
        self.assertEqual(result["free_time"], "10:00–20:00, gaps of 2 h or more")
        day = result["weekends"][1]["days"][0]
        self.assertEqual((day["free"], day["free_hours"]), (["15:30–20:00"], 4.5))
        self.assertEqual(result["weekends"][0]["days"][0]["free"], ["10:00–20:00"])

    def test_today_counts_from_now_rounded_up_to_the_half_hour(self):
        result = self.plan([], now=datetime(2026, 10, 17, 13, 10, tzinfo=LA), weeks=1)
        self.assertEqual(result["weekends"][0]["days"][0]["free"], ["13:30–22:00"])

    def test_late_on_saturday_nothing_is_left_today(self):
        result = self.plan([], now=datetime(2026, 10, 17, 21, 30, tzinfo=LA), weeks=1)
        self.assertEqual(result["weekends"][0]["days"][0]["free"], [])

    def test_all_day_events_block_only_when_busy(self):
        result = self.plan([
            all_day("Street festival", date(2026, 10, 17), date(2026, 10, 18)),
            all_day("Weekend trip", date(2026, 10, 24), date(2026, 10, 25), busy=True),
        ])
        festival_day = result["weekends"][0]["days"][1]
        self.assertEqual((festival_day["all_day"], festival_day["free"]), (["Street festival"], ["08:00–22:00"]))
        trip_day = result["weekends"][1]["days"][0]
        self.assertEqual((trip_day["all_day"], trip_day["free"], trip_day["free_hours"]), (["Weekend trip (busy)"], [], 0.0))

    def test_an_event_from_the_night_before_is_clipped(self):
        friday, saturday = date(2026, 10, 23), date(2026, 10, 24)
        result = self.plan([timed("Camping", at(friday, 18), at(saturday, 10))])
        day = result["weekends"][1]["days"][0]
        self.assertEqual((day["events"], day["free"]), (["…–10:00 Camping"], ["10:00–22:00"]))

    def test_the_weekend_with_the_clock_change_says_so_and_still_adds_up(self):
        sunday = date(2026, 11, 1)
        result = self.plan([timed("Brunch", at(sunday, 10), at(sunday, 12))])
        weekend = result["weekends"][2]
        self.assertEqual(weekend["note"], "Sun Nov 1: clocks fall back 1 h at 02:00 (PDT → PST)")
        self.assertEqual(weekend["days"][1]["free"], ["08:00–10:00", "12:00–22:00"])
        self.assertEqual(weekend["days"][1]["free_hours"], 12.0)
        self.assertNotIn("note", result["weekends"][0])


class TestExplainTraces(unittest.TestCase):

    def test_each_day_shows_its_sum_and_what_was_too_short(self):
        saturday = date(2026, 10, 24)
        report = Report()
        day_availability(saturday, [timed("Soccer", at(saturday, 9), at(saturday, 11)),
                                    timed("Nap", at(saturday, 11, 30), at(saturday, 13))], FRIDAY_EVENING, DEFAULTS, report)
        self.assertEqual(report.traces, [
            "Sat Oct 24: 10 h free = 08:00–09:00 (1 h) + 13:00–22:00 (9 h), counting 08:00–22:00; "
            "under 1 h, so left out: 11:00–11:30",
        ])

    def test_a_busy_all_day_event_and_a_day_already_over(self):
        report = Report()
        day_availability(date(2026, 10, 24), [all_day("Day trip", date(2026, 10, 24), date(2026, 10, 24), busy=True)],
                         FRIDAY_EVENING, DEFAULTS, report)
        day_availability(date(2026, 10, 17), [], datetime(2026, 10, 17, 21, 50, tzinfo=LA), DEFAULTS, report)
        self.assertEqual(report.traces, [
            "Sat Oct 24: 0 h free, busy all day (Day trip)",
            "Sat Oct 17: 0 h free, nothing left of 08:00–22:00 after now",
        ])

    def test_today_says_it_counts_from_now(self):
        report = Report()
        day_availability(date(2026, 10, 17), [], datetime(2026, 10, 17, 13, 10, tzinfo=LA), DEFAULTS, report)
        self.assertEqual(report.traces, ["Sat Oct 17: 8.5 h free = 13:30–22:00 (8.5 h), counting 13:30–22:00 from now"])


if __name__ == "__main__":
    unittest.main()
