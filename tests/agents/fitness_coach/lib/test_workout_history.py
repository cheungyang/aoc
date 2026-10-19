import json
import unittest
from datetime import date, datetime

from core.tooling import Report
from tests.agents.fitness_coach.lib.workout_fixture import LIBRARY, TZ
from agents.fitness_coach.lib.rules import defaults
from agents.fitness_coach.lib.workout_history import (
    already_logged, history_line, parse_history, recent_mains, recently_done,
)

BY_ID = {e["id"]: e for entries in LIBRARY.values() for e in entries}
RULES = defaults()
TODAY = date(2026, 10, 16)


def entry(id, day, session_time="07:30", skipped=False):
    line = {"date": day, "session": f"{day}T{session_time}", "id": id}
    if skipped:
        line["skipped"] = True
    else:
        line["sets"] = [{"load": 100, "reps": 5, "signal": "completed"}]
    return json.dumps(line)


def parse(lines, report=None):
    return parse_history(lines, "workout_history.jsonl", report or Report())


class TestReading(unittest.TestCase):
    def test_bad_lines_come_back_raw(self):
        report = Report()
        entries = parse([entry("bb_back_squat", "2026-10-13"), "garbage", '{"id": "x", "date": "nope"}', "[]"], report)
        self.assertEqual([e["id"] for e in entries], ["bb_back_squat"])
        [issue] = report.issues
        self.assertEqual(issue.headline(), "workout_history.jsonl: 3 lines not recognized")
        self.assertEqual(issue.lines, ["garbage", '{"id": "x", "date": "nope"}', "[]"])

    def test_recent_mains_and_when_their_run_began(self):
        entries = parse([
            entry("bb_back_squat", "2026-07-01"),   # an earlier run, then a long pause
            entry("bb_back_squat", "2026-08-20"),
            entry("bb_back_squat", "2026-09-05"),
            entry("bb_back_squat", "2026-09-24"),
            entry("bb_back_squat", "2026-10-13"),
            entry("bb_bench_press", "2026-10-14"),
            entry("bb_deadlift", "2026-08-01"),     # older than 6 weeks
            entry("pull_ups", "2026-10-15", skipped=True),
            entry("db_goblet_squat", "2026-10-15"),  # not a main lift
        ])
        self.assertEqual(recent_mains(entries, BY_ID, TODAY, RULES), [
            {"id": "bb_bench_press", "since": "2026-10-14"},
            {"id": "bb_back_squat", "since": "2026-08-20"},
        ])

    def test_the_windows_come_from_rules(self):
        entries = parse([entry("bb_back_squat", "2026-09-05"), entry("bb_back_squat", "2026-09-24"),
                         entry("bb_deadlift", "2026-08-01")])
        rules = {**RULES, "main_lookback_days": 90, "main_run_gap_days": 14}
        self.assertEqual(recent_mains(entries, BY_ID, TODAY, rules), [
            {"id": "bb_back_squat", "since": "2026-09-24"},   # 19 days apart: a new run
            {"id": "bb_deadlift", "since": "2026-08-01"},
        ])

    def test_recently_done_counts_back_the_days_given(self):
        entries = parse([entry("db_goblet_squat", "2026-10-09"), entry("plank", "2026-10-08"),
                         entry("lever_squat", "2026-10-15", skipped=True)])
        self.assertEqual(recently_done(entries, TODAY, 7), {"db_goblet_squat"})
        self.assertEqual(recently_done(entries, TODAY, 8), {"db_goblet_squat", "plank"})

    def test_already_logged_window(self):
        entries = parse([entry("bb_back_squat", "2026-10-16", "07:30")])
        self.assertEqual(already_logged(entries, ["bb_back_squat"], datetime(2026, 10, 16, 8, 30, tzinfo=TZ)), ["bb_back_squat"])
        self.assertEqual(already_logged(entries, ["bb_back_squat"], datetime(2026, 10, 16, 9, 0, tzinfo=TZ)), ["bb_back_squat"])
        self.assertEqual(already_logged(entries, ["bb_back_squat"], datetime(2026, 10, 16, 9, 1, tzinfo=TZ)), [])
        self.assertEqual(already_logged(entries, ["bb_back_squat"], datetime(2026, 10, 16, 18, 0, tzinfo=TZ)), [])
        self.assertEqual(already_logged(entries, ["plank"], datetime(2026, 10, 16, 7, 30, tzinfo=TZ)), [])


class TestWriting(unittest.TestCase):
    START = datetime(2026, 10, 13, 7, 30, tzinfo=TZ)

    def test_exact_line_formats(self):
        squat = [{"load": 175.0, "reps": 6, "signal": "up"}, {"load": 185, "reps": 6, "signal": "completed"}]
        self.assertEqual(history_line(self.START, BY_ID["bb_back_squat"], squat, False),
                         '{"date":"2026-10-13","session":"2026-10-13T07:30","id":"bb_back_squat",'
                         '"sets":[{"load":175,"reps":6,"signal":"up"},{"load":185,"reps":6,"signal":"completed"}]}')
        self.assertEqual(history_line(self.START, BY_ID["plank"], [{"secs": 45, "signal": "completed"}], False),
                         '{"date":"2026-10-13","session":"2026-10-13T07:30","id":"plank","sets":[{"secs":45,"signal":"completed"}]}')
        self.assertEqual(history_line(self.START, BY_ID["pull_ups"], [{"reps": 8, "load": 10, "signal": "down"}], False),
                         '{"date":"2026-10-13","session":"2026-10-13T07:30","id":"pull_ups","sets":[{"reps":8,"signal":"down"}]}')
        self.assertEqual(history_line(self.START, BY_ID["lever_squat"], [], True),
                         '{"date":"2026-10-13","session":"2026-10-13T07:30","id":"lever_squat","skipped":true}')

    def test_cut_short_keeps_its_sets(self):
        line = json.loads(history_line(self.START, BY_ID["pull_ups"], [{"reps": 6, "signal": "completed"}], True))
        self.assertEqual((len(line["sets"]), line["skipped"]), (1, True))


if __name__ == "__main__":
    unittest.main()
