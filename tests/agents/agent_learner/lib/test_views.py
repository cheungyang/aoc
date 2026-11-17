"""The views friction_query and the Monday scan read (agents/agent-learner/lib/views.py)."""
import os
import shutil
import tempfile
import unittest

from agents.agent_learner.lib import views
from core.storage.learning import CHECKED, FAILED, LearningStore

NOW = 1_800_000_000.0
DAY = 86400


def card(source_id, *, agent="day-planner", detector="correction", session="s1", at=NOW - DAY, **fields):
    row = {"at": at, "detector": detector, "source": "learning_events", "source_id": str(source_id), "agent_id": agent,
           "session": session, "evidence": "no, only the first three", "evidence_role": "user", "severity": 2}
    row.update(fields)
    return row


class TestViews(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.store = LearningStore(os.path.join(self.dir, "memory.db"))
        self.store.record_friction([card(1, session="a"), card(2, session="a"), card(3, session="b"),
                                    card(4, agent="", detector="voice_cutoff", evidence="You said stop.",
                                         evidence_role="system"),
                                    card(5, at=NOW - 40 * DAY)])

    def propose(self, pattern, status=CHECKED, at=NOW - DAY):
        return self.store.add_proposal({"pattern": pattern, "path": "skills/x/SKILL.md", "base": "abc",
                                        "rationale": "why", "status": status,
                                        "gates": [{"gate": "budget", "ok": status == CHECKED, "detail": "too long"}]},
                                       at=at)

    def test_patterns_recurring_first_with_their_open_proposals(self):
        pid = self.propose("day-planner:correction")
        self.propose("day-planner:correction", status=FAILED)
        found = views.patterns_view(self.store, now=NOW)
        first = found["patterns"][0]
        self.assertEqual((first["pattern"], first["cards"], first["sessions"], first["recurring"]),
                         ("day-planner:correction", 3, 2, True))
        self.assertEqual(first["open_proposals"], [pid], "checked ones only")
        self.assertEqual(len(first["examples"]), 3)
        self.assertEqual(found["patterns"][1]["pattern"], "?:voice_cutoff")
        self.assertIn("recurring", found["note"])

    def test_cards_by_agent_including_none(self):
        self.assertEqual([c["detector"] for c in views.cards_view(self.store, agent="?", now=NOW)["cards"]],
                         ["voice_cutoff"])
        self.assertEqual(len(views.cards_view(self.store, agent="day-planner", now=NOW)["cards"]), 3)
        self.assertEqual(len(views.cards_view(self.store, agent="day-planner", days=60, now=NOW)["cards"]), 3,
                         "the window is capped at 30 days: the 40-day-old card stays out")

    def test_an_episode_or_none(self):
        self.assertIsNone(views.episode_view(self.store, 999))
        some = self.store.friction(0)[0]["id"]
        self.assertEqual(views.episode_view(self.store, some)["card"]["id"], some)

    def test_proposals_with_the_checks_that_failed(self):
        self.propose("day-planner:correction", status=FAILED)
        [p] = views.proposals_view(self.store, now=NOW)["proposals"]
        self.assertEqual(p["failed_checks"], [{"gate": "budget", "detail": "too long"}])

    def test_open_patterns_leave_out_those_with_a_checked_proposal(self):
        self.assertEqual([views.pattern_key(p.agent_id, p.detector) for p in views.open_patterns(self.store, NOW)],
                         ["day-planner:correction"])
        self.propose("day-planner:correction")
        self.assertEqual(views.open_patterns(self.store, NOW), [])
        self.propose("day-planner:correction", at=NOW - 8 * DAY)
        self.assertEqual(views.open_patterns(self.store, NOW), [], "the newer one still counts")


if __name__ == "__main__":
    unittest.main()
