import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from core.tooling import FAILED, OK, PARTIAL, Report
from agents.excursion_planner.lib.events import CALENDAR, MAX_RAW, parse_events

LA = ZoneInfo("America/Los_Angeles")

# Shaped like `gog calendar events primary --json`: Google Calendar Event
# resources plus gog's own display fields, trimmed.
GOG_EVENTS = {
    "events": [
        {
            "id": "7k1", "status": "confirmed", "summary": "Soccer practice",
            "start": {"dateTime": "2026-10-17T09:00:00-07:00", "timeZone": "America/Los_Angeles"},
            "end": {"dateTime": "2026-10-17T11:00:00-07:00", "timeZone": "America/Los_Angeles"},
            "startDayOfWeek": "Saturday", "startLocal": "2026-10-17T09:00:00-07:00",
        },
        {
            "id": "8m2", "status": "confirmed", "summary": "Street festival",
            "transparency": "transparent",
            "start": {"date": "2026-10-17"}, "end": {"date": "2026-10-19"},
        },
        {
            "id": "9p3", "status": "cancelled", "summary": "Brunch (moved)",
            "start": {"dateTime": "2026-10-18T10:00:00-07:00"}, "end": {"dateTime": "2026-10-18T12:00:00-07:00"},
        },
        {
            "id": "1q4", "status": "confirmed", "summary": "Team offsite dinner",
            "attendees": [{"email": "me@example.com", "self": True, "responseStatus": "declined"}],
            "start": {"dateTime": "2026-10-17T18:00:00-07:00"}, "end": {"dateTime": "2026-10-17T21:00:00-07:00"},
        },
        {
            "id": "2r5", "status": "confirmed", "eventType": "workingLocation", "summary": "Home",
            "start": {"date": "2026-10-19"}, "end": {"date": "2026-10-20"},
        },
        {
            "id": "3s6", "status": "confirmed",
            "start": {"dateTime": "2026-10-18T14:00:00Z"}, "end": {"dateTime": "2026-10-18T15:00:00Z"},
        },
    ],
    "nextPageToken": "",
}


class TestParseEvents(unittest.TestCase):

    def setUp(self):
        self.report = Report()
        self.events = parse_events(GOG_EVENTS, LA, self.report)

    def test_drops_cancelled_declined_and_working_location(self):
        self.assertEqual(
            [e.title for e in self.events],
            ["Soccer practice", "Street festival", "(busy)"],
        )

    def test_what_is_left_out_is_accounted_for(self):
        self.assertEqual(self.report.status, OK)
        self.assertEqual(self.report.traces, [
            "left out: 'Brunch (moved)' (cancelled), 'Team offsite dinner' (declined), 'Home' (working location)",
        ])
        read, = self.report.reads
        self.assertEqual((read.source, read.records, read.skipped), (CALENDAR, 3, 3))

    def test_timed_event(self):
        soccer = self.events[0]
        self.assertFalse(soccer.all_day)
        self.assertTrue(soccer.busy)
        self.assertEqual(soccer.start, datetime(2026, 10, 17, 16, tzinfo=timezone.utc))

    def test_all_day_event_keeps_googles_exclusive_end(self):
        festival = self.events[1]
        self.assertTrue(festival.all_day)
        self.assertFalse(festival.busy)
        self.assertEqual((festival.first_day, festival.end_day), (date(2026, 10, 17), date(2026, 10, 19)))

    def test_an_untitled_event_still_blocks_time(self):
        self.assertTrue(self.events[2].busy)

    def test_a_time_without_offset_reads_in_its_own_zone(self):
        events = parse_events([{
            "summary": "Call",
            "start": {"dateTime": "2026-10-17T09:00:00", "timeZone": "America/New_York"},
            "end": {"dateTime": "2026-10-17T10:00:00", "timeZone": "America/New_York"},
        }], LA, Report())
        self.assertEqual(events[0].start, datetime(2026, 10, 17, 13, tzinfo=timezone.utc))

    def test_an_event_with_no_end_is_a_moment_or_a_single_day(self):
        moment, day = parse_events([
            {"summary": "Pickup", "start": {"dateTime": "2026-10-17T09:00:00-07:00"}},
            {"summary": "Fair", "start": {"date": "2026-10-17"}},
        ], LA, Report())
        self.assertEqual(moment.end, moment.start)
        self.assertEqual((day.first_day, day.end_day), (date(2026, 10, 17), date(2026, 10, 18)))

    def test_empty_replies_mean_no_events(self):
        for payload in ({"events": [], "nextPageToken": ""}, {}, {"events": None}, {"nextPageToken": ""}, []):
            with self.subTest(payload=payload):
                report = Report()
                self.assertEqual(parse_events(payload, LA, report), [])
                self.assertEqual((report.status, report.issues), (OK, []))


class TestWhatCantBeRead(unittest.TestCase):

    def test_unreadable_events_come_back_raw_and_the_rest_still_count(self):
        report = Report()
        events = parse_events({"events": [
            {"summary": "Dinner", "start": {"dateTime": "Saturday 7pm"}},
            {"summary": "Picnic", "start": {}, "end": {}},
            {"summary": "Call", "start": {"dateTime": "2026-10-17T09:00:00", "timeZone": "Mars/Olympus"}},
            {"summary": "Fair", "start": {"date": "2026-13-40"}},
            {"summary": "Hike", "start": "2026-10-17"},
            "Soccer 9am",
            {"summary": "Soccer", "start": {"dateTime": "2026-10-17T09:00:00-07:00"},
             "end": {"dateTime": "2026-10-17T11:00:00-07:00"}},
        ]}, LA, report)
        self.assertEqual([e.title for e in events], ["Soccer"])
        self.assertEqual(report.status, PARTIAL)
        issue, = report.issues
        self.assertEqual((issue.source, issue.problem, issue.required), (CALENDAR, "6 lines not recognized", False))
        self.assertEqual(issue.lines[0], '{"summary": "Dinner", "start": {"dateTime": "Saturday 7pm"}}')
        self.assertEqual(issue.lines[5], '"Soccer 9am"')
        self.assertEqual((report.reads[0].records, report.reads[0].skipped), (1, 6))

    def test_a_raw_line_shows_the_fields_read_first_and_is_cut(self):
        report = Report()
        parse_events([{"kind": "calendar#event", "htmlLink": "https://x" * 100, "summary": "Gala",
                       "start": {"dateTime": "soon"}}], LA, report)
        line = report.issues[0].lines[0]
        self.assertTrue(line.startswith('{"summary": "Gala", "start": {"dateTime": "soon"}, "kind": '))
        self.assertEqual(len(line), MAX_RAW + 1)
        self.assertTrue(line.endswith("…"))

    def test_a_reply_with_no_event_list_is_missing(self):
        for payload in ("Calendar not found", None, 3, {"events": "none"}, {"data": [{"summary": "Soccer"}]}):
            with self.subTest(payload=payload):
                report = Report()
                self.assertEqual(parse_events(payload, LA, report), [])
                self.assertEqual(report.status, FAILED)
                issue, = report.issues
                self.assertEqual(issue.source, CALENDAR)
                self.assertTrue(issue.problem.startswith("gog's reply holds no event list: "))

    def test_more_events_than_fetched_is_partial(self):
        report = Report()
        parse_events({"events": [], "nextPageToken": "abc"}, LA, report, limit=300)
        self.assertEqual(report.status, PARTIAL)
        self.assertEqual(report.issue_lines(),
                         [f"{CALENDAR}: it holds more than the 300 events fetched; some may be missing"])


if __name__ == "__main__":
    unittest.main()
