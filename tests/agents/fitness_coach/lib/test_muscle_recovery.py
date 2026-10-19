import unittest
from datetime import datetime, timedelta

from core.tooling import Report
from tests.agents.fitness_coach.lib.workout_fixture import LIBRARY, NOW, RECOVERY, TZ
from agents.fitness_coach.lib.muscle_recovery import (
    after_session, check_record, current_recovery, eligible_groups, next_ready, recovery_record,
)
from agents.fitness_coach.lib.rules import defaults

BY_ID = {e["id"]: e for entries in LIBRARY.values() for e in entries}
RULES = defaults()
ALL_GROUPS = ("Chest", "Back", "Shoulders", "Biceps", "Triceps", "Quads", "Hamstrings", "Glutes", "Calves", "Core")


class TestCurrent(unittest.TestCase):
    def test_two_points_an_hour_capped_at_100(self):
        record = {"updated": "2026-10-16T02:30:00-07:00", "Quads": 10, "Back": 99, "Core": 0}
        values = current_recovery(record, NOW, RULES)          # 5.5 hours later
        self.assertEqual((values["Quads"], values["Back"], values["Core"]), (21, 100, 11))
        self.assertEqual(values["Chest"], 100)                 # missing group: recovered

    def test_the_rate_comes_from_rules(self):
        record = {"updated": "2026-10-16T02:30:00-07:00", "Quads": 10}
        self.assertEqual(current_recovery(record, NOW, {**RULES, "recovery_per_hour": 3.0})["Quads"], 26)

    def test_rounds_down_so_99_point_9_is_not_eligible(self):
        record = {"updated": (NOW - timedelta(minutes=147)).isoformat(), "Glutes": 95}   # +4.9
        self.assertEqual(current_recovery(record, NOW, RULES)["Glutes"], 99)
        self.assertNotIn("Glutes", eligible_groups(current_recovery(record, NOW, RULES)))

    def test_without_updated_the_stored_values_stand(self):
        self.assertEqual(current_recovery({"Back": 40}, NOW, RULES)["Back"], 40)
        self.assertEqual(current_recovery(None, NOW, RULES)["Back"], 100)

    def test_eligible_and_next_ready(self):
        values = current_recovery(RECOVERY, NOW, RULES)        # 24 h after: Back 60 -> 100
        self.assertEqual(len(eligible_groups(values)), 10)
        low = {g: 40 for g in values}
        low["Chest"] = 90
        group, when = next_ready(low, NOW, RULES)
        self.assertEqual((group, when), ("Chest", NOW + timedelta(hours=5)))
        self.assertEqual(next_ready(low, NOW, {**RULES, "recovery_per_hour": 5.0})[1], NOW + timedelta(hours=2))
        self.assertIsNone(next_ready(values, NOW, RULES))

    def test_explain_traces_each_group_below_100(self):
        report = Report()
        current_recovery({"updated": "2026-10-16T02:30:00-07:00", "Quads": 10, "Back": 100}, NOW, RULES, report)
        self.assertEqual(report.traces, ["Quads recovery 21 = min(100, 10 + 2/h × 5.5 h)"])


class TestCheckRecord(unittest.TestCase):
    def check(self, record):
        report = Report()
        return check_record(record, "muscle_recovery.json", report), report

    def test_a_good_record_has_no_issues(self):
        record, report = self.check(dict(RECOVERY))
        self.assertEqual((record, report.issues), (RECOVERY, []))

    def test_no_file_is_a_first_run(self):
        record, report = self.check(None)
        self.assertEqual((record, report.issues), (None, []))

    def test_what_it_cannot_read_is_named(self):
        record, report = self.check({"updated": "yesterday", "Chest": "full", "Back": 50, "note": "deload week"})
        self.assertEqual(record["Back"], 50)
        self.assertEqual(report.issue_lines(), [
            "muscle_recovery.json: 'updated' ('yesterday') isn't a time: the values are taken as stored, without recovery since",
            "muscle_recovery.json: Chest: 'full' isn't a number: counted as recovered",
            "muscle_recovery.json: 1 line not recognized",
        ])
        self.assertEqual(report.issues[-1].lines, ["\"note\": 'deload week'"])
        self.assertEqual(current_recovery(record, NOW, RULES)["Chest"], 100)

    def test_not_an_object(self):
        record, report = self.check([1, 2])
        self.assertIsNone(record)
        self.assertEqual(report.issue_lines(), ["muscle_recovery.json: not a JSON object: every group counts as recovered"])


class TestAfterSession(unittest.TestCase):
    def test_primaries_to_10_secondaries_and_finishers_minus_30_once(self):
        values = {g: 100 for g in ALL_GROUPS}
        values["Shoulders"] = 20
        done = [BY_ID["bb_back_squat"], BY_ID["bb_hip_thrust"], BY_ID["plank"]]
        result = after_session(values, done, RULES)
        self.assertEqual(result["Quads"], 10)
        self.assertEqual(result["Glutes"], 10)       # primary of the hip thrust, though also a squat secondary
        self.assertEqual(result["Hamstrings"], 70)   # secondary of both lifts: -30 once
        self.assertEqual(result["Core"], 70)         # finisher primary and secondary: -30 once
        self.assertEqual(result["Shoulders"], 0)     # floor 0
        self.assertEqual(result["Chest"], 100)

    def test_the_drops_come_from_rules(self):
        rules = {**RULES, "worked_recovery": 25, "secondary_hit": 20}
        result = after_session({g: 100 for g in ALL_GROUPS}, [BY_ID["bb_back_squat"]], rules)
        self.assertEqual((result["Quads"], result["Glutes"]), (25, 80))

    def test_a_worked_primary_never_goes_up(self):
        self.assertEqual(after_session({"Quads": 4}, [BY_ID["db_goblet_squat"]], RULES)["Quads"], 4)

    def test_explain_traces_each_change(self):
        report = Report()
        after_session({g: 100 for g in ALL_GROUPS}, [BY_ID["plank"]], RULES, report)
        self.assertEqual(report.traces, ["Shoulders recovery 100 → 70 (hit as a secondary or by a finisher)",
                                         "Core recovery 100 → 70 (hit as a secondary or by a finisher)"])


class TestRecord(unittest.TestCase):
    def test_keeps_file_order_and_other_keys(self):
        old = {"updated": "x", "Chest": 100, "note": "keep me", "Back": 50}
        values = current_recovery({}, NOW, RULES)
        values["Back"] = 10
        record = recovery_record(old, values, datetime(2026, 10, 16, 8, 5, 30, tzinfo=TZ))
        keys = list(record)
        self.assertEqual(keys[:4], ["updated", "Chest", "note", "Back"])
        self.assertEqual(record["updated"], "2026-10-16T08:05:30-07:00")
        self.assertEqual((record["note"], record["Back"]), ("keep me", 10))
        self.assertEqual(len(keys), 12)              # updated + note + 10 groups


if __name__ == "__main__":
    unittest.main()
