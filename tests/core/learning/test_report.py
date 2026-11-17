"""What `!learn` and the weekly digest say (core/learning/report.py)."""
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from core.learning import report
from core.learning import thresholds as T
from core.storage.learning import NOT_REAL, REAL, LearningStore

NOW = 1_800_000_000.0
DAY = 86400


def card(source_id, *, at=NOW - DAY, detector="correction", agent="day-planner", session="s1", severity=2,
         category="misread_request", evidence="no, only the first three", role="user", **details):
    return {"at": at, "detector": detector, "source": "turn_ledger", "source_id": str(source_id), "agent_id": agent,
            "session": session, "category": category, "evidence": evidence, "evidence_role": role,
            "severity": severity, "cost_turns": 1 if detector == "correction" else None, "details": details}


class _ReportTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.store = LearningStore(os.path.join(self.dir, "memory.db"))

    def ids(self):
        """{source_id: card id}."""
        return {e["source_id"]: e["id"] for e in self.store.friction(0)}


class TestPatterns(_ReportTest):

    def test_a_pattern_recurs_with_three_cards_in_two_sessions(self):
        events = [dict(card(1, session="a"), id=1), dict(card(2, session="a"), id=2), dict(card(3, session="b"), id=3),
                  dict(card(4, agent="main", session="c"), id=4), dict(card(5, agent="main", session="c"), id=5),
                  dict(card(6, agent="main", session="c"), id=6)]
        found = {(p.agent_id, p.detector): p for p in report.patterns(events)}
        self.assertTrue(found[("day-planner", "correction")].recurring)
        self.assertFalse(found[("main", "correction")].recurring, "three cards, but one session")
        self.assertEqual(report.patterns(events)[0].agent_id, "day-planner", "recurring ones first")

    def test_i_told_you_recurs_at_once(self):
        [pattern] = report.patterns([dict(card(1, category="ignored_instruction"), id=1)])
        self.assertTrue(pattern.recurring)

    def test_a_pattern_line_quotes_the_newest_cards(self):
        events = [dict(card(1, at=NOW - 3 * DAY, evidence="older words"), id=1),
                  dict(card(2, at=NOW - DAY, evidence="newer words", session="b"), id=2)]
        line = report.patterns(events)[0].line()
        self.assertTrue(line.startswith("day-planner · correction ×2 in 2 sessions · severity 4 · +2 turns"))
        self.assertLess(line.index('"newer words" (#2)'), line.index('"older words" (#1)'))


class TestLabels(_ReportTest):

    def test_parse_label(self):
        self.assertEqual(report.parse_label("412 #418,420 real"), ([412, 418, 420], REAL, []))
        self.assertEqual(report.parse_label("7 ❌"), ([7], NOT_REAL, []))
        self.assertEqual(report.parse_label("7 maybe"), ([7], None, ["maybe"]))

    def test_labeling_cards(self):
        self.store.record_friction([card(1), card(2, detector="refusal")])
        ids = self.ids()
        text = report.label_text(self.store, f"{ids['1']} {ids['2']} 999 real")
        self.assertIn(f"Labeled #{ids['1']}, #{ids['2']} as real friction.", text)
        self.assertIn("No card #999.", text)
        self.assertIn("correction 1/1", text)
        self.assertEqual(self.store.precision(), {"correction": (1, 1), "refusal": (1, 1)})

    def test_usage_when_the_verdict_or_ids_are_missing(self):
        for args in ("", "12", "real", "12 sort of"):
            with self.subTest(args):
                self.assertTrue(report.label_text(self.store, args).startswith("Usage: `!learn label"))

    def test_cards_to_label_take_turns_across_detectors(self):
        self.store.record_friction([card(1), card(2), card(3), card(4, detector="refusal"),
                                    card(5, detector="tool_failure", role="tool"),
                                    card(6, at=NOW - 40 * DAY), card(7)])
        self.store.label_friction(self.ids()["7"], REAL)
        picked = report.cards_to_label(self.store, 3, NOW)
        self.assertEqual(sorted(e["detector"] for e in picked), ["correction", "refusal", "tool_failure"])
        everything = report.cards_to_label(self.store, 25, NOW)
        self.assertEqual(sorted(e["source_id"] for e in everything), ["1", "2", "3", "4", "5"],
                         "not a labeled card, nor one older than 30 days")

    def test_cards_text_shows_what_a_correction_followed(self):
        self.store.record_friction([card(1, reply="Here are all eleven items due today")])
        text = report.cards_text(self.store, "", NOW)
        self.assertIn(f"#{self.ids()['1']} day-planner · correction · \"no, only the first three\"", text)
        self.assertIn('↳ after: "Here are all eleven items due today"', text)
        self.assertEqual(report.cards_text(LearningStore(os.path.join(self.dir, "empty.db")), "", NOW),
                         "No unlabeled cards from the last 30 days.")


class TestLedgerText(_ReportTest):

    def test_no_ledger_yet(self):
        self.assertIn("No ledger yet", report.ledger_text(self.store, NOW))

    def test_growth_over_a_month_and_a_sudden_drop_are_flagged(self):
        self.store.record_ledger("2026-10-01", [{"agent_id": "grower", "persona_chars": 10000}])
        self.store.record_ledger("2026-10-31", [{"agent_id": "shrinker", "persona_chars": 10000},
                                                {"agent_id": "steady", "persona_chars": 5000}])
        self.store.record_ledger("2026-11-01", [{"agent_id": "grower", "persona_chars": 11500},
                                                {"agent_id": "shrinker", "persona_chars": 7000},
                                                {"agent_id": "steady", "persona_chars": 5100}])
        text = report.ledger_text(self.store, NOW)
        self.assertIn("grower 11.5k · persona 11.5k", text)
        self.assertIn("⚠️ grower grew +15% since 2026-10-01 (10.0k → 11.5k)", text)
        self.assertIn("⚠️ shrinker shrank 30% since 2026-10-31", text)
        self.assertNotIn("⚠️ steady", text)
        self.assertIn("steady 5.1k", text)


class TestSummaryAndDigest(_ReportTest):

    def test_an_empty_loop(self):
        text = report.summary_text(self.store, NOW)
        self.assertIn("No nightly scan has run yet", text)
        self.assertIn("No friction recorded in the last 7 days.", text)
        self.assertIn("**Phase 0 exit:** 0/50 labeled, no precision yet (needs ≥0.7) · 0/5 recurring patterns", text)
        self.assertEqual(report.digest_text(self.store, NOW), "")

    def test_the_switch_off_is_said(self):
        with patch.dict(os.environ, {"LEARNING_OBSERVE": "0"}):
            self.assertIn(report.BANNER, report.summary_text(self.store, NOW))
        with patch.dict(os.environ, {"LEARNING_OBSERVE": "1"}):
            self.assertNotIn(report.BANNER, report.summary_text(self.store, NOW))

    def test_a_week_of_friction(self):
        self.store.record_friction([card(1, session="a", severity=3), card(2, session="b"), card(3, session="b"),
                                    card(4, detector="refusal", agent="home-steward", severity=1, role="system",
                                         evidence="Refused: filesystem: 'write' wiki/x.md"),
                                    card(5, at=NOW - 10 * DAY)])
        self.store.record_run(since=NOW - 3 * DAY, until=NOW - 3600, found=4, added=4, at=NOW - 3600)
        self.store.label_friction(self.ids()["2"], REAL)
        text = report.summary_text(self.store, NOW)
        self.assertIn("4 found (4 new)", text)
        self.assertIn("**Last 7 days:** 4 frictions · correction 3 · refusal 1", text)
        self.assertIn("**By agent:** day-planner 3 · home-steward 1", text)
        top = text.split("**Top 4**\n")[1].splitlines()[0]
        self.assertTrue(top.startswith(f"#{self.ids()['1']} day-planner · correction"), "the most severe first")
        self.assertIn("1/50 labeled, precision 1.00", text)
        self.assertIn("1/5 recurring patterns", text)

        digest = report.digest_text(self.store, NOW)
        self.assertIn("**4 frictions** (1 the week before) across 2 agent(s) · nightly scans: 1", digest)
        self.assertIn("⟳ day-planner · correction ×4 in 3 sessions", digest)
        self.assertIn("**Label these 4**", digest)
        self.assertIn("**Detectors** (real/labeled): correction 1/1", digest)
        self.assertIn("**Prompt ledger:** no alerts", digest)

    def test_friction_for_one_agent(self):
        self.store.record_friction([card(1), card(2, agent="main", detector="misroute", evidence="wrong agent")])
        text = report.friction_text(self.store, "main", NOW)
        self.assertIn("**Friction for main, last 14 days:** 1 cards in 1 patterns", text)
        self.assertNotIn("day-planner", text)
        self.assertEqual(report.friction_text(self.store, "nobody", NOW), "No friction recorded for nobody in the last 14 days.")

    def test_a_failed_scan_is_in_the_digest_even_in_a_quiet_week(self):
        self.store.record_run(since=0, until=NOW, found=0, added=0, errors="refusals: KeyError: 'id'", at=NOW - 3600)
        self.assertIn("nightly scans: 1, 1 with errors", report.digest_text(self.store, NOW))


class TestLearnText(_ReportTest):

    def test_each_subcommand(self):
        self.store.record_friction([card(1)])
        self.assertIn("🪞 **Learning loop**", report.learn_text("", self.store, NOW))
        self.assertIn("**Friction for day-planner", report.learn_text("friction day-planner", self.store, NOW))
        self.assertIn("1 card(s) to label", report.learn_text("cards 2", self.store, NOW))
        self.assertIn("as not friction", report.learn_text(f"label {self.ids()['1']} not", self.store, NOW))
        self.assertIn("No ledger yet", report.learn_text("ledger", self.store, NOW))
        self.assertTrue(report.learn_text("exp 7", self.store, NOW).startswith("Usage: `!learn`"))

    def test_now_defaults_to_the_clock(self):
        self.store.record_friction([card(1, at=time.time() - 60)])
        self.assertIn("1 frictions", report.learn_text("", self.store))


if __name__ == "__main__":
    unittest.main()
