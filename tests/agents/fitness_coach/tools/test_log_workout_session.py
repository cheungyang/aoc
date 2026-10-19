import json
import unittest
from contextlib import ExitStack
from datetime import timezone
from unittest.mock import patch

from pydantic import ValidationError

from core.loaders.tools_loader import ToolsLoader
from tests.helpers import envelope, execution_context
from tests.agents.fitness_coach.lib.workout_fixture import NOW, TZ, ExerciseDir
from tools import google_health as gh
from agents.fitness_coach.tools import log_workout_session as lws

SESSION = [
    {"id": "bb_back_squat", "sets": [{"load": 135, "reps": 6, "signal": "completed"}] * 3},
    {"id": "lever_squat", "skipped": True},
    {"id": "plank", "sets": [{"secs": 45, "signal": "completed"}] * 3},
]
BENCH_AT_100 = {"id": "bb_bench_press", "sets": [{"load": 100, "reps": 6, "signal": "completed"}] * 3}


class FakeClient:
    def __init__(self, existing=()):
        self.existing, self.calls = list(existing), []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params, None))
        return {"dataPoints": self.existing}

    def post(self, path, json_body=None, retry=True):
        self.calls.append(("POST", path, json_body, retry))
        return {"name": "operations/op1", "done": True, "response": {"name": "users/me/dataTypes/exercise/dataPoints/new1"}}


def run(exercises=SESSION, start="2026-10-16T07:25", minutes=32, client=None, writes=True, denied=(),
        in_a_run=True, **extra):
    """One call as fitness-coach, with its real grants; `denied` refuses those actions on top."""
    client = client or FakeClient()
    real_check = ToolsLoader.check_permission

    def check(self, ctx, tool, action=None, path=None, **kwargs):
        return action not in denied and real_check(self, ctx, tool, action, path, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(patch.object(lws, "_now", lambda: NOW))
        stack.enter_context(patch.object(ToolsLoader, "check_permission", check))
        stack.enter_context(patch.object(gh, "GoogleHealthClient", lambda *a, **k: client))
        stack.enter_context(patch.object(gh, "_now", lambda: NOW.astimezone(timezone.utc)))
        stack.enter_context(patch.object(gh, "_tz", lambda: TZ))
        stack.enter_context(patch.object(gh, "write_enabled", lambda: writes))
        if in_a_run:
            stack.enter_context(execution_context(agent_id="fitness-coach"))
        out = lws.log_workout_session.invoke({"exercises": exercises, "start": start, "duration_minutes": minutes, **extra})
    return envelope(out), client


class TestLogWorkoutSession(unittest.TestCase):
    def test_every_write_and_the_google_health_log_in_one_call(self):
        with ExerciseDir() as d:
            out, client = run()
            history = d.lines("workout_history.jsonl")
            progression = d.lines("progression.jsonl")
        self.assertEqual((out["status"], out["issues"], out["errors"]), ("ok", "None", "None"))
        data = json.loads(out["payload"])
        self.assertEqual(data["session"], "2026-10-16T07:25, 32 min")
        self.assertEqual(data["exercises"][0], {"name": "Barbell Back Squat", "done": "3×6 @ 135", "next": "5-8 @ 145"})
        self.assertEqual(data["saved"], "history (+3 lines), progression, recovery")
        self.assertEqual(data["google_health"], "logged 07:25-07:57")
        self.assertNotIn("remarks", data)
        self.assertEqual(len(history), 3)
        self.assertEqual([p["id"] for p in progression], ["bb_back_squat", "plank"])

        method, path, body, retry = client.calls[-1]
        self.assertEqual((method, path, retry), ("POST", "users/me/dataTypes/exercise/dataPoints", False))
        exercise = body["exercise"]
        self.assertEqual(exercise["exerciseType"], "STRENGTH_TRAINING")
        self.assertEqual(exercise["interval"]["startTime"], "2026-10-16T07:25:00-07:00")
        self.assertEqual(exercise["interval"]["endTime"], "2026-10-16T07:57:00-07:00")
        self.assertIn("Barbell Back Squat 3×6 @ 135", exercise["notes"])

    def test_a_google_health_failure_keeps_the_files(self):
        with ExerciseDir() as d:
            out, client = run(writes=False)
            self.assertEqual(len(d.lines("workout_history.jsonl")), 3)
        self.assertEqual(out["status"], "ok")
        self.assertIn("not logged: Writing to Google Health is disabled", json.loads(out["payload"])["google_health"])
        self.assertFalse(any(c[0] == "POST" for c in client.calls))

    def test_a_session_the_watch_already_recorded_is_not_logged_twice(self):
        watch = {"exercise": {"interval": {"startTime": "2026-10-16T14:30:00Z", "endTime": "2026-10-16T15:00:00Z"}}}
        with ExerciseDir():
            out, client = run(client=FakeClient([watch]))
        self.assertIn("overlaps an existing session", json.loads(out["payload"])["google_health"])

    def test_the_same_session_twice_writes_nothing_the_second_time(self):
        with ExerciseDir() as d:
            run()
            out, client = run(start="2026-10-16T07:40")              # within 90 minutes
            self.assertEqual(len(d.lines("workout_history.jsonl")), 3)
        self.assertEqual(out["status"], "failed")
        self.assertIn("Already logged for this session: bb_back_squat, lever_squat, plank", out["errors"])
        self.assertEqual(client.calls, [])

    def test_refused_writes_write_nothing(self):
        """The voice replay refuses every action of a tool whose READ_ACTIONS is empty."""
        self.assertEqual(tuple(lws.READ_ACTIONS), ())
        with ExerciseDir() as d:
            out, client = run(denied=("write", "log_workout"))
            self.assertEqual(d.read("workout_history.jsonl"), "")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["errors"], "Error: Agent 'fitness-coach' may not write "
                                        "pkm/wallet/exercise/workout_history.jsonl; nothing was written.")
        self.assertEqual(client.calls, [])

    def test_without_the_log_workout_grant_the_files_are_still_saved(self):
        with ExerciseDir() as d:
            out, client = run(denied=("log_workout",))
            self.assertEqual(len(d.lines("workout_history.jsonl")), 3)
        self.assertEqual(json.loads(out["payload"])["google_health"], "not logged: no permission")
        self.assertEqual(client.calls, [])

    def test_bad_input_writes_nothing(self):
        with ExerciseDir() as d:
            out, _ = run(exercises=[{"id": "bb_back_squat", "sets": [{"reps": 6, "signal": "completed"}]}])
            self.assertEqual(d.read("workout_history.jsonl"), "")
        self.assertEqual((out["status"], out["payload"]), ("failed", ""))
        self.assertTrue(out["errors"].startswith("Error: Nothing was written.\n"))
        self.assertIn("needs load and reps", out["errors"])

    def test_a_gap_needs_next_load_then_takes_it(self):
        with ExerciseDir() as d:
            out, client = run(exercises=[BENCH_AT_100])
            self.assertEqual(d.read("workout_history.jsonl"), "")
            self.assertEqual(out["status"], "failed")
            self.assertIn("bb_bench_press: every set was ✅ at 100, and one step up (105) can't be built. "
                          "Pass next_load for it: 102.5, the weight below the gap (reps go up), or 115", out["errors"])
            self.assertEqual(client.calls, [])

            out, _ = run(exercises=[{**BENCH_AT_100, "next_load": 102.5}])
            progression = d.lines("progression.jsonl")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(json.loads(out["payload"])["exercises"][0]["next"], "8 @ 102.5")
        self.assertEqual(progression, [{"id": "bb_bench_press", "last": "2026-10-16", "load": 100, "reps": 6,
                                        "next_load": 102.5, "next_reps": 8}])

    def test_a_next_load_that_cant_be_built_is_snapped_down_and_said(self):
        with ExerciseDir():
            out, _ = run(exercises=[{**BENCH_AT_100, "next_load": 105}])
        data = json.loads(out["payload"])
        self.assertEqual(data["exercises"][0]["next"], "8 @ 102.5")
        self.assertEqual(data["remarks"], ["bb_bench_press: next_load 105 can't be built; 102.5, the weight below it."])

    def test_rules_md_changes_the_targets_and_recovery(self):
        with ExerciseDir() as d:
            d.write_rules("- secs_step: 5\n- worked_recovery: 25\n")
            out, _ = run()
            recovery = json.loads(d.read("muscle_recovery.json"))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(json.loads(out["payload"])["exercises"][2]["next"], "50s")
        self.assertEqual(recovery["Quads"], 25)

    def test_an_unreadable_progression_line_is_reported_and_kept(self):
        with ExerciseDir() as d:
            d.write("progression.jsonl", "squat 185 x 6\n")
            out, _ = run()
            lines = d.read("progression.jsonl").splitlines()
        self.assertEqual(out["status"], "partial")
        self.assertEqual(out["issues"], "- pkm/wallet/exercise/progression.jsonl: 1 line not recognized\n    squat 185 x 6")
        self.assertEqual(lines[0], "squat 185 x 6")
        self.assertEqual(len(lines), 3)

    def test_explain_lists_what_was_read_and_how_each_number_was_reached(self):
        with ExerciseDir():
            out, _ = run(exercises=SESSION[:1], explain=True)
        explain = out["explain"]
        self.assertIn("- pkm/wallet/exercise/progression.jsonl (modified ", explain)
        self.assertIn("- bb_back_squat next load 145 from 135 × 6 (every set ✅)", explain)
        self.assertIn("- Back recovery 100 = min(100, 60 + 2/h × 24.0 h)", explain)
        self.assertIn("- Quads recovery 100 → 10 (worked as a primary)", explain)

    def test_fields_are_typed(self):
        with ExerciseDir():
            with self.assertRaises(ValidationError):
                run(exercises=[{"id": "plank", "sets": [{"weight": 10, "reps": 5, "signal": "completed"}]}])
            with self.assertRaises(ValidationError):
                run(exercises=[{"id": "plank", "sets": [{"secs": 30, "signal": "done"}]}])

    def test_start_as_clock_time_and_bad_start(self):
        with ExerciseDir():
            out, _ = run(start="07:25")
            self.assertEqual(json.loads(out["payload"])["session"], "2026-10-16T07:25, 32 min")
        with ExerciseDir():
            out, _ = run(start="this morning")
        self.assertEqual(out["errors"], "Error: start must be YYYY-MM-DDTHH:MM (local), got 'this morning'.")

    def test_needs_an_agent_run(self):
        with ExerciseDir() as d:
            out, _ = run(in_a_run=False)
            self.assertEqual(d.read("workout_history.jsonl"), "")
        self.assertIn("no active execution context", out["errors"])


if __name__ == "__main__":
    unittest.main()
