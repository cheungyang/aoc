"""The voice baseline and agent health reports in scripts/compare_token_stats.py."""
import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from core.storage.sessions import SessionStore
from scripts.compare_token_stats import (_agent_for_table, _percentile, get_health_stats, get_stats, get_voice_stats,
                                         main, print_voice_stats)


class TestVoiceStats(unittest.TestCase):

    def test_voice_tokens_and_turn_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory.db")
            store = SessionStore(db)
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

    def test_a_voice_window_can_end_before_now(self):
        """The first_token check: the weeks before shadow mode, against the weeks in it."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory.db")
            store = SessionStore(db)
            for at, ms in ((1000.0, 1800), (2000.0, 2600)):
                with mock.patch("core.storage.sessions.time.time", return_value=at):
                    store.append_turn_trace("main:discord:general",
                                            {"ttfa_ms": ms, "deltas": {"first_token": ms}, "events": []})

            before = get_voice_stats(db_path=db, since_ts=0, until_ts=1500)
            during = get_voice_stats(db_path=db, since_ts=1500)

        self.assertEqual(before["stages"]["first_token"], [1800])
        self.assertEqual(during["stages"]["first_token"], [2600])

    LIGHT, AGENT = {"kind": "model_call", "light": True}, {"kind": "model_call"}
    LIGHT_TURNS = [
        [LIGHT, {"kind": "handover", "agent": "main"}, AGENT, AGENT],
        [LIGHT, {"kind": "handover", "depth": "low", "marker": "found"}, AGENT],   # traced before the light call
                                                                                   # only said the first line
        [LIGHT, {"kind": "light_fallback", "reason": "deadline", "ms": 1500}, AGENT],
        [LIGHT, {"kind": "light_fallback", "reason": "refused", "ms": 80}, AGENT, AGENT],
        [LIGHT],                                                    # answered on the light call, before it couldn't
        [AGENT],                                                    # on the agent's own model only
        [],                                                         # a voice hook: no model call
    ]

    def _light_turns_db(self, tmp):
        db = os.path.join(tmp, "memory.db")
        store = SessionStore(db)
        for events in self.LIGHT_TURNS:
            store.append_turn_trace("main:discord:general", {"ttfa_ms": 900, "deltas": {}, "events": events})
        return db

    def test_light_first_calls_their_hand_overs_and_fallbacks(self):
        """Whether the light first call is live: the turns that made one, how many
        handed over, and how many fell back to the agent's model, and why."""
        with tempfile.TemporaryDirectory() as tmp:
            stats = get_voice_stats(db_path=self._light_turns_db(tmp))

        self.assertEqual(stats["light_turns"], 5)
        self.assertEqual(stats["handovers"], 2)
        self.assertEqual(dict(stats["fallbacks"]), {"deadline": 1, "refused": 1})
        self.assertEqual(sorted(stats["model_calls"]), [0, 1, 1, 2, 2, 3, 3])

    def test_the_report_says_whether_the_light_call_is_live(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(out):
            print_voice_stats(db_path=self._light_turns_db(tmp))

        printed = out.getvalue()
        self.assertRegex(printed, r"Light first calls +\| +5   of 6 voice turns that called a model\n")
        self.assertRegex(printed, r"Handed over +\| +2   fell back 2 \{'deadline': 1, 'refused': 1\}\n")

    def test_health_counts_round_trips_and_dead_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory.db")
            store = SessionStore(db)
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

    def test_token_periods_include_archived_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory.db")
            store = SessionStore(db)
            store.append_token_usage("day-planner:tool:day-planning", "m", 100, 10, 50.0, execution_time=2.0)
            store.archive_session("day-planner:tool:day-planning")
            store.append_token_usage("main:discord:general", "m", 300, 30, 0.0)

            historical, current = get_stats(db_path=db, cutoff_ts=0)

        self.assertEqual(historical["calls"], 0)
        self.assertEqual((current["calls"], current["in_tokens"], current["out_tokens"]), (2, 400, 40))
        self.assertEqual(current["cached_weighted"], 50.0)
        self.assertEqual((current["exec_time"], current["exec_calls"]), (2.0, 1))
        self.assertEqual(dict(current["agents"]), {"day-planner": [110, 1, 100, 50.0], "main": [330, 1, 300, 0.0]})

    def test_the_reports_read_the_tables_moved_to_archive_db(self):
        """Startup moves archived sessions to archive.db beside memory.db (SessionStore.archive_inactive)."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory.db")
            store = SessionStore(db)
            session = "home-steward:discord:home-automation"
            store.append_message(session, "user", "lights off")
            store.append_token_usage(session, "m", 100, 10, 0.0, surface="voice")
            store.append_turn_trace(session, {"ttfa_ms": 1200, "deltas": {}, "events": []})
            store.archive_session(session)
            self.assertEqual(store.archive_inactive([]), (1, 3))
            store.append_token_usage("main:discord:general", "m", 300, 30, 0.0, surface="voice")

            _, current = get_stats(db_path=db, cutoff_ts=0)
            voice = get_voice_stats(db_path=db)
            health = get_health_stats(db_path=db)

        self.assertEqual((current["calls"], current["in_tokens"]), (2, 400))
        self.assertEqual((voice["calls"], voice["turns"], voice["ttfa"]), (2, 1, [1200]))
        self.assertEqual(health["calls"][("home-steward", "voice")], [1])

    def test_a_missing_db_is_reported_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory.db")
            self.assertEqual(get_stats(db_path=db), (None, None))
            self.assertIsNone(get_voice_stats(db_path=db))
            self.assertIsNone(get_health_stats(db_path=db))
            self.assertFalse(os.path.exists(db))

    def test_helpers(self):
        self.assertEqual(_agent_for_table("ctx_day_planner_tool_day_planning", ["day-planner", "main"]), "day-planner")
        self.assertEqual(_percentile([5, 1, 3], 50), 3)
        self.assertIsNone(_percentile([], 90))


class TestTheCommandLine(unittest.TestCase):

    def test_days_looks_back_for_the_voice_report_too(self):
        """`!python compare_token_stats.py --voice --days 1`: the last day's voice turns."""
        with mock.patch("scripts.compare_token_stats.print_voice_stats") as voice:
            self.assertEqual(main(["--voice", "--days", "1"]), 0)
        self.assertAlmostEqual(voice.call_args.kwargs["since_ts"], time.time() - 86400, delta=60)
        self.assertIsNone(voice.call_args.kwargs["until_ts"])

    def test_otherwise_the_voice_report_reads_all_of_it_or_since_until(self):
        """--since wins over --days."""
        with mock.patch("scripts.compare_token_stats.print_voice_stats") as voice:
            main(["--voice"])
            main(["--voice", "--since", "1000", "--until", "2000"])
            main(["--voice", "--since", "1000", "--days", "1"])
        self.assertEqual([c.kwargs for c in voice.call_args_list], [{"since_ts": 0, "until_ts": None},
                                                                    {"since_ts": 1000, "until_ts": 2000},
                                                                    {"since_ts": 1000, "until_ts": None}])

    def test_the_health_report_still_looks_back_a_week(self):
        with mock.patch("scripts.compare_token_stats.print_health_stats") as health:
            self.assertEqual(main(["--health"]), 0)
            main(["--health", "--days", "2"])
        week, two_days = (c.kwargs["since_ts"] for c in health.call_args_list)
        self.assertAlmostEqual(week, time.time() - 7 * 86400, delta=60)
        self.assertAlmostEqual(two_days, time.time() - 2 * 86400, delta=60)


if __name__ == "__main__":
    unittest.main()
