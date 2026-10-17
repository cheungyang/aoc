"""The voice baseline and agent health reports in scripts/compare_token_stats.py."""
import os
import tempfile
import unittest

from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
from scripts.compare_token_stats import _agent_for_table, _percentile, get_health_stats, get_voice_stats


class TestVoiceStats(unittest.TestCase):

    def test_voice_tokens_and_turn_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory.db")
            store = SqliteSessionStore(db_path=db)
            session = "main:discord:general"
            store.append_token_usage(session, "m", 1000, 50, 0.0, surface="voice")
            store.append_token_usage(session, "m", 900, 40, 0.0, surface="text")
            store.append_token_usage("day-planner:tool:day-planning", "m", 2000, 80, 0.0, surface="voice")
            store.append_turn_trace(session, {
                "ttfa_ms": 4000, "deltas": {"stt_done": 1500, "first_token": 2000},
                "events": [{"kind": "model_call"}, {"kind": "tool_start"}, {"kind": "model_call"}],
            })
            store.append_turn_trace(session, {"ttfa_ms": None, "outcome": "cancelled", "deltas": {}, "events": []})

            stats = get_voice_stats(db_path=db)

        self.assertEqual(stats["calls"], 2, "text-surface rows are not voice")
        self.assertEqual((stats["in_tokens"], stats["out_tokens"]), (3000, 130))
        self.assertEqual(stats["agents"]["day-planner"], [1, 2000, 80])
        self.assertEqual(stats["turns"], 2)
        self.assertEqual(dict(stats["outcomes"]), {"answered": 1, "cancelled": 1})
        self.assertEqual(stats["ttfa"], [4000])
        self.assertEqual(stats["stages"]["stt_done"], [1500])
        self.assertEqual(sorted(stats["model_calls"]), [0, 2])

    def test_health_counts_round_trips_and_dead_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory.db")
            store = SqliteSessionStore(db_path=db)
            session = "home-steward:discord:home-automation"
            store.append_message(session, "user", "lights off")
            for _ in range(3):
                store.append_token_usage(session, "m", 100, 10, 0.0, surface="voice")
            store.append_message(session, "system", "Tool Output [0.1s]: <errors>Error: Agent home-steward "
                                                    "does not have permission to perform 'read' on path pkm/x</errors>")
            store.append_message(session, "user", "thanks")
            store.append_token_usage(session, "m", 100, 10, 0.0, surface="voice")
            store.append_message(session, "system", "Tool Output [0.0s]: <load_skill_response><errors>"
                                                    "Error loading skill: nope</errors></load_skill_response>")
            store.append_message(session, "user", "never answered")  # no model call: not a turn

            stats = get_health_stats(db_path=db)

        self.assertEqual(stats["calls"][("home-steward", "voice")], [3, 1])
        self.assertEqual(len(stats["denied"]["home-steward"]), 1)
        self.assertEqual(len(stats["skill_failures"]["home-steward"]), 1)

    def test_helpers(self):
        self.assertEqual(_agent_for_table("ctx_day_planner_tool_day_planning", ["day-planner", "main"]), "day-planner")
        self.assertEqual(_percentile([5, 1, 3], 50), 3)
        self.assertIsNone(_percentile([], 90))


if __name__ == "__main__":
    unittest.main()
