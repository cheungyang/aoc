"""Proposals (core/learning/proposals.py): refused, checked or failed, and saved; never applied."""
import os
import shutil
import tempfile
import unittest

from core.learning import proposals
from core.learning.proposals import ProposalError, submit
from core.storage.learning import CHECKED, FAILED, LearningStore

NOW = 1_800_000_000.0
SKILL = ("---\nname: morning_digest\ndescription: The morning digest.\n---\n## Core\n\n"
         "1. List every item due today.\n2. Close with the weather.\n")
RULES = "- top_items: 3  # how many items the digest leads with\n"
QUIET = {"notes_check": lambda path, text: [], "lint": lambda agent, root: [], "agent_names": set(), "tools": set()}


class _Proposals(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = os.path.join(self.dir, "repo")
        self.pkm = os.path.join(self.dir, "pkm")
        self.write(os.path.join(self.root, "skills", "morning_digest", "SKILL.md"), SKILL)
        self.write(os.path.join(self.pkm, "agents", "day-planner", "RULES.md"), RULES)
        self.store = LearningStore(os.path.join(self.dir, "memory.db"))
        self.store.record_friction([
            {"at": NOW - 3600, "detector": "correction", "source": "learning_events", "source_id": str(i),
             "agent_id": "day-planner", "evidence": "just the top three please", "evidence_role": "user"}
            for i in range(1, 4)])
        self.cards = sorted(c["id"] for c in self.store.friction(0))

    @staticmethod
    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def submit(self, **kwargs):
        args = dict(path="skills/morning_digest/SKILL.md",
                    edits=[{"find": "List every item due today.", "replace": "Lead with the 3 highest-impact items."}],
                    pattern="day-planner:correction", rationale="Three corrections asked for fewer items.",
                    fixes=self.cards, now=NOW, root=self.root, pkm=self.pkm, **QUIET)
        args.update(kwargs)
        return submit(self.store, **args)


class TestSaved(_Proposals):

    def test_a_proposal_that_passes_is_checked_and_saved_and_the_file_is_untouched(self):
        saved = self.submit(expected="It leads with three items.", retires=["Keep summaries brief"])
        self.assertEqual(saved["status"], CHECKED, saved["gates"])
        stored = self.store.proposal(saved["id"])
        self.assertEqual(stored["pattern"], "day-planner:correction")
        self.assertEqual(stored["fixes"], self.cards)
        self.assertEqual(stored["retires"], ["Keep summaries brief"])
        self.assertEqual(stored["base"], proposals.gates.sha(SKILL))
        self.assertEqual(stored["delta_chars"], len("Lead with the 3 highest-impact items.") - len("List every item due today."))
        self.assertEqual([g["gate"] for g in stored["gates"]], list(proposals.gates.ORDER))
        with open(os.path.join(self.root, "skills", "morning_digest", "SKILL.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), SKILL, "handing a proposal in edits nothing")

    def test_one_that_fails_a_check_is_saved_as_failed_with_the_reason(self):
        saved = self.submit(edits=[{"find": "List every item due today.", "replace": "See https://example.com."}])
        self.assertEqual(saved["status"], FAILED)
        failed = [g for g in saved["gates"] if not g["ok"]]
        self.assertEqual([g["gate"] for g in failed], ["capability"])
        self.assertIn("a new URL", failed[0]["detail"])

    def test_a_vault_file_is_read_from_the_vault(self):
        saved = self.submit(path="pkm/agents/day-planner/RULES.md",
                            edits=[{"find": "top_items: 3", "replace": "top_items: 5"}])
        self.assertEqual(saved["status"], CHECKED, saved["gates"])
        self.assertEqual(saved["base"], proposals.gates.sha(RULES))

    def test_a_file_it_may_not_change_fails_where_without_reading_it(self):
        saved = self.submit(path="agents/day-planner/agent.json", edits=[{"find": "{", "replace": "["}])
        self.assertEqual(saved["status"], FAILED)
        self.assertEqual([g["gate"] for g in saved["gates"]], ["where"])


class TestRefused(_Proposals):

    def test_what_isnt_a_proposal_saves_nothing(self):
        cases = {
            "no edits": dict(edits=[]),
            "both text": dict(edits=[{"find": "x"}]),
            "isn't \"<agent>:<detector>\"": dict(pattern="day planner"),
            "no rationale": dict(rationale="  "),
            "no cards": dict(fixes=[]),
            "no card #999": dict(fixes=[999]),
            "doesn't exist": dict(path="skills/nowhere/SKILL.md"),
            "at most 6": dict(edits=[{"find": str(i), "replace": ""} for i in range(7)]),
        }
        for words, kwargs in cases.items():
            with self.subTest(words):
                with self.assertRaises(ProposalError) as raised:
                    self.submit(**kwargs)
                self.assertIn(words, str(raised.exception))
        self.assertEqual(self.store.proposals(), [])

    def test_the_same_change_twice(self):
        first = self.submit()
        with self.assertRaises(ProposalError) as raised:
            self.submit()
        self.assertIn(f"already proposal #{first['id']}", str(raised.exception))

    def test_three_patterns_a_week_and_three_candidates_a_pattern(self):
        for n, word in enumerate(("highest-impact", "most urgent", "biggest")):
            self.submit(edits=[{"find": "List every item due today.", "replace": f"Lead with the 3 {word} items."}])
        with self.assertRaises(ProposalError) as raised:
            self.submit(edits=[{"find": "List every item due today.", "replace": "Lead with three items."}])
        self.assertIn("already has 3 checked candidates", str(raised.exception))
        for n, detector in enumerate(("rephrase", "round_trips")):
            self.submit(pattern=f"day-planner:{detector}",
                        edits=[{"find": "Close with the weather.", "replace": f"Close with the weather ({n})."}])
        with self.assertRaises(ProposalError) as raised:
            self.submit(pattern="day-planner:tool_failure")
        self.assertIn("3 patterns already have a checked proposal this week", str(raised.exception))

    def test_failed_ones_dont_count_toward_the_limits(self):
        for n in range(4):
            self.submit(edits=[{"find": "List every item due today.", "replace": f"See https://example.com/{n}."}])
        self.assertEqual(self.submit()["status"], CHECKED)

    def test_last_weeks_proposals_dont_count(self):
        for word in ("highest-impact", "most urgent", "biggest"):
            self.submit(now=NOW - 8 * 86400,
                        edits=[{"find": "List every item due today.", "replace": f"Lead with the 3 {word} items."}])
        self.assertEqual(self.submit()["status"], CHECKED)


if __name__ == "__main__":
    unittest.main()
