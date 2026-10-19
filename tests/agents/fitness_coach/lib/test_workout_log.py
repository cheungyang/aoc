import json
import unittest
from datetime import datetime, timedelta

from core.tooling import Report
from tests.agents.fitness_coach.lib.workout_fixture import NOW, TZ, ExerciseDir
from agents.fitness_coach.lib.rules import defaults
from agents.fitness_coach.lib.workout_data import load_data
from agents.fitness_coach.lib.workout_log import apply_session, plan_session

RULES = defaults()
START = datetime(2026, 10, 16, 7, 25, tzinfo=TZ)
SESSION = [
    {"id": "bb_back_squat", "sets": [{"load": 125, "reps": 6, "signal": "up"}, {"load": 135, "reps": 6, "signal": "completed"},
                                     {"load": 135, "reps": 6, "signal": "completed"}]},
    {"id": "bb_bench_press", "sets": [{"load": 135, "reps": 6, "signal": "completed"}] * 3},
    {"id": "lever_squat", "skipped": True},
    {"id": "plank", "sets": [{"secs": 45, "signal": "completed"}] * 3},
]
OLD_PROGRESSION = [
    {"id": "plank", "last": "2026-10-10", "secs": 40, "next_secs": 45},
    {"id": "bb_bench_press", "last": "2026-10-13", "load": 130, "reps": 6, "next_load": 135},
]
BENCH_AT_100 = [{"id": "bb_bench_press", "sets": [{"load": 100, "reps": 6, "signal": "completed"}] * 3}]


def plan(results=SESSION, start=START, minutes=30, now=NOW, rules=RULES, report=None):
    report = report or Report()
    return plan_session(load_data(report), results, start, minutes, now, rules, report)


class TestValidation(unittest.TestCase):
    def check(self, expected, **kwargs):
        with ExerciseDir():
            writes, errors = plan(**kwargs)
            self.assertIsNone(writes)
            self.assertTrue(any(expected in e for e in errors), errors)

    def test_every_problem_stops_every_write(self):
        self.check("Unknown exercise 'bb_squat'", results=[{"id": "bb_squat", "sets": []}])
        self.check("needs load and reps", results=[{"id": "bb_back_squat", "sets": [{"reps": 5, "signal": "completed"}]}])
        self.check("needs secs", results=[{"id": "plank", "sets": [{"reps": 5, "signal": "completed"}]}])
        self.check("listed twice", results=[{"id": "plank", "skipped": True}, {"id": "Plank", "skipped": True}])
        self.check("in the future", start=NOW + timedelta(hours=1))
        self.check("more than 24 hours", start=NOW - timedelta(days=2))
        self.check("at least 1", minutes=0)
        self.check("exercises is empty", results=[])
        self.check("next_load is only for loaded lifts", results=[{"id": "plank", "next_load": 10,
                                                                  "sets": [{"secs": 45, "signal": "completed"}]}])

    def test_the_start_window_is_24_hours_back_to_5_minutes_ahead(self):
        with ExerciseDir():
            self.assertEqual(plan(start=NOW + timedelta(minutes=5), minutes=1)[1], [])
            self.assertIn("in the future", plan(start=NOW + timedelta(minutes=6), minutes=1)[1][0])
            self.assertEqual(plan(start=NOW - timedelta(hours=24))[1], [])
            self.assertIn("more than 24 hours", plan(start=NOW - timedelta(hours=24, minutes=1))[1][0])

    def test_a_session_already_logged_is_refused(self):
        history = [{"date": "2026-10-16", "session": "2026-10-16T07:25", "id": "plank", "sets": [{"secs": 45, "signal": "completed"}]}]
        with ExerciseDir(history=history):
            writes, errors = plan()
        self.assertIsNone(writes)
        self.assertIn("Already logged for this session: plank", errors[0])

    def test_a_gap_without_the_coachs_pick_lists_the_choices(self):
        with ExerciseDir():
            writes, errors = plan(results=BENCH_AT_100 + SESSION[2:])
        self.assertIsNone(writes)
        self.assertEqual(errors, [
            "bb_bench_press: every set was ✅ at 100, and one step up (105) can't be built. Pass next_load for it: "
            "102.5, the weight below the gap (reps go up), or 115, the weight above it; the rep range tops at 8."])


class TestSession(unittest.TestCase):
    def test_writes(self):
        with ExerciseDir(progression=OLD_PROGRESSION) as d:
            writes, errors = plan()
            self.assertEqual(errors, [])
            written = []
            apply_session(writes, written)
            history = d.lines("workout_history.jsonl")
            progression = d.read("progression.jsonl").splitlines()
            recovery = json.loads(d.read("muscle_recovery.json"))

        self.assertEqual(written, ["history (+4 lines)", "progression", "recovery"])
        self.assertEqual([h["id"] for h in history], ["bb_back_squat", "bb_bench_press", "lever_squat", "plank"])
        self.assertEqual({h["session"] for h in history}, {"2026-10-16T07:25"})
        self.assertEqual(history[2], {"date": "2026-10-16", "session": "2026-10-16T07:25", "id": "lever_squat", "skipped": True})

        self.assertEqual(progression, [
            '{"id":"plank","last":"2026-10-16","secs":45,"next_secs":55}',                           # replaced in place
            '{"id":"bb_bench_press","last":"2026-10-16","load":135,"reps":6,"next_load":140}',
            '{"id":"bb_back_squat","last":"2026-10-16","load":135,"reps":6,"next_load":135}',     # new: appended
        ])
        self.assertEqual(recovery["updated"], "2026-10-16T08:00:00-07:00")
        self.assertEqual((recovery["Quads"], recovery["Chest"]), (10, 10))
        self.assertEqual((recovery["Glutes"], recovery["Shoulders"], recovery["Triceps"]), (70, 70, 70))
        self.assertEqual(recovery["Core"], 70)       # squat secondary and plank: -30 once
        self.assertEqual(recovery["Back"], 100)      # 60 a day ago, +48 capped

        self.assertEqual(writes.summary, [
            {"name": "Barbell Back Squat", "done": "6 @ 125, 6 @ 135, 6 @ 135", "next": "5-8 @ 135"},
            {"name": "Barbell Bench Press", "done": "3×6 @ 135", "next": "5-8 @ 140"},
            {"name": "Lever Arm Squat", "skipped": True},
            {"name": "Plank", "done": "3×45s", "next": "55s"},
        ])
        self.assertEqual((writes.start, writes.end), (START, START + timedelta(minutes=30)))
        self.assertTrue(writes.notes.startswith("Home workout: Barbell Back Squat 6 @ 125"))
        self.assertTrue(writes.notes.endswith("skipped: Lever Arm Squat"))
        self.assertEqual(writes.remarks, [])

    def test_the_coachs_pick_after_a_gap_and_its_snap(self):
        with ExerciseDir():
            writes, errors = plan(results=[{**BENCH_AT_100[0], "next_load": 102.5}])
            self.assertEqual(errors, [])
            self.assertEqual(writes.summary[0]["next"], "8 @ 102.5")
            self.assertEqual(writes.remarks, [])
            writes, _ = plan(results=[{**BENCH_AT_100[0], "next_load": 105}])
            self.assertEqual(writes.summary[0]["next"], "8 @ 102.5")
            self.assertEqual(writes.remarks, ["bb_bench_press: next_load 105 can't be built; 102.5, the weight below it."])
            writes, _ = plan(results=[{**BENCH_AT_100[0], "next_load": 30}])
            self.assertEqual(writes.remarks, ["bb_bench_press: next_load 30 can't be built; 45, the lightest weight."])

    def test_rules_change_the_targets_and_recovery(self):
        rules = {**RULES, "barbell_step_upper": 2.5, "worked_recovery": 25, "secs_step": 5}
        with ExerciseDir():
            writes, _ = plan(rules=rules)
        self.assertEqual([row.get("next") for row in writes.summary], ["5-8 @ 135", "5-8 @ 137.5", None, "50s"])
        self.assertEqual((writes.recovery["Quads"], writes.recovery["Chest"]), (25, 25))

    def test_explain_traces_how_recovery_and_targets_were_reached(self):
        report = Report(explain=True)
        with ExerciseDir():
            plan(results=SESSION[1:2], report=report)
        self.assertEqual(report.traces, [
            "bb_bench_press next load 140 from 135 × 6 (every set ✅)",
            "Back recovery 100 = min(100, 60 + 2/h × 24.0 h)",
            "Chest recovery 100 → 10 (worked as a primary)",
            "Shoulders recovery 100 → 70 (hit as a secondary or by a finisher)",
            "Triceps recovery 100 → 70 (hit as a secondary or by a finisher)",
        ])

    def test_end_is_trimmed_to_now(self):
        with ExerciseDir():
            writes, _ = plan(minutes=90)
        self.assertEqual(writes.end, NOW)
        self.assertIn("end trimmed", writes.remarks[0])

    def test_all_skipped_touches_only_history(self):
        with ExerciseDir(progression=OLD_PROGRESSION) as d:
            before = (d.read("progression.jsonl"), d.read("muscle_recovery.json"))
            writes, _ = plan(results=[{"id": "plank", "skipped": True}])
            written = []
            apply_session(writes, written)
            self.assertEqual((d.read("progression.jsonl"), d.read("muscle_recovery.json")), before)
            self.assertEqual(written, ["history (+1 lines)"])

    def test_history_without_a_final_newline_stays_one_line_per_entry(self):
        with ExerciseDir() as d:
            d.write("workout_history.jsonl", '{"date":"2026-10-01","session":"2026-10-01T07:00","id":"plank","skipped":true}')
            writes, _ = plan(results=[{"id": "plank", "skipped": True}])
            apply_session(writes, [])
            lines = d.read("workout_history.jsonl").split("\n")
        self.assertEqual(len(lines), 3)   # two entries and the final newline
        self.assertEqual(lines[-1], "")


if __name__ == "__main__":
    unittest.main()
