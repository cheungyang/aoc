import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from core.util.local_time import (
    LocalTimeError,
    clock_change,
    day_label,
    describe_days,
    describe_span,
    localize,
    parse_local,
    utc_text,
)

LA = ZoneInfo("America/Los_Angeles")


class TestParseLocal(unittest.TestCase):

    def test_date_only_is_a_date(self):
        self.assertEqual(parse_local("2026-10-24"), date(2026, 10, 24))

    def test_wall_times_come_back_naive(self):
        for text in ("2026-10-24 09:00", "2026-10-24T09:00", "2026-10-24 9:00", "2026-10-24T09:00:00"):
            with self.subTest(text=text):
                self.assertEqual(parse_local(text), datetime(2026, 10, 24, 9, 0))

    def test_an_offset_is_kept(self):
        self.assertEqual(parse_local("2026-10-24T16:00:00Z"), datetime(2026, 10, 24, 16, tzinfo=timezone.utc))

    def test_rejects_what_is_not_a_time(self):
        for text in ("", "Saturday 9am", "2026-13-01 09:00"):
            with self.subTest(text=text), self.assertRaises(LocalTimeError):
                parse_local(text)


class TestLocalize(unittest.TestCase):

    def test_summer_and_winter_offsets(self):
        self.assertEqual(utc_text(localize(datetime(2026, 10, 24, 9), LA)[0]), "2026-10-24T16:00:00Z")
        self.assertEqual(utc_text(localize(datetime(2026, 11, 7, 9), LA)[0]), "2026-11-07T17:00:00Z")

    def test_a_time_the_clocks_skip_is_refused(self):
        with self.assertRaisesRegex(LocalTimeError, "spring forward"):
            localize(datetime(2027, 3, 14, 2, 30), LA)

    def test_a_time_that_happens_twice_takes_the_first_and_says_so(self):
        moment, note = localize(datetime(2026, 11, 1, 1, 30), LA)
        self.assertEqual(utc_text(moment), "2026-11-01T08:30:00Z")
        self.assertIn("happens twice", note)
        self.assertIn("PDT", note)

    def test_an_aware_time_is_already_an_instant(self):
        moment, note = localize(datetime(2026, 10, 24, 16, tzinfo=timezone.utc), LA)
        self.assertEqual((moment.hour, note), (9, None))


class TestDescriptions(unittest.TestCase):

    def test_day_label(self):
        self.assertEqual(day_label(date(2026, 11, 1)), "Sun Nov 1")

    def test_span_within_a_day(self):
        start, end = datetime(2026, 10, 24, 9, tzinfo=LA), datetime(2026, 10, 24, 13, tzinfo=LA)
        self.assertEqual(describe_span(start, end, LA), "Sat Oct 24, 09:00–13:00 PDT")

    def test_span_across_the_clock_change_names_both_zones(self):
        start = localize(datetime(2026, 10, 31, 22), LA)[0]
        end = localize(datetime(2026, 11, 1, 3), LA)[0]
        self.assertEqual(describe_span(start, end, LA), "Sat Oct 31 22:00 PDT – Sun Nov 1 03:00 PST")

    def test_days(self):
        self.assertEqual(describe_days(date(2026, 10, 24), date(2026, 10, 24)), "Sat Oct 24 (all day)")
        self.assertEqual(describe_days(date(2026, 10, 24), date(2026, 10, 25)), "Sat Oct 24 – Sun Oct 25 (all day)")


class TestClockChange(unittest.TestCase):

    def span(self, first, last):
        return datetime.combine(first, datetime.min.time(), LA), datetime.combine(last, datetime.min.time(), LA)

    def test_fall_back(self):
        self.assertEqual(
            clock_change(*self.span(date(2026, 10, 31), date(2026, 11, 2)), LA),
            "Sun Nov 1: clocks fall back 1 h at 02:00 (PDT → PST)",
        )

    def test_spring_forward(self):
        self.assertEqual(
            clock_change(*self.span(date(2027, 3, 13), date(2027, 3, 15)), LA),
            "Sun Mar 14: clocks spring forward 1 h at 02:00 (PST → PDT)",
        )

    def test_no_change(self):
        self.assertIsNone(clock_change(*self.span(date(2026, 10, 24), date(2026, 10, 26)), LA))

    def test_half_hour_zone(self):
        lord_howe = ZoneInfo("Australia/Lord_Howe")
        start = datetime(2026, 10, 3, tzinfo=lord_howe)
        end = datetime(2026, 10, 5, tzinfo=lord_howe)
        self.assertIn("spring forward 30 min at 02:00", clock_change(start, end, lord_howe))


if __name__ == "__main__":
    unittest.main()
