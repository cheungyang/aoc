"""The friction detectors (core/learning/detectors.py): each rule on fixture rows, then the scan.

Every text here is invented: the repo is public (tests/test_repo_privacy.py).
"""
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from core.learning import detectors as D
from core.learning import thresholds as T
from core.storage.learning import NOT_REAL, REFUSAL, LearningStore
from core.storage.sessions import SessionStore
from core.storage.signals import SignalStore

HOUR = 3600


def turn(id, at, user, reply="", *, agent="day-planner", session="s1", caller="main", tap="chat", surface="text",
         context=None):
    """A turn_ledger row as LearningStore.turns returns it. Your words carry the time stamp the model sees."""
    return {"id": id, "at": at, "agent_id": agent, "session_id": session, "caller": caller, "tap": tap,
            "surface": surface, "user_text": f"[Tue 09:{id % 60:02d}] {user}", "reply_text": reply,
            "context": context or {}, "memory": [], "effects": []}


def row(id, entry_type, message="", *, role=None, at=None, surface=None, execution_time=None):
    """A ctx_ session row as LearningStore.session_rows returns it."""
    return {"id": id, "entry_type": entry_type, "from_role": role, "created_at": float(at if at is not None else id),
            "surface": surface, "execution_time": execution_time, "message": message}


def detectors_of(events):
    return [e.detector for e in events]


class TestCorrectionsAndRephrases(unittest.TestCase):

    def test_a_correction_right_after_the_reply(self):
        turns = [turn(1, 0, "what's on my plate today", "Here are all eleven items due today: ..."),
                 turn(2, 60, "no, only the first three please")]
        [event] = D.corrections_and_rephrases(turns)
        self.assertEqual((event.detector, event.category, event.source_id), ("correction", "misread_request", "2"))
        self.assertEqual((event.evidence, event.evidence_role), ("no, only the first three please", "user"))
        self.assertEqual((event.agent_id, event.severity, event.cost_turns), ("day-planner", 2, 1))
        self.assertEqual(event.details["turn"], 1)

    def test_i_told_you_is_an_ignored_instruction(self):
        turns = [turn(1, 0, "plan my week", "Here is a long plan ..."), turn(2, 30, "I told you to keep it short")]
        self.assertEqual(D.corrections_and_rephrases(turns)[0].category, "ignored_instruction")

    def test_a_no_answering_the_replys_question_is_not_a_correction(self):
        turns = [turn(1, 0, "book the dentist", "Thursday at three is free. Shall I book it?"), turn(2, 30, "no")]
        self.assertEqual(D.corrections_and_rephrases(turns), [])

    def test_no_worries_is_not_a_no(self):
        turns = [turn(1, 0, "thanks for that", "Done."), turn(2, 30, "no worries, that's all for now")]
        self.assertEqual(D.corrections_and_rephrases(turns), [])

    def test_too_late_to_be_about_the_reply(self):
        turns = [turn(1, 0, "plan my week", "Here it is."), turn(2, T.PAIR_GAP_S + 1, "no, the other week")]
        self.assertEqual(D.corrections_and_rephrases(turns), [])

    def test_saying_the_same_thing_again(self):
        turns = [turn(1, 0, "what is the weather in lisbon tomorrow", "It's sunny in Lisbon today."),
                 turn(2, 45, "what's the weather in lisbon for tomorrow")]
        [event] = D.corrections_and_rephrases(turns)
        self.assertEqual(event.detector, "rephrase")
        self.assertGreaterEqual(event.details["ratio"], T.REPHRASE_RATIO)

    def test_short_turns_never_look_like_a_repeat(self):
        turns = [turn(1, 0, "ok thanks", "Anytime."), turn(2, 20, "ok thanks")]
        self.assertEqual(D.corrections_and_rephrases(turns), [])

    def test_only_your_turns_in_the_same_session_pair_up(self):
        for other in (dict(tap="scheduled"), dict(caller="day-planner"), dict(context={"called": True}),
                      dict(surface="scheduled"), dict(session="s2")):
            with self.subTest(**{k: str(v) for k, v in other.items()}):
                turns = [turn(1, 0, "plan my week", "Here it is."), turn(2, 30, "no, the other week", **other)]
                self.assertEqual(D.corrections_and_rephrases(turns), [])


class TestTurnLedgerDetectors(unittest.TestCase):

    def test_a_question_left_unanswered_while_you_talked_elsewhere(self):
        asked = turn(1, 0, "find me a flight", "I found two flights. Which one do you prefer?")
        elsewhere = turn(2, 2 * HOUR, "what's for dinner", agent="meal-planner", session="s2")
        [event] = D.abandoned_questions([asked, elsewhere], now=2 * T.ABANDON_S)
        self.assertEqual((event.detector, event.source_id, event.evidence), ("abandoned_question", "1",
                                                                               "Which one do you prefer?"))
        self.assertEqual(event.evidence_role, "agent")

    def test_not_abandoned(self):
        cases = {
            "answered in the session": [turn(1, 0, "find me a flight", "Which one do you prefer?"),
                                        turn(2, HOUR, "the first one")],
            "away, not abandoning": [turn(1, 0, "find me a flight", "Which one do you prefer?")],
            "only an offer": [turn(1, 0, "find me a flight", "Booked. Want me to add it to your calendar?"),
                              turn(2, HOUR, "dinner ideas", session="s2")],
            "no question": [turn(1, 0, "find me a flight", "Booked the morning one."),
                            turn(2, HOUR, "dinner ideas", session="s2")],
        }
        for name, turns in cases.items():
            with self.subTest(name):
                self.assertEqual(D.abandoned_questions(turns, now=2 * T.ABANDON_S), [])

    def test_a_question_too_recent_to_call_abandoned(self):
        turns = [turn(1, 0, "find me a flight", "Which one do you prefer?"), turn(2, HOUR, "dinner", session="s2")]
        self.assertEqual(D.abandoned_questions(turns, now=T.ABANDON_S - 1), [])

    def test_asking_another_agent_the_same_thing(self):
        turns = [turn(1, 0, "move my dentist appointment to friday"),
                 turn(2, 300, "move my dentist appointment to friday please", agent="main", session="s2")]
        [event] = D.agent_switches(turns)
        self.assertEqual((event.detector, event.agent_id, event.source_id), ("agent_switch", "day-planner", "1"))
        self.assertEqual(event.details["to_agent"], "main")

    def test_a_different_request_to_another_agent_is_no_switch(self):
        turns = [turn(1, 0, "move my dentist appointment to friday"),
                 turn(2, 300, "what should we cook tonight for dinner", agent="meal-planner", session="s2")]
        self.assertEqual(D.agent_switches(turns), [])

    def test_markup_in_the_reply_you_saw(self):
        turns = [turn(1, 0, "hi", "Sure.<speech>Sure.</speech>"), turn(2, 10, "hi", "Fine.")]
        [event] = D.leaked_markup(turns)
        self.assertEqual((event.detector, event.evidence), ("contract_break", "<speech> reached the channel"))


class TestLabelsAndLoggedEvents(unittest.TestCase):

    def test_judged_routes(self):
        routes = [
            {"id": 1, "label": "bad", "label_reason": "wrong agent", "labeled_at": 5.0, "to_agent": "day-planner",
             "gist": "a flight moved"},
            {"id": 2, "label": "bad", "label_reason": "not worth it", "labeled_at": 6.0, "to_agent": "day-planner",
             "gist": "rain tomorrow"},
            {"id": 3, "label": None, "outcome": "declined", "outcome_at": 7.0, "to_agent": "meal-planner", "gist": "x"},
            {"id": 4, "label": None, "outcome": "ignored", "outcome_at": 8.0, "to_agent": "meal-planner", "gist": "y"},
            {"id": 5, "label": "good", "labeled_at": 9.0, "to_agent": "meal-planner", "gist": "z"},
        ]
        events = D.judged_routes(routes)
        self.assertEqual([(e.detector, e.agent_id, e.source_id, e.severity) for e in events], [
            ("misroute", "main", "1", 2), ("explicit_label", "main", "2", 2),
            ("explicit_label", "meal-planner", "3:declined", 2), ("explicit_label", "meal-planner", "4:ignored", 1)])
        self.assertEqual(events[0].category, "routing_handoff")

    def test_switchboard_errors_and_speech_fallbacks_are_contract_breaks(self):
        [error] = D.switchboard_errors([{"id": 4, "at": 1.0, "errors": "no reply block"}])
        [fallback] = D.speech_fallbacks([{"id": 9, "at": 2.0, "agent_id": "day-planner"}])
        self.assertEqual((error.detector, error.agent_id, error.evidence), ("contract_break", "main", "no reply block"))
        self.assertEqual((fallback.detector, fallback.agent_id, fallback.source), ("contract_break", "day-planner",
                                                                                  "learning_events"))

    def test_a_thumbs_down_counts_once_per_message(self):
        [event] = D.reactions([{"id": 3, "at": 1.0, "agent_id": "day-planner", "name": "👎", "channel": "day-planning",
                                "detail": {"message_id": "77", "channel_id": "5"}}])
        self.assertEqual((event.detector, event.source_id, event.evidence),
                         ("explicit_label", "reaction:77", "👎 on a message in #day-planning"))

    def test_commands(self):
        logged = [{"id": 1, "at": 1.0, "name": "kill", "agent_id": "main", "detail": {"args": "3"}},
                  {"id": 2, "at": 2.0, "name": "go back", "agent_id": "reward-travel", "detail": {"heard": "go back"}},
                  {"id": 3, "at": 3.0, "name": "new", "agent_id": "main", "detail": {}}]
        events = D.commands(logged)
        self.assertEqual([(e.detector, e.agent_id, e.evidence) for e in events],
                         [("interrupt", "main", "!kill 3"), ("misroute", "main", "go back")])

    def test_a_refusal_retried_soon_is_worse(self):
        logged = [{"id": 1, "at": 0.0, "agent_id": "a", "name": "filesystem", "detail": {"action": "write", "path": "x"}},
                  {"id": 2, "at": 30.0, "agent_id": "a", "name": "filesystem", "detail": {"action": "write", "path": "y"}},
                  {"id": 3, "at": 40.0, "agent_id": "a", "name": "bash", "detail": {"action": "rm", "path": ""}},
                  {"id": 4, "at": 30.0 + T.RETRY_GAP_S + 1, "agent_id": "a", "name": "filesystem", "detail": {}}]
        events = D.refusals(logged)
        self.assertEqual([(e.source_id, e.severity, e.details["retry"]) for e in events],
                         [("1", 1, False), ("2", 2, True), ("3", 1, False), ("4", 1, False)])
        self.assertEqual(events[1].evidence, "Refused again: filesystem: 'write' y")

    def test_unmet_follow_ups(self):
        day = 86400
        follow_ups = [
            {"id": 1, "closed_by": "you", "opened_at": 0.0, "closed_at": day / 2, "goal": "seats to Lisbon"},
            {"id": 2, "closed_by": "you", "opened_at": 0.0, "closed_at": 3 * day, "goal": "kept a while"},
            {"id": 3, "closed_by": "until", "kind": "watch", "finds": 0, "opened_at": 0.0, "closed_at": 9.0,
             "goal": "a price drop", "until": "2026-11-01"},
            {"id": 4, "closed_by": "until", "kind": "watch", "finds": 2, "opened_at": 0.0, "closed_at": 9.0, "goal": "g"},
            {"id": 5, "closed_by": "until", "kind": "once", "finds": 0, "opened_at": 0.0, "closed_at": 9.0, "goal": "g"},
        ]
        events = D.follow_up_outcomes(follow_ups)
        self.assertEqual([(e.source_id, e.severity) for e in events], [("1", 2), ("3", 1)])


class TestSessionTables(unittest.TestCase):

    def test_tool_failures_loops_round_trips_and_cutoffs(self):
        call = 'Tool web_search [weather]:{"q": "weather"}'
        rows = [
            row(1, "message", "[Tue 09:00] weather?", role="user"),
            row(2, "message", call, role="system"),
            row(3, "message", "Tool Output [1.2s]: <status>failed</status> ... <errors>HTTP 500 from the API\n"
                              "retry later</errors>", role="system"),
            row(4, "message", call, role="system"),
            row(5, "message", "Tool Output [0.4s]: <status>ok</status>", role="system"),
            *[row(6 + i, "token", surface="text") for i in range(7)],
            row(13, "turn_trace", json.dumps({"agent": "day-planner", "outcome": "cancelled"})),
            row(14, "message", "[Tue 09:05] thanks", role="user"),
            row(15, "token", surface="text"),
        ]
        events = D.session_friction("ctx_day_planner_x", "day-planner", rows)
        by_detector = {e.detector: e for e in events}
        self.assertEqual(sorted(by_detector), ["round_trips", "tool_failure", "tool_loop", "voice_cutoff"])
        failure = by_detector["tool_failure"]
        self.assertEqual((failure.evidence, failure.details["tool"], failure.severity),
                         ("HTTP 500 from the API", "web_search", 2))
        self.assertEqual((by_detector["tool_loop"].source_id, by_detector["tool_loop"].details["times"]), ("2-4", 2))
        self.assertEqual(by_detector["round_trips"].details, {"calls": 7, "budget": 6})
        self.assertEqual(by_detector["voice_cutoff"].source_id, "13")
        self.assertTrue(all(e.session == "ctx_day_planner_x" for e in events))

    def test_replies_that_arent_a_failed_call(self):
        """Seen on real data: a skill's text shows its output template, a reply quotes a
        template's placeholder, and a refusal is the refusal detector's card."""
        rows = [
            row(1, "message", "[Tue 09:00] tidy the inbox", role="user"),
            row(2, "message", "Tool load_skill:{'skill_id': 'inbox'}", role="system"),
            row(3, "message", "Tool Output [0.1s]: ## Output\n<errors>[Any errors, or 'None']</errors>", role="system"),
            row(4, "message", "Tool agent_call:{'agent_id': 'note-taker'}", role="system"),
            row(5, "message", "Tool Output [2.0s]: Done. <errors>[Any file reading\nerrors, or 'None']</errors>",
                role="system"),
            row(6, "message", "Tool filesystem:{'action': 'write'}", role="system"),
            row(7, "message", "Tool Output [0.1s]: <errors>Error: Agent note-taker does not have permission to "
                              "perform 'write' on path wiki/x.md</errors>", role="system"),
            row(8, "message", "Tool filesystem:{'action': 'read'}", role="system"),
            row(9, "message", 'Tool Output [0.1s]: <errors><instruction_error action="read" path="wiki/y.md">Error: '
                              'File not found at wiki/y.md</instruction_error></errors>', role="system"),
        ]
        [event] = D.session_friction("ctx_t", "note-taker", rows)
        self.assertEqual((event.source_id, event.evidence), ("9", "Error: File not found at wiki/y.md"))

    def test_a_slow_voice_turn(self):
        usual = D.usual_ttfa([("day-planner", 1.0)] * (T.SLOW_MIN_SAMPLES - 1) + [("day-planner", 2.0)]
                             + [("main", 1.0)] * (T.SLOW_MIN_SAMPLES - 1))
        self.assertEqual(usual, {"day-planner": 1.0}, "an agent with too few traces has no usual")
        rows = [row(1, "turn_trace", json.dumps({"agent": "day-planner"}), execution_time=1.4),
                row(2, "turn_trace", json.dumps({"agent": "day-planner"}), execution_time=1.6),
                row(3, "turn_trace", json.dumps({"agent": "main"}), execution_time=9.0)]
        [event] = D.slow_turns("ctx_t", "day-planner", rows, usual)
        self.assertEqual((event.source_id, event.cost_seconds), ("2", 0.6))

    def test_the_table_s_agent_is_the_longest_id_it_starts_with(self):
        agents = ["graph-worker", "graph-worker-low", "main"]
        self.assertEqual(D.table_agent("ctx_graph_worker_low_job_1a2b3c4d", agents), "graph-worker-low")
        self.assertEqual(D.table_agent("ctx_graph_worker_job_1a2b3c4d_archived_1700000000", agents), "graph-worker")
        self.assertEqual(D.table_agent("ctx_unknown_x", agents), "")


class TestPostEdits(unittest.TestCase):

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault)

    def git(self, commits, numstat="5\t2\twiki/plan.md"):
        def fake(vault, *args):
            if args[:2] == ("config", "user.email"):
                return "bot@example.com\n"
            if args[0] == "log":
                return "".join(f"{sha}\t{email}\t{at}\n" for sha, email, at in commits)
            if args[0] == "show":
                return numstat
            return None
        return fake

    def turns(self):
        return [turn(1, 1000.0, "write the plan", "Done.", context={"touched": ["wiki/plan.md (write)",
                                                                               "wiki/other.md (read)"]})]

    def test_your_edit_of_what_the_agent_wrote(self):
        git = self.git([("b0t", "bot@example.com", 1500), ("y0u", "you@example.com", 4600)])
        [event] = D.post_edits(self.turns(), self.vault, now=1e9, git=git)
        self.assertEqual((event.detector, event.source_id, event.at), ("post_edit", "1:wiki/plan.md", 4600.0))
        self.assertEqual(event.details["lines"], 7)
        self.assertEqual(event.evidence, "You changed 7 lines of wiki/plan.md, 1 h after it was written")

    def test_no_card(self):
        cases = {
            "only the host's commits": (self.git([("b0t", "bot@example.com", 1500)]), self.turns()),
            "a small touch-up": (self.git([("y0u", "you@example.com", 1500)], numstat="1\t1\twiki/plan.md"),
                                 self.turns()),
            "a file the agent only read": (self.git([("y0u", "you@example.com", 1500)]),
                                           [turn(1, 1000.0, "x", context={"touched": ["wiki/plan.md (read)"]})]),
        }
        for name, (git, turns) in cases.items():
            with self.subTest(name):
                self.assertEqual(D.post_edits(turns, self.vault, now=1e9, git=git), [])

    def test_without_a_host_identity_it_stays_silent(self):
        self.assertEqual(D.post_edits(self.turns(), self.vault, now=1e9, git=lambda *a: None), [])


class TestScrub(unittest.TestCase):

    def test_secrets_are_scrubbed_and_prose_survives(self):
        self.assertEqual(D.scrub("api_key=AbCdEf123456 failed"), "api_key=[secret] failed")
        self.assertEqual(D.scrub("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345"), "Authorization: [secret]")
        self.assertEqual(D.scrub("sk-proj-abcdefghijklmnop1234 leaked"), "[secret] leaked")
        for prose in ("my password reset didn't work", "the token expired yesterday",
                      "agents/day-planner/skills/morning_digest_and_more/SKILL.md was refused"):
            self.assertEqual(D.scrub(prose), prose)

    def test_clip_scrubs_and_cuts(self):
        self.assertEqual(D.clip("token: hunter22   and\nmore", 200), "token: [secret] and more")
        self.assertEqual(D.clip("x" * 20, 10), "x" * 10 + "…")


class TestScan(unittest.TestCase):
    """The whole scan over a memory.db with a turn ledger, a session table and logged events."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "memory.db")
        self.store = LearningStore(self.path)
        self.now = time.time()
        with SignalStore(self.path).connect():
            pass
        with sqlite3.connect(self.path) as conn:
            for id, offset, user, reply in ((1, 120, "[Tue 09:00] what's on today", "Here are all eleven items ..."),
                                            (2, 60, "[Tue 09:01] no, only the first three", "Here are three.")):
                conn.execute("INSERT INTO turn_ledger (id, at, agent_id, session_id, caller, tap, surface, user_text, "
                             "reply_text) VALUES (?, ?, 'day-planner', 's1', 'main', 'chat', 'text', ?, ?)",
                             (id, self.now - offset, user, reply))
        sessions = SessionStore(self.path)
        sessions.append_message("day-planner:discord:day-planning", "user", "[Tue 09:00] what's on today")
        sessions.append_message("day-planner:discord:day-planning", "system", "Tool calendar_list:{}")
        sessions.append_message("day-planner:discord:day-planning", "system",
                                "Tool Output [0.2s]: <status>failed</status><errors>calendar unreachable</errors>")
        self.store.record_event(REFUSAL, at=self.now - 30, agent_id="day-planner", name="filesystem",
                                detail={"action": "write", "path": "wiki/x.md"})

    def scan(self):
        return D.scan(self.store, self.now - HOUR, self.now + 60, now=self.now, agents=["day-planner"])

    def test_every_detector_runs_and_a_rerun_adds_nothing(self):
        result = self.scan()
        self.assertEqual(result.errors, [])
        self.assertEqual(sorted(detectors_of(result.events)), ["correction", "refusal", "tool_failure"])
        failure = next(e for e in result.events if e.detector == "tool_failure")
        self.assertEqual((failure.agent_id, failure.session), ("day-planner", "ctx_day_planner_discord_day_planning"))
        rows = [e.row() for e in result.events]
        self.assertEqual(self.store.record_friction(rows), 3)
        self.assertEqual(self.store.record_friction([e.row() for e in self.scan().events]), 0)

    def test_a_detector_that_fails_doesnt_stop_the_others(self):
        with patch.object(D, "corrections_and_rephrases", side_effect=RuntimeError("boom")):
            result = self.scan()
        self.assertEqual(result.errors, ["corrections: RuntimeError: boom"])
        self.assertEqual(sorted(detectors_of(result.events)), ["refusal", "tool_failure"])

    def test_a_muted_detector_s_cards_are_left_out(self):
        self.store.record_friction([{"at": 1.0, "detector": "refusal", "source": "learning_events",
                                     "source_id": f"old{i}"} for i in range(T.MUTE_AFTER)])
        for event in self.store.friction(0):
            self.store.label_friction(event["id"], NOT_REAL)
        result = self.scan()
        self.assertEqual(result.muted, ["refusal"])
        self.assertEqual(sorted(detectors_of(result.events)), ["correction", "tool_failure"])


if __name__ == "__main__":
    unittest.main()
