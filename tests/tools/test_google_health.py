"""Tests for the agent-facing google_health tool.

No network: the HTTP client is replaced by `FakeClient`, and time and timezone
are pinned so date ranges and the write guards are deterministic.
"""
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tools import google_health as gh

TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 4, 20, 0, tzinfo=TZ)


def exercise_point(start, end, kind="RUNNING", pid="ex1", **metrics):
    return {
        "name": f"users/abc/dataTypes/exercise/dataPoints/{pid}",
        "dataSource": {"recordingMethod": "ACTIVELY_MEASURED"},
        "exercise": {
            "interval": {"startTime": start, "endTime": end},
            "exerciseType": kind,
            "displayName": kind.title(),
            "activeDuration": "1800s",
            "metricsSummary": metrics,
        },
    }


SLEEP = [
    {"name": "users/abc/dataTypes/sleep/dataPoints/s1", "sleep": {
        "interval": {"startTime": "2026-10-03T06:00:00Z", "endTime": "2026-10-03T13:30:00Z"},
        "metadata": {"mainSleep": True},
        "summary": {"minutesAsleep": "420", "minutesAwake": "30", "minutesInSleepPeriod": "450",
                    "stagesSummary": [{"type": "DEEP", "minutes": "80"}, {"type": "REM", "minutes": "90"}]}}},
    {"name": "users/abc/dataTypes/sleep/dataPoints/s2", "sleep": {
        "interval": {"startTime": "2026-10-03T21:00:00Z", "endTime": "2026-10-03T21:40:00Z"},
        "summary": {"minutesAsleep": "35"}}},
]

WEIGHT = [
    {"weight": {"sampleTime": {"physicalTime": "2026-10-04T14:00:00Z"}, "weightGrams": 80000.0}},
    {"weight": {"sampleTime": {"physicalTime": "2026-09-28T14:00:00Z"}, "weightGrams": 81500.0}},
]

ROLLUP = {"rollupDataPoints": [
    {"civilStartTime": {"date": {"year": 2026, "month": 10, "day": 3}},
     "activeMinutes": {"activeMinutesRollupByActivityLevel": [
         {"activityLevel": "LIGHT", "activeMinutesSum": "120"},
         {"activityLevel": "VIGOROUS", "activeMinutesSum": "25"}]}},
    {"civilStartTime": {"date": {"year": 2026, "month": 10, "day": 2}},
     "activeMinutes": {"activeMinutesRollupByActivityLevel": [
         {"activityLevel": "MODERATE", "activeMinutesSum": "40"}]}},
]}


class FakeClient:
    def __init__(self, exercises=None, pages=None):
        self.exercises = exercises if exercises is not None else []
        self.pages = pages
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params, None))
        if self.pages is not None:
            return self.pages.pop(0)
        if path.endswith("/exercise/dataPoints"):
            return {"dataPoints": self.exercises}
        if path.endswith("/sleep/dataPoints"):
            return {"dataPoints": SLEEP}
        if path.endswith("/weight/dataPoints"):
            return {"dataPoints": WEIGHT}
        return {}

    def post(self, path, json_body=None, retry=True):
        self.calls.append(("POST", path, json_body, retry))
        if path.endswith(":dailyRollUp"):
            return ROLLUP
        return {"name": "operations/op1", "done": True,
                "response": {"name": "users/abc/dataTypes/exercise/dataPoints/new1"}}


class Ctx:
    agent_id = "test-agent"


def run(instructions, client=None, ctx=None, allow=True, grant=None, writes=False):
    client = client or FakeClient()
    with patch.object(gh, "GoogleHealthClient", lambda *a, **k: client), \
         patch.object(gh, "try_context", lambda: ctx), \
         patch.object(gh, "_now", lambda: NOW), \
         patch.object(gh, "_tz", lambda: TZ), \
         patch.object(gh, "write_enabled", lambda: writes), \
         patch.object(gh.ToolsLoader, "check_permission", lambda *a, **k: allow), \
         patch.object(gh.ToolsLoader, "get_tool_permissions",
                      lambda *a, **k: grant if grant is not None else {"exercise": ["log_workout"]}):
        return gh.google_health.invoke({"instructions": instructions})


def payload(out):
    return out.split("<payload>")[1].split("</payload>")[0]


def errors(out):
    return out.split("<errors>")[1].split("</errors>")[0]


WORKOUT = {"action": "log_workout", "exercise_type": "strength training",
           "start_time": "2026-10-04T18:00", "end_time": "2026-10-04T18:45"}


class TestEnvelope(unittest.TestCase):
    def test_empty_instructions_are_rejected(self):
        self.assertIn("non-empty list", gh.google_health.invoke({"instructions": []}))

    def test_unknown_action_lists_available(self):
        out = run([{"action": "steps"}])
        self.assertIn("Unknown action", out)
        self.assertIn("list_exercises", out)

    def test_one_failure_does_not_abort_the_batch(self):
        out = run([{"action": "weight", "start_date": "nope"}, {"action": "exercise_types"}])
        self.assertIn("YYYY-MM-DD", errors(out))
        self.assertIn("STRENGTH_TRAINING", payload(out))

    def test_exercise_types_needs_no_credentials(self):
        def boom(*a, **k):
            raise gh.GoogleHealthError("credentials not found")
        with patch.object(gh, "GoogleHealthClient", boom), patch.object(gh, "try_context", lambda: None):
            out = gh.google_health.invoke({"instructions": [{"action": "exercise_types"}, {"action": "weight"}]})
        self.assertIn("PICKELBALL", payload(out))
        self.assertIn("credentials not found", errors(out))


class TestReads(unittest.TestCase):
    def test_active_minutes_summarises_per_day_and_totals(self):
        out = run([{"action": "active_minutes", "days": 7}])
        self.assertIn('"date": "2026-10-02"', out)
        self.assertIn('"total": 145', out)       # 120 light + 25 vigorous
        self.assertIn('"vigorous": 25', out)

    def test_active_minutes_chunks_to_14_days(self):
        client = FakeClient()
        run([{"action": "active_minutes", "days": 30}], client=client)
        rollups = [c for c in client.calls if c[1].endswith(":dailyRollUp")]
        self.assertEqual(len(rollups), 3)        # 14 + 14 + 2

    def test_range_ends_today_inclusive(self):
        client = FakeClient()
        run([{"action": "list_exercises", "days": 7}], client=client)
        flt = client.calls[0][2]["filter"]
        self.assertIn('>= "2026-09-28"', flt)
        self.assertIn('< "2026-10-05"', flt)

    def test_list_exercises_summarises(self):
        client = FakeClient(exercises=[exercise_point(
            "2026-10-04T14:00:00Z", "2026-10-04T14:30:00Z", distanceMillimeters=5_000_000.0,
            caloriesKcal=310.4, averageHeartRateBeatsPerMinute="150")])
        out = run([{"action": "list_exercises"}], client=client)
        self.assertIn('"distance_km": 5.0', out)
        self.assertIn('"duration_min": 30', out)
        self.assertIn('"start": "2026-10-04 07:00"', out)   # local time
        self.assertIn('"avg_heart_rate": 150', out)

    def test_list_exercises_paginates_up_to_limit(self):
        page = {"dataPoints": [exercise_point("2026-10-01T10:00:00Z", "2026-10-01T11:00:00Z")] * 25,
                "nextPageToken": "t"}
        client = FakeClient(pages=[dict(page), dict(page)])
        out = run([{"action": "list_exercises", "limit": 30}], client=client)
        self.assertEqual(len([c for c in client.calls if c[0] == "GET"]), 2)
        self.assertEqual(client.calls[1][2]["pageSize"], 5)
        self.assertIn('"truncated": true', out)

    def test_sleep_groups_by_wake_date(self):
        out = run([{"action": "sleep"}])
        self.assertIn('"wake_date": "2026-10-03"', out)
        self.assertIn('"minutes_asleep": 455', out)    # main sleep + nap
        self.assertIn('"deep": 80', out)

    def test_weight_reports_latest_and_change(self):
        out = run([{"action": "weight", "days": 30}])
        self.assertIn('"kg": 80.0', out)
        self.assertIn('"change_kg": -1.5', out)
        self.assertIn('"lb": 176.4', out)

    def test_bad_range_is_an_error(self):
        out = run([{"action": "sleep", "start_date": "2026-10-05", "end_date": "2026-10-01"}])
        self.assertIn("must be before", errors(out))


class TestPermissions(unittest.TestCase):
    def test_denied_action_is_refused_and_never_dispatched(self):
        client = FakeClient()
        out = run([{"action": "weight"}], client=client, ctx=Ctx(), allow=False)
        self.assertIn("lacks permission", out)
        self.assertEqual(client.calls, [])

    def test_permission_target_is_the_data_type(self):
        seen = []

        def capture(self, ctx, tool_id, action=None, path=None, **kw):
            seen.append((action, path))
            return True

        with patch.object(gh, "GoogleHealthClient", lambda *a, **k: FakeClient()), \
             patch.object(gh, "try_context", lambda: Ctx()), \
             patch.object(gh.ToolsLoader, "check_permission", capture):
            gh.google_health.invoke({"instructions": [{"action": "active_minutes"}, {"action": "sleep"}]})
        self.assertEqual(seen, [("active_minutes", "active-minutes"), ("sleep", "sleep")])

    def test_matcher(self):
        self.assertTrue(gh.PERMISSION_MATCHER("*", "exercise"))
        self.assertTrue(gh.PERMISSION_MATCHER("exercise", "exercise"))
        self.assertFalse(gh.PERMISSION_MATCHER("exercise", "sleep"))

    def test_bundles_are_valid_and_cover_the_vocabulary(self):
        from core.loaders import permission_bundles as pb
        pb.validate("google_health")
        covered = set()
        for name in pb.definitions_for("google_health"):
            covered.update(pb.expand_actions("google_health", [name]))
        self.assertEqual(covered, set(gh.ACTION_TARGETS))
        self.assertNotIn("log_workout", pb.expand_actions("google_health", ["@observe"]))


class TestWrites(unittest.TestCase):
    def test_refused_when_writes_disabled_and_never_reaches_the_api(self):
        client = FakeClient()
        out = run([WORKOUT], client=client, writes=False)
        self.assertIn("GOOGLE_HEALTH_WRITE_ENABLED", errors(out))
        self.assertEqual(client.calls, [])

    def test_blanket_grant_is_refused(self):
        client = FakeClient()
        out = run([WORKOUT], client=client, ctx=Ctx(), writes=True, grant={})
        self.assertIn("blanket grant", errors(out))
        self.assertEqual(client.calls, [])

    def test_successful_write_is_a_result_not_an_error(self):
        client = FakeClient()
        out = run([WORKOUT], client=client, writes=True)
        self.assertIn('<instruction_result action="log_workout"', payload(out))
        self.assertEqual(errors(out).strip(), "None")
        self.assertIn('"id": "new1"', out)

    def test_write_body_and_no_retry(self):
        client = FakeClient()
        run([WORKOUT], client=client, writes=True)
        method, path, body, retry = client.calls[-1]
        self.assertEqual((method, path), ("POST", "users/me/dataTypes/exercise/dataPoints"))
        self.assertFalse(retry)
        ex = body["exercise"]
        self.assertEqual(ex["exerciseType"], "STRENGTH_TRAINING")
        self.assertEqual(ex["interval"]["startTime"], "2026-10-04T18:00:00-07:00")
        self.assertEqual(ex["interval"]["startUtcOffset"], "-25200s")
        self.assertEqual(ex["activeDuration"], "2700s")
        self.assertEqual(body["dataSource"]["recordingMethod"], "MANUAL")

    def test_overlap_is_refused_unless_allowed(self):
        existing = exercise_point("2026-10-05T01:30:00Z", "2026-10-05T02:00:00Z")  # 18:30-19:00 local
        client = FakeClient(exercises=[existing])
        out = run([WORKOUT], client=client, writes=True)
        self.assertIn("overlaps", errors(out))
        self.assertFalse(any(c[0] == "POST" for c in client.calls))

        out = run([{**WORKOUT, "allow_overlap": True}], client=client, writes=True)
        self.assertIn('<instruction_result action="log_workout"', payload(out))

    def test_non_overlapping_neighbour_is_fine(self):
        existing = exercise_point("2026-10-05T02:00:00Z", "2026-10-05T02:30:00Z")  # 19:00 local
        out = run([WORKOUT], client=FakeClient(exercises=[existing]), writes=True)
        self.assertEqual(errors(out).strip(), "None")

    def test_validation(self):
        cases = {
            "end_time": ({"end_time": "2026-10-04T17:00"}, "must be after"),
            "future": ({"start_time": "2026-10-04T21:00", "end_time": "2026-10-04T22:00"}, "future"),
            "long": ({"start_time": "2026-10-02T08:00", "end_time": "2026-10-03T09:00"}, "24 hours"),
            "old": ({"start_time": "2025-10-04T18:00", "end_time": "2025-10-04T19:00"}, "days ago"),
            "type": ({"exercise_type": "jousting"}, "Unknown exercise_type"),
            "other": ({"exercise_type": "OTHER"}, "display_name"),
            "missing": ({"start_time": None}, "required"),
        }
        for name, (override, expected) in cases.items():
            client = FakeClient()
            out = run([{**WORKOUT, **override}], client=client, writes=True)
            self.assertIn(expected, errors(out), name)
            self.assertFalse(any(c[0] == "POST" for c in client.calls), name)

    def test_exercise_type_normalisation(self):
        self.assertEqual(gh.normalise_exercise_type("Run"), "RUNNING")
        self.assertEqual(gh.normalise_exercise_type("pickleball"), "PICKELBALL")
        self.assertEqual(gh.normalise_exercise_type("strength-training"), "STRENGTH_TRAINING")
        with self.assertRaises(gh.GuardRejection) as ctx:
            gh.normalise_exercise_type("runing")
        self.assertIn("RUNNING", str(ctx.exception))


class TestCredentials(unittest.TestCase):
    def test_world_readable_file_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            with open(path, "w") as f:
                f.write('{"client_id":"a","client_secret":"b","refresh_token":"c"}')
            os.chmod(path, 0o644)
            with self.assertRaises(gh.GoogleHealthError) as ctx:
                gh.read_credentials(path)
            self.assertIn("chmod 600", str(ctx.exception))
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            self.assertEqual(gh.read_credentials(path)["refresh_token"], "c")

    def test_missing_file_names_the_setup_command(self):
        with self.assertRaises(gh.GoogleHealthError) as ctx:
            gh.read_credentials("/nonexistent/creds.json")
        self.assertIn("google_health_auth.py", str(ctx.exception))

    def test_redact_strips_tokens(self):
        text = "Bearer ya29.abc refresh=1//0gXYZ secret=shh"
        out = gh.redact(text, "shh")
        for leaked in ("ya29.abc", "1//0gXYZ", "shh"):
            self.assertNotIn(leaked, out)

    def test_auth_script_requests_the_same_scopes(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "google_health_auth", os.path.join(gh.PROJECT_ROOT, "scripts", "google_health_auth.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.SCOPES, gh.SCOPES)


if __name__ == "__main__":
    unittest.main()
