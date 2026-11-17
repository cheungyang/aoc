"""LearningStore: the learning loop's tables, and the SELECT-only reads its detectors make."""
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from core.storage.follow_ups import FollowUpStore
from core.storage.learning import COMMAND, NOT_REAL, REAL, REFUSAL, LearningStore, archived_at
from core.storage.sessions import SessionStore
from core.storage.signals import SignalStore


def event(**overrides):
    """A friction_events row as a detector returns it."""
    row = {"at": 100.0, "detector": "correction", "source": "turn_ledger", "source_id": "1",
           "agent_id": "day-planner", "session": "s1", "category": "misread_request",
           "evidence": "no, only the first three", "evidence_role": "user", "severity": 2, "cost_turns": 1,
           "cost_seconds": None, "details": {"turn": 1}}
    row.update(overrides)
    return row


class _StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "memory.db")
        self.store = LearningStore(self.path)

    def sql(self, statement, params=()):
        with sqlite3.connect(self.path) as conn:
            conn.execute(statement, params)


class TestFrictionEvents(_StoreTest):

    def test_a_rerun_adds_nothing_twice(self):
        self.assertEqual(self.store.record_friction([event(), event(source_id="2")], found_at=5.0), 2)
        self.assertEqual(self.store.record_friction([event(), event(source_id="3")]), 1)
        self.assertEqual(len(self.store.friction(0)), 3)
        self.assertEqual(self.store.friction(0)[-1]["found_at"], 5.0)

    def test_the_same_rows_from_another_detector_are_another_event(self):
        self.store.record_friction([event()])
        self.assertEqual(self.store.record_friction([event(detector="rephrase")]), 1)

    def test_friction_is_newest_first_and_filtered(self):
        self.store.record_friction([event(at=10.0, source_id="a"),
                                    event(at=20.0, source_id="b", agent_id="main", detector="refusal")])
        self.assertEqual([e["source_id"] for e in self.store.friction(0)], ["b", "a"])
        self.assertEqual([e["source_id"] for e in self.store.friction(0, agent_id="main")], ["b"])
        self.assertEqual([e["source_id"] for e in self.store.friction(0, detector="correction")], ["a"])
        self.assertEqual([e["source_id"] for e in self.store.friction(15)], ["b"])
        self.assertEqual([e["source_id"] for e in self.store.friction(0, limit=1)], ["b"])
        self.assertEqual(self.store.friction(0)[1]["details"], {"turn": 1}, "JSON comes back decoded")

    def test_labels_and_precision(self):
        self.store.record_friction([event(source_id=str(i)) for i in range(3)]
                                   + [event(detector="refusal", source_id="r")])
        by_source = {e["source_id"]: e["id"] for e in self.store.friction(0)}
        self.assertTrue(self.store.label_friction(by_source["0"], REAL, at=7.0))
        self.assertTrue(self.store.label_friction(by_source["1"], NOT_REAL))
        self.assertTrue(self.store.label_friction(by_source["r"], NOT_REAL))
        self.assertFalse(self.store.label_friction(999, REAL), "no such event")
        with self.assertRaises(ValueError):
            self.store.label_friction(by_source["2"], "maybe")
        self.assertEqual(self.store.precision(), {"correction": (1, 2), "refusal": (0, 1)})
        labeled = [e for e in self.store.friction(0) if e["id"] == by_source["0"]][0]
        self.assertEqual((labeled["label"], labeled["labeled_at"]), (REAL, 7.0))

    def test_summary_per_agent_and_detector(self):
        self.store.record_friction([event(source_id="a", severity=2), event(source_id="b", severity=3),
                                    event(source_id="c", agent_id="main", at=1.0)])
        summary = {(r["agent_id"], r["detector"]): (r["events"], r["weight"]) for r in self.store.friction_summary(50)}
        self.assertEqual(summary, {("day-planner", "correction"): (2, 5)})


class TestEventsVersionsLedgerRuns(_StoreTest):

    def test_events_by_kind_and_window(self):
        self.store.record_event(COMMAND, at=10.0, agent_id="main", name="kill", detail={"args": "3"})
        self.store.record_event(REFUSAL, at=20.0, name="filesystem")
        self.store.record_event(COMMAND, at=30.0, name="new")
        self.assertEqual([e["name"] for e in self.store.events(COMMAND, 0)], ["kill", "new"])
        self.assertEqual([e["name"] for e in self.store.events(COMMAND, 0, until=30.0)], ["kill"])
        self.assertEqual([e["name"] for e in self.store.events(COMMAND, 15)], ["new"])
        self.assertEqual(self.store.events(COMMAND, 0)[0]["detail"], {"args": "3"})

    def test_a_version_is_noted_once_and_files_are_kept_by_hash(self):
        self.assertTrue(self.store.note_prompt_version("a", "v1.m1", {"agents/a/AGENTS.md": "sha1"},
                                                       static_chars=5, memory_chars=2, at=1.0))
        self.assertFalse(self.store.note_prompt_version("a", "v1.m1", {}, at=2.0))
        self.store.note_prompt_version("a", "v2.m1", {}, at=3.0)
        versions = self.store.prompt_versions("a")
        self.assertEqual([v["version"] for v in versions], ["v2.m1", "v1.m1"])
        self.assertEqual((versions[1]["files"], versions[1]["static_chars"]), ({"agents/a/AGENTS.md": "sha1"}, 5))
        self.assertEqual(self.store.keep_prompt_files({"sha1": ("agents/a/AGENTS.md", "text")}), 1)
        self.assertEqual(self.store.keep_prompt_files({"sha1": ("elsewhere.md", "other")}), 0)
        self.assertEqual(self.store.prompt_file("sha1"), "text")
        self.assertIsNone(self.store.prompt_file("nope"))

    def test_a_day_snapshot_replaces_that_days_rows(self):
        self.assertEqual(self.store.ledger(), [])
        self.store.record_ledger("2026-11-01", [{"agent_id": "a", "persona_chars": 10}])
        self.store.record_ledger("2026-11-02", [{"agent_id": "a", "persona_chars": 12}, {"agent_id": "b", "persona_chars": 1}])
        self.store.record_ledger("2026-11-02", [{"agent_id": "a", "persona_chars": 13}])
        self.assertEqual([(r["agent_id"], r["persona_chars"]) for r in self.store.ledger()], [("a", 13), ("b", 1)])
        self.assertEqual(self.store.ledger("2026-11-01")[0]["persona_chars"], 10)
        self.assertEqual([r["day"] for r in self.store.ledger_history("2026-11-02")], ["2026-11-02", "2026-11-02"])

    def test_runs(self):
        self.assertIsNone(self.store.last_run())
        self.store.record_run(since=0, until=10, found=3, added=2, took_s=1.5, at=10.0)
        self.store.record_run(since=10, until=20, found=1, added=1, errors="refusals: KeyError: 'id'", at=20.0)
        self.assertEqual(self.store.last_run()["errors"], "refusals: KeyError: 'id'")
        self.assertEqual([r["found"] for r in self.store.runs(15)], [1])


class TestWhatTheDetectorsRead(_StoreTest):

    def test_tables_that_dont_exist_give_nothing(self):
        self.assertEqual(self.store.turns(0, 1e12), [])
        self.assertEqual(self.store.judged_routes(0, 1e12), [])
        self.assertEqual(self.store.decision_errors(0, 1e12), [])
        self.assertEqual(self.store.closed_follow_ups(0, 1e12), [])
        self.assertEqual(self.store.session_tables(0), [])
        self.assertEqual(self.store.session_rows("ctx_none", 0, 1e12), [])

    def test_turns_in_the_window_with_their_json_decoded(self):
        with SignalStore(self.path).connect():
            pass
        for at in (10.0, 30.0):
            self.sql("INSERT INTO turn_ledger (at, agent_id, session_id, caller, tap, surface, user_text, reply_text, "
                     "context) VALUES (?, 'day-planner', 's1', 'main', 'chat', 'text', 'hi', 'hello', ?)",
                     (at, json.dumps({"touched": ["wiki/plan.md (write)"]})))
        turns = self.store.turns(0, 20)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["context"], {"touched": ["wiki/plan.md (write)"]})
        self.assertEqual((turns[0]["memory"], turns[0]["effects"]), ([], []))

    def test_judged_routes_are_labeled_declined_or_ignored_in_the_window(self):
        with SignalStore(self.path).connect():
            pass
        self.sql("INSERT INTO signals (id, decision_id, at, kind, gist) VALUES (1, 1, 5.0, 'event', 'a flight moved')")
        for label, reason, labeled_at, outcome, outcome_at in (
                ("bad", "wrong agent", 10.0, None, None),    # labeled in the window
                (None, None, None, "declined", 12.0),        # declined in the window
                (None, None, None, "engaged", 12.0),         # engaged: no friction
                ("bad", "wrong timing", 99.0, None, None)):  # labeled after the window
            self.sql("INSERT INTO routes (signal_id, decision_id, to_agent, asked, urgency, verdict, label, label_reason, "
                     "labeled_at, outcome, outcome_at) VALUES (1, 1, 'day-planner', 'now', 'now', 'deliver', ?, ?, ?, ?, ?)",
                     (label, reason, labeled_at, outcome, outcome_at))
        routes = self.store.judged_routes(0, 50)
        self.assertEqual([(r["label"], r["outcome"]) for r in routes], [("bad", None), (None, "declined")])
        self.assertEqual(routes[0]["gist"], "a flight moved")

    def test_decision_errors(self):
        with SignalStore(self.path).connect():
            pass
        self.sql("INSERT INTO decisions (at, tap, mode, errors) VALUES (10.0, 'chat', 'shadow', 'no reply block')")
        self.sql("INSERT INTO decisions (at, tap, mode) VALUES (11.0, 'chat', 'shadow')")
        self.assertEqual([d["errors"] for d in self.store.decision_errors(0, 50)], ["no reply block"])

    def test_closed_follow_ups_in_the_window(self):
        with FollowUpStore(self.path).connect():
            pass
        for closed_at, status in ((10.0, "closed"), (99.0, "closed"), (None, "open")):
            self.sql("INSERT INTO follow_ups (agent_id, goal, check_key, every_s, interval_s, until, status, opened_at, "
                     "closed_at, next_check_at, closed_by) VALUES ('reward-travel', 'seats to Lisbon', 'k', 86400, "
                     "86400, '2026-12-01', ?, 1.0, ?, 2.0, ?)", (status, closed_at, "you" if closed_at else ""))
        self.assertEqual([f["closed_at"] for f in self.store.closed_follow_ups(0, 50)], [10.0])

    def test_session_tables_are_the_active_ones_and_those_archived_since(self):
        sessions = SessionStore(self.path)
        sessions.append_message("day-planner:discord:day-planning", "user", "hi")
        sessions.append_message("main:discord:general", "user", "hello")
        archived = sessions.archive_table("ctx_main_discord_general")
        self.assertAlmostEqual(archived_at(archived), time.time(), delta=5)
        self.assertEqual(sorted(self.store.session_tables(time.time() - 60)),
                         sorted(["ctx_day_planner_discord_day_planning", archived]))
        self.assertEqual(self.store.session_tables(time.time() + 3600), ["ctx_day_planner_discord_day_planning"])

    def test_a_long_message_comes_back_as_its_two_ends(self):
        sessions = SessionStore(self.path)
        output = "Tool Output [1.0s]: <status>failed</status>" + "x" * 9000 + "<errors>HTTP 500</errors>"
        sessions.append_message("a:b", "system", output)
        sessions.append_token_usage("a:b", "m", 1, 1, 0.0, surface="voice")
        sessions.append_turn_trace("a:b", {"agent": "day-planner", "ttfa_ms": 1500}, surface="voice")
        rows = self.store.session_rows("ctx_a_b", 0, time.time() + 1)
        self.assertEqual([r["entry_type"] for r in rows], ["message", "token", "turn_trace"])
        message = rows[0]["message"]
        self.assertTrue(message.startswith("Tool Output [1.0s]: <status>failed</status>"))
        self.assertTrue(message.endswith("<errors>HTTP 500</errors>"))
        self.assertEqual(len(message), 4000 + len("\n[...]\n") + 4000)
        self.assertEqual(rows[1]["surface"], "voice")
        self.assertEqual(self.store.trace_times(["ctx_a_b", "ctx_missing"], 0), [("day-planner", 1.5)])

    def test_archived_at(self):
        self.assertIsNone(archived_at("ctx_a_b"))
        self.assertEqual(archived_at("ctx_a_b_archived_1700000000"), 1700000000)
        self.assertEqual(archived_at("ctx_a_b_archived_1700000000_2"), 1700000000, "renamed by the archive")


if __name__ == "__main__":
    unittest.main()
