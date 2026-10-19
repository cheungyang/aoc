import json
import os
import unittest

from core.tooling import Report
from tests.agents.fitness_coach.lib.workout_fixture import ExerciseDir
from agents.fitness_coach.lib.exercise_library import (
    is_lower_body, load_library, parse_rep_range, resolve, suggest,
)


class TestLibrary(unittest.TestCase):
    def setUp(self):
        self.d = ExerciseDir().__enter__()
        self.addCleanup(self.d.__exit__, None, None, None)
        self.library, self.report = self.load()

    def load(self):
        report = Report()
        return load_library(os.path.join(self.d.dir, "library"), report), report

    def test_loads_every_group_file_in_group_order(self):
        self.assertEqual(self.report.issues, [])
        self.assertEqual(len(self.report.reads), 10)          # one per group file
        ids = list(self.library)
        self.assertEqual(ids[0], "bb_bench_press")          # Chest comes first
        self.assertLess(ids.index("bb_deadlift"), ids.index("bb_back_squat"))
        self.assertEqual([e["_order"] for e in self.library.values()], list(range(len(ids))))

    def test_a_broken_file_is_an_issue_not_a_crash(self):
        self.d.write("library/core.json", "[{oops")
        library, report = self.load()
        self.assertNotIn("plank", library)
        self.assertIn("bb_back_squat", library)
        [line] = report.issue_lines()
        self.assertTrue(line.startswith("pkm/wallet/exercise/library/core.json: not valid JSON ("), line)
        self.assertTrue(line.endswith("): no Core exercises"), line)

    def test_a_missing_or_misshapen_file_leaves_its_group_empty_and_says_so(self):
        os.remove(os.path.join(self.d.dir, "library", "calves.json"))
        self.d.write("library/biceps.json", '{"id": "bb_curl"}')
        _, report = self.load()
        self.assertEqual(report.issue_lines(), [
            "pkm/wallet/exercise/library/biceps.json: not a JSON array: no Biceps exercises",
            "pkm/wallet/exercise/library/calves.json: not found: no Calves exercises",
        ])
        self.assertEqual(report.status, "partial")

    def test_entries_it_cannot_use_are_named(self):
        plank = {"id": "plank", "name": "Plank", "primary_muscle": "Core", "role": "finisher", "tracking": "time"}
        self.d.write("library/core.json", json.dumps([
            plank,
            {"name": "Dead Bug", "primary_muscle": "Core"},                                   # no id
            {**plank, "name": "Plank again"},                                                 # repeated id
            {"id": "farmer_walk", "primary_muscle": "Forearms", "role": "carry", "tracking": "distance"},
        ]))
        library, report = self.load()
        self.assertEqual(library["plank"]["name"], "Plank")      # the first entry counts
        self.assertIn("farmer_walk", library)                    # kept, but never a candidate
        self.assertEqual(report.issue_lines(), [
            "pkm/wallet/exercise/library/core.json: 'plank' is listed again; the first entry counts",
            "pkm/wallet/exercise/library/core.json: farmer_walk: role 'carry' isn't main, accessory, finisher: never a candidate",
            "pkm/wallet/exercise/library/core.json: farmer_walk: primary_muscle 'Forearms' isn't a muscle group: never a candidate",
            "pkm/wallet/exercise/library/core.json: farmer_walk: tracking 'distance' isn't weight_reps, reps, time: counted as reps",
            "pkm/wallet/exercise/library/core.json: 1 line not recognized",
        ])
        self.assertEqual(report.issues[-1].lines, ['{"name": "Dead Bug", "primary_muscle": "Core"}'])

    def test_resolve_by_id_or_name(self):
        self.assertEqual(resolve(self.library, "bb_back_squat")["id"], "bb_back_squat")
        self.assertEqual(resolve(self.library, "barbell hip thrust")["id"], "bb_hip_thrust")
        self.assertEqual(resolve(self.library, "TRX-Assisted Pistol Squat")["id"], "trx_pistol_squat")
        self.assertIsNone(resolve(self.library, "squat"))
        self.assertIn("bb_back_squat", suggest(self.library, "bb_back_sqaut"))


class TestHelpers(unittest.TestCase):
    def test_rep_ranges(self):
        self.assertEqual(parse_rep_range("8-12/leg"), (8, 12, False, "/leg"))
        self.assertEqual(parse_rep_range("30-60s"), (30, 60, True, ""))
        self.assertEqual(parse_rep_range("15-30s/side"), (15, 30, True, "/side"))
        self.assertEqual(parse_rep_range("2-5 min"), (120, 300, True, ""))
        self.assertEqual(parse_rep_range("5"), (5, 5, False, ""))

    def test_lower_body_includes_every_deadlift(self):
        self.assertTrue(is_lower_body({"id": "bb_deadlift", "name": "Barbell Deadlift", "primary_muscle": "Back"}))
        self.assertTrue(is_lower_body({"id": "db_rdl", "name": "Dumbbell RDL", "primary_muscle": "Hamstrings"}))
        self.assertTrue(is_lower_body({"id": "bb_suitcase_deadlift", "name": "Suitcase Deadlift", "primary_muscle": "Core"}))
        self.assertTrue(is_lower_body({"id": "bb_hip_thrust", "name": "Barbell Hip Thrust", "primary_muscle": "Glutes"}))
        self.assertFalse(is_lower_body({"id": "bb_bench_press", "name": "Barbell Bench Press", "primary_muscle": "Chest"}))


if __name__ == "__main__":
    unittest.main()
