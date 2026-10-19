import json
import os
import unittest
from datetime import date

from core.tooling import Report
from core.util.config import Config
from tests.agents.fitness_coach.lib.workout_fixture import NOW, ExerciseDir
from agents.fitness_coach.lib.rules import defaults
from agents.fitness_coach.lib.workout_data import load_data
from agents.fitness_coach.lib.workout_plan import accessories_per_group, build_details, build_setup, last_night_hours

REAL_PKM = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "pkm")
RULES = defaults()
COLUMNS = ("id", "name", "group", "station", "level", "days", "rx")


def rows(payload, role):
    return [dict(zip(COLUMNS, r.split(" | "))) for r in payload.get(role, [])]


class TestSleep(unittest.TestCase):
    def test_last_night_sums_todays_sessions_without_naps(self):
        sleep = {"sessions": [
            {"wake_date": "2026-10-16", "minutes_asleep": 300, "main_sleep": True},
            {"wake_date": "2026-10-16", "minutes_asleep": 40},                       # no flag: counted
            {"wake_date": "2026-10-16", "minutes_asleep": 30, "main_sleep": False},  # a nap
            {"wake_date": "2026-10-15", "minutes_asleep": 480},
        ]}
        self.assertEqual(last_night_hours(sleep, date(2026, 10, 16)), 5.7)
        self.assertIsNone(last_night_hours({"sessions": []}, date(2026, 10, 16)))
        self.assertIsNone(last_night_hours(None, date(2026, 10, 16)))


class TestSetup(unittest.TestCase):
    def setup(self, sleep=7.5, note=None, rules=RULES, report=None, **fixture):
        report = report or Report()
        with ExerciseDir(**fixture):
            return build_setup(load_data(report), NOW, sleep, note, rules, report)

    def test_snapshot(self):
        report = Report()
        payload = self.setup(recovery={"updated": "2026-10-16T06:00:00-07:00", "Quads": 30, "Chest": 100}, report=report)
        self.assertEqual(report.issues, [])
        self.assertEqual(payload["start"], "2026-10-16T08:00")
        self.assertEqual((payload["sleep_h"], payload["short_sleep"]), (7.5, False))
        self.assertEqual(payload["recovering"], {"Quads": 34})
        self.assertNotIn("Quads", payload["eligible"])
        self.assertNotIn("bb_back_squat", [r["id"] for r in rows(payload, "main")])
        self.assertEqual(payload["rows"], "id | name | group | station | level | days | rx")

    def test_short_or_unknown_sleep(self):
        self.assertTrue(self.setup(sleep=5.2)["short_sleep"])
        self.assertFalse(self.setup(sleep=5.2, rules={**RULES, "short_sleep_hours": 5.0})["short_sleep"])
        unknown = self.setup(sleep=None, note="unknown (Google Health: 503)")
        self.assertEqual((unknown["sleep_h"], unknown["sleep_note"]), (None, "unknown (Google Health: 503)"))
        self.assertNotIn("short_sleep", unknown)

    def test_rows_carry_days_and_the_prescription(self):
        payload = self.setup(progression=[
            {"id": "bb_back_squat", "last": "2026-10-13", "load": 135, "reps": 6, "next_load": 145},
            {"id": "bb_bench_press", "last": "2026-09-01", "load": 135, "reps": 6, "next_load": 140},
        ])
        mains = {r["id"]: r for r in rows(payload, "main")}
        self.assertEqual((mains["bb_back_squat"]["days"], mains["bb_back_squat"]["rx"]), ("3", "5-8 @ 145"))
        self.assertEqual((mains["bb_bench_press"]["days"], mains["bb_bench_press"]["rx"]), ("45", "5-8 @ 140 (-10%: 5-8 @ 125)"))
        self.assertEqual((mains["bb_deadlift"]["days"], mains["bb_deadlift"]["rx"]), ("-", "3-6 @ 45 (new)"))
        self.assertEqual(mains["bb_back_squat"]["station"], "Rack")

    def test_recent_mains_come_first(self):
        history = [{"date": "2026-10-14", "session": "2026-10-14T07:30", "id": "bb_hip_thrust",
                    "sets": [{"load": 135, "reps": 10, "signal": "completed"}]}]
        payload = self.setup(history=history)
        self.assertEqual(payload["recent_mains"], ["bb_hip_thrust since 2026-10-14"])
        self.assertEqual(rows(payload, "main")[0]["id"], "bb_hip_thrust")

    def test_accessories_rotate_and_spread_over_stations(self):
        history = [{"date": "2026-10-14", "session": "2026-10-14T07:30", "id": "db_goblet_squat",
                    "sets": [{"load": 25, "reps": 10, "signal": "completed"}]}]
        quads = [r for r in rows(self.setup(history=history), "accessory") if r["group"] == "Quads"]
        ids = [r["id"] for r in quads]
        self.assertEqual(len(quads), 4)                       # the budget split, capped at 4
        self.assertEqual(len({r["station"] for r in quads}), 4)
        self.assertIn("db_walking_lunge", ids)                # the Floor pick, goblet squat was done 2 days ago
        self.assertNotIn("db_goblet_squat", ids)
        self.assertNotIn("bb_zercher_squat", ids)             # advanced ranks last

    def test_finishers_core_first(self):
        finishers = rows(self.setup(), "finisher")
        self.assertEqual({r["group"] for r in finishers}, {"Core"})
        self.assertEqual(finishers[0]["id"], "hanging_leg_raise")

    def test_rules_size_and_rotate_the_lists(self):
        history = [{"date": "2026-10-14", "session": "2026-10-14T07:30", "id": "hanging_leg_raise",
                    "sets": [{"reps": 10, "signal": "completed"}]}]
        payload = self.setup(history=history, rules={**RULES, "max_mains": 2, "max_finishers": 2, "rotate_days": 1})
        self.assertEqual(len(payload["main"]), 2)
        self.assertEqual([r["id"] for r in rows(payload, "finisher")], ["hanging_leg_raise", "ab_wheel_rollout"])
        self.assertEqual((accessories_per_group(4, RULES), accessories_per_group(10, RULES)), (4, 2))
        self.assertEqual(accessories_per_group(10, {**RULES, "accessories_per_group_min": 3}), 3)

    def test_nothing_recovered(self):
        record = {"updated": "2026-10-16T07:00:00-07:00", **{g: 50 for g in (
            "Chest", "Back", "Shoulders", "Biceps", "Triceps", "Quads", "Hamstrings", "Glutes", "Calves", "Core")}}
        record["Back"] = 80
        payload = self.setup(recovery=record)
        self.assertEqual(payload["eligible"], [])
        self.assertIn("Back is next, at Fri 17:00", payload["note"])
        self.assertNotIn("main", payload)

    def test_explain_traces_each_rx(self):
        report = Report(explain=True)
        self.setup(report=report, progression=[
            {"id": "bb_back_squat", "last": "2026-10-13", "load": 135, "reps": 6, "next_load": 145},
            {"id": "bb_bench_press", "last": "2026-09-01", "load": 135, "reps": 6, "next_load": 140},
        ])
        self.assertIn("bb_back_squat rx 5-8 @ 145: progression.jsonl's next target (last done 2026-10-13, 3 d ago)",
                      report.traces)
        self.assertIn("bb_bench_press rx 5-8 @ 140: progression.jsonl's next target (last done 2026-09-01, 45 d ago); "
                      "over 21 d, -10% gives 5-8 @ 125", report.traces)
        self.assertIn("bb_deadlift rx 3-6 @ 45 (new): never done, a calibration start, at the lightest buildable weight, 45",
                      report.traces)

    @unittest.skipUnless(os.path.isdir(os.path.join(REAL_PKM, "wallet", "exercise", "library")), "no real library")
    def test_the_real_library_fits_a_voice_turn(self):
        """All ten groups eligible over the full library: still well under ~1.5k tokens."""
        Config().pkm_dir = os.path.abspath(REAL_PKM)   # read-only
        try:
            data = load_data(Report())
        finally:
            Config().pkm_dir = None
        data.recovery, data.history = None, []
        text = json.dumps(build_setup(data, NOW, 7.0, None, RULES, Report()), ensure_ascii=False, indent=1)
        self.assertLess(len(text) // 4, 1100)


class TestDetails(unittest.TestCase):
    def details(self, refs, progression=()):
        report = Report()
        with ExerciseDir(progression=progression):
            return build_details(load_data(report), refs, NOW, RULES, report), report

    def test_serving_details(self):
        payload, report = self.details(["bb_back_squat", "Barbell Hip Thrust", "plank", "squat"],
                                       [{"id": "bb_back_squat", "last": "2026-10-13", "load": 135, "reps": 6, "next_load": 145}])
        self.assertEqual(payload["start"], "2026-10-16T08:00")
        squat, thrust, plank = payload["exercises"]
        self.assertEqual(squat, {"id": "bb_back_squat", "name": "Barbell Back Squat", "rx": "5-8 @ 145",
                                 "cue": "Barbell Back Squat cue.", "media": "pkm/wallet/exercise/assets/bb_back_squat.gif",
                                 "loads": [125, 135, 145, 155, 165]})
        self.assertEqual(thrust["loads"], [45, 55, 65])   # new: climbs from the empty bar
        self.assertNotIn("loads", plank)
        self.assertEqual(report.status, "partial")
        self.assertTrue(report.issue_lines()[0].startswith("ids: unknown exercise 'squat'"), report.issue_lines())

    def test_a_gap_one_step_up_is_named(self):
        payload, _ = self.details(["bb_bench_press"], [{"id": "bb_bench_press", "last": "2026-10-13", "load": 95, "reps": 6, "next_load": 100}])
        [bench] = payload["exercises"]
        self.assertEqual(bench["loads"], [90, 95, 100, 115, 120])          # 🔼 from 100 jumps the gap to 115
        self.assertEqual(bench["gap"], [{"from": 100, "below": 102.5, "above": 115, "top_reps": 8}])

    def test_with_a_break_discount_either_start_works(self):
        payload, _ = self.details(["bb_bench_press"], [{"id": "bb_bench_press", "last": "2026-09-16", "load": 115, "reps": 6, "next_load": 120}])
        [bench] = payload["exercises"]
        self.assertEqual(bench["rx"], "5-8 @ 120 (-10%: 5-8 @ 102.5)")
        self.assertEqual(bench["loads"], [92.5, 97.5, 102.5, 115, 120, 125, 130])
        self.assertEqual(bench["gap"], [{"from": 102.5, "below": 102.5, "above": 115, "top_reps": 8}])

    def test_none_of_the_ids_is_in_the_library(self):
        payload, report = self.details(["squat"])
        self.assertEqual(payload["exercises"], [])
        self.assertEqual(report.status, "failed")
        self.assertIn("ids: none of the ids is in the library", report.issue_lines())


if __name__ == "__main__":
    unittest.main()
