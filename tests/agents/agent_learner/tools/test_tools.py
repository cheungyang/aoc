"""friction_query and submit_proposal, as Kaizen calls them (agents/agent-learner/tools/)."""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.learning import proposals
from core.storage.learning import CHECKED, LearningStore
from tests.helpers import envelope, execution_context
from agents.agent_learner.tools.friction_query import friction_query
from agents.agent_learner.tools.submit_proposal import submit_proposal

SKILL = "---\nname: morning_digest\ndescription: The digest.\n---\n## Core\n\n1. List every item due today.\n"
QUIET = {"notes_check": lambda path, text: [], "lint": lambda agent, root: [], "agent_names": set(), "tools": set()}


class _Tools(unittest.TestCase):

    def setUp(self):
        self.store = LearningStore()  # memory.db, which the suite points at a temporary file per test
        self.store.record_friction([
            {"at": 1_000.0 + i, "detector": "correction", "source": "learning_events", "source_id": str(i),
             "agent_id": "day-planner", "session": f"s{i % 2}", "evidence": "just the top three", "evidence_role": "user",
             "severity": 2} for i in range(3)])
        self.cards = sorted(c["id"] for c in self.store.friction(0))

    def call(self, tool, **kwargs):
        with execution_context(agent_id="agent-learner"):
            return envelope(tool.func(**kwargs))


@patch("agents.agent_learner.lib.views.time.time", return_value=1_000.0 + 3600)
class TestFrictionQuery(_Tools):

    def test_patterns_by_default(self, _now):
        out = self.call(friction_query)
        self.assertEqual(out["status"], "ok", out)
        [pattern] = json.loads(out["payload"])["patterns"]
        self.assertEqual((pattern["pattern"], pattern["cards"], pattern["recurring"]), ("day-planner:correction", 3, True))

    def test_an_episode_needs_a_card_that_exists(self, _now):
        self.assertIn("needs `card`", self.call(friction_query, view="episode")["errors"])
        self.assertIn("No card #999", self.call(friction_query, view="episode", card=999)["errors"])
        out = self.call(friction_query, view="episode", card=self.cards[0])
        self.assertEqual(json.loads(out["payload"])["card"]["id"], self.cards[0])

    def test_the_other_views(self, _now):
        self.assertEqual(len(json.loads(self.call(friction_query, view="cards")["payload"])["cards"]), 3)
        self.assertEqual(json.loads(self.call(friction_query, view="ledger")["payload"])["agents"], [])
        self.assertEqual(json.loads(self.call(friction_query, view="proposals")["payload"])["proposals"], [])

    def test_another_agent_cant_use_it(self, _now):
        with execution_context(agent_id="day-planner"):
            out = envelope(friction_query.func())
        self.assertEqual(out["status"], "failed")


class TestSubmitProposal(_Tools):

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        path = os.path.join(self.root, "skills", "morning_digest", "SKILL.md")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write(SKILL)
        real = proposals.submit
        patcher = patch("agents.agent_learner.tools.submit_proposal.submit",
                        lambda store, **kw: real(store, root=self.root, pkm=self.root, now=2_000.0, **QUIET, **kw))
        patcher.start()
        self.addCleanup(patcher.stop)

    def hand_in(self, **kwargs):
        args = dict(path="skills/morning_digest/SKILL.md", pattern="day-planner:correction",
                    edits=[{"find": "List every item due today.", "replace": "Lead with the 3 highest-impact items."}],
                    rationale="Three corrections asked for fewer items.", fixes=self.cards)
        args.update(kwargs)
        return self.call(submit_proposal, **args)

    def test_a_checked_proposal_is_saved_and_nothing_is_edited(self):
        out = self.hand_in()
        self.assertEqual(out["status"], "ok", out)
        result = json.loads(out["payload"])
        self.assertEqual(result["status"], CHECKED, result["checks"])
        self.assertIn("waits for the user's review", result["upshot"])
        self.assertEqual(self.store.proposal(result["id"])["by_agent"], "agent-learner")
        with open(os.path.join(self.root, "skills", "morning_digest", "SKILL.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), SKILL)

    def test_a_failed_one_says_which_checks_and_what_to_do(self):
        result = json.loads(self.hand_in(edits=[{"find": "List every item due today.",
                                                 "replace": "See https://example.com."}])["payload"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("failed capability", result["upshot"])
        self.assertIn("Never word around", result["upshot"])

    def test_what_isnt_a_proposal_comes_back_as_an_error(self):
        out = self.hand_in(fixes=[999])
        self.assertEqual(out["status"], "failed")
        self.assertIn("Nothing saved: no card #999", out["errors"])
        self.assertEqual(self.store.proposals(), [])


if __name__ == "__main__":
    unittest.main()
