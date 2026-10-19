import json
import os
import re
import unittest
from unittest.mock import patch

from core.loaders.tools_loader import ToolsLoader
from tests.helpers import envelope, execution_context
from tests.agents.fitness_coach.lib.workout_fixture import NOW, ExerciseDir
from agents.fitness_coach.tools import workout_setup as ws

SLEPT_5_5_H = {"sessions": [{"wake_date": "2026-10-16", "minutes_asleep": 330, "main_sleep": True}]}
SQUAT_3_DAYS_AGO = {"id": "bb_back_squat", "last": "2026-10-13", "load": 135, "reps": 6, "next_load": 145}


def run(args=None, sleep=None):
    def read_sleep():
        if isinstance(sleep, Exception):
            raise sleep
        return sleep or SLEPT_5_5_H

    with patch.object(ws, "_now", lambda: NOW), patch.object(ws, "_read_sleep", read_sleep), \
         execution_context(agent_id="fitness-coach"):
        return envelope(ws.workout_setup.invoke(args or {}))


class TestWorkoutSetup(unittest.TestCase):
    def test_setup_in_one_call(self):
        with ExerciseDir():
            out = run()
        self.assertEqual((out["status"], out["issues"], out["errors"]), ("ok", "None", "None"))
        data = json.loads(out["payload"])
        self.assertEqual((data["start"], data["sleep_h"], data["short_sleep"]), ("2026-10-16T08:00", 5.5, True))
        self.assertIn("bb_back_squat | Barbell Back Squat | Quads | Rack | intermediate | - | 5-8 @ 45 (new)", data["main"])
        self.assertTrue(data["accessory"] and data["finisher"])
        self.assertNotIn("cue", out["payload"])               # cue and media come at Let's Go
        self.assertNotIn("explain", out)

    def test_a_sleep_error_is_unknown_sleep_and_partial(self):
        with ExerciseDir():
            out = run(sleep=RuntimeError("credentials not found"))
        self.assertEqual(out["status"], "partial")
        self.assertEqual(out["issues"], "- Google Health: sleep couldn't be read (credentials not found)")
        data = json.loads(out["payload"])
        self.assertIsNone(data["sleep_h"])
        self.assertIn("credentials not found", data["sleep_note"])
        self.assertIn("main", data)

    def test_no_sleep_logged_today(self):
        with ExerciseDir():
            out = run(sleep={"sessions": [{"wake_date": "2026-10-15", "minutes_asleep": 400}]})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(json.loads(out["payload"])["sleep_note"], "unknown (no sleep logged for today yet)")

    def test_details_for_the_picks(self):
        with ExerciseDir(progression=[{"id": "bb_bench_press", "last": "2026-10-13", "load": 95, "reps": 6, "next_load": 100}]):
            out = run({"ids": ["bb_back_squat", "Plank", "bb_bench_press"]})
        self.assertEqual(out["status"], "ok")
        data = json.loads(out["payload"])
        self.assertEqual(data["start"], "2026-10-16T08:00")
        self.assertEqual([e["id"] for e in data["exercises"]], ["bb_back_squat", "plank", "bb_bench_press"])
        squat, plank, bench = data["exercises"]
        self.assertEqual(squat["loads"], [45, 55, 65])
        self.assertEqual((plank["cue"], plank["media"]), ("Plank cue.", "pkm/wallet/exercise/assets/plank.gif"))
        self.assertEqual(bench["gap"], [{"from": 100, "below": 102.5, "above": 115, "top_reps": 8}])
        self.assertNotIn("gap", squat)

    def test_unknown_ids(self):
        with ExerciseDir():
            some = run({"ids": ["bb_back_squat", "bb_back_sqaut"]})
            none = run({"ids": ["squat thing"]})
        self.assertEqual(some["status"], "partial")
        self.assertIn("- ids: unknown exercise 'bb_back_sqaut'; did you mean: bb_back_squat", some["issues"])
        self.assertEqual(len(json.loads(some["payload"])["exercises"]), 1)
        self.assertEqual((none["status"], none["payload"]), ("failed", ""))
        self.assertIn("- ids: none of the ids is in the library (required)", none["issues"])

    def test_explain_lists_the_files_and_how_each_number_was_reached(self):
        with ExerciseDir(progression=[SQUAT_3_DAYS_AGO]):
            out = run({"explain": True})
        explain = out["explain"]
        self.assertIn("- pkm/wallet/exercise/library/quads.json (modified ", explain)
        self.assertRegex(explain, re.escape("- pkm/wallet/exercise/muscle_recovery.json (modified ") + r"[^)]*, 10 records\)")
        self.assertIn("- Google Health (1 record)", explain)
        self.assertIn("- Back recovery 100 = min(100, 60 + 2/h × 24.0 h)", explain)
        self.assertIn("- bb_back_squat rx 5-8 @ 145: progression.jsonl's next target (last done 2026-10-13, 3 d ago)", explain)

    def test_rules_md_changes_the_answer(self):
        with ExerciseDir() as d:
            d.write_rules("- short_sleep_hours: 5\n- max_mains: 2\n")
            out = run()
        self.assertEqual(out["status"], "ok")
        data = json.loads(out["payload"])
        self.assertFalse(data["short_sleep"])
        self.assertEqual(len(data["main"]), 2)

    def test_a_rules_md_line_that_doesnt_read_keeps_the_default_and_says_so(self):
        with ExerciseDir() as d:
            d.write_rules("- short_sleep_hours: six\n")
            out = run()
        self.assertEqual(out["status"], "partial")
        self.assertIn("- pkm/agents/fitness-coach/RULES.md: line 1: 'short_sleep_hours: six' can't be read", out["issues"])
        self.assertTrue(json.loads(out["payload"])["short_sleep"])     # 5.5 h, under the default 6

    def test_missing_or_unreadable_files_are_reported(self):
        with ExerciseDir(recovery=None) as d:                         # no recovery file: a first run, not an issue
            os.remove(os.path.join(d.dir, "equipment.md"))
            d.write("progression.jsonl", "squat 185 x 6\n")
            out = run()
        self.assertEqual(out["status"], "partial")
        self.assertEqual(out["issues"], (
            "- pkm/wallet/exercise/equipment.md: not found: loads are not snapped to buildable weights, stations show as 'Any'\n"
            "- pkm/wallet/exercise/progression.jsonl: 1 line not recognized\n"
            "    squat 185 x 6"))
        self.assertIn("main", json.loads(out["payload"]))

    def test_no_library_fails(self):
        with ExerciseDir(library={}):
            out = run()
        self.assertEqual((out["status"], out["payload"]), ("failed", ""))
        self.assertEqual(out["issues"], "- pkm/wallet/exercise/library: no exercises found (required)")

    def test_an_agent_without_the_grant_is_refused(self):
        with ExerciseDir(), patch.object(ToolsLoader, "check_permission", return_value=False):
            out = run()
        self.assertEqual((out["status"], out["payload"]), ("failed", ""))
        self.assertIn("does not have permission to use workout_setup", out["errors"])

    def test_outside_an_agent_run(self):
        with ExerciseDir():
            self.assertIn("no active execution context", ws.workout_setup.invoke({}))


if __name__ == "__main__":
    unittest.main()
