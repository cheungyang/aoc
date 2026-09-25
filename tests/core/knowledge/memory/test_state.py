"""state.json: rolling signals, dream health and one-shot suggestions."""
import datetime
import os
import tempfile
import unittest

from core.knowledge.memory import state as mstate
from core.knowledge.memory import store

D = datetime.date
TODAY = D(2026, 9, 27)


class TestState(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pkm = tmp.name
        self.state = mstate.load(self.pkm)

    def test_load_defaults_and_round_trip(self):
        self.assertEqual(self.state, {"events": [], "suggested": [], "dreams": {}, "near_duplicates": []})
        mstate.record_event(self.state, mstate.UNKNOWN_TAG, "a", "pets", TODAY)
        mstate.save(self.state, self.pkm)
        self.assertEqual(mstate.load(self.pkm)["events"][0]["tag"], "pets")

    def test_corrupt_file_loads_empty(self):
        store.write_atomic(store.state_path(self.pkm), "not json")
        self.assertEqual(mstate.load(self.pkm)["events"], [])

    def test_prune_keeps_fourteen_days(self):
        mstate.record_event(self.state, mstate.UNKNOWN_TAG, "a", "x", TODAY - datetime.timedelta(days=15))
        mstate.record_event(self.state, mstate.UNKNOWN_TAG, "a", "y", TODAY - datetime.timedelta(days=14))
        mstate.prune_events(self.state, TODAY)
        self.assertEqual([e["tag"] for e in self.state["events"]], ["y"])

    def test_dream_health(self):
        mstate.record_dream(self.state, "a", False, TODAY, "boom")
        self.assertFalse(mstate.failing(self.state, "a"))
        mstate.record_dream(self.state, "a", False, TODAY, "boom")
        self.assertTrue(mstate.failing(self.state, "a"))
        self.assertEqual(self.state["dreams"]["a"]["last_error"], "boom")
        mstate.record_dream(self.state, "a", True, TODAY)
        self.assertEqual(self.state["dreams"]["a"], {"last_success": "2026-09-27", "consecutive_failures": 0})

    def test_near_duplicates_are_flagged_once(self):
        mstate.flag_near_duplicate(self.state, "food", "b", "a")
        mstate.flag_near_duplicate(self.state, "food", "a", "b")
        self.assertEqual(self.state["near_duplicates"], [{"tag": "food", "texts": ["a", "b"]}])

    def _events(self, kind, agent, tag, n):
        for _ in range(n):
            mstate.record_event(self.state, kind, agent, tag, TODAY)

    def test_suggestions_need_three_events_and_post_once(self):
        self._events(mstate.UNSUBSCRIBED_WRITE, "excursion-planner", "food", 3)
        self._events(mstate.UNKNOWN_TAG, "a", "pets", 3)
        self._events(mstate.UNKNOWN_TAG, "a", "rare", 2)
        lines = mstate.suggestions(self.state, {"excursion-planner": []}, ["food"], TODAY)
        self.assertEqual(len(lines), 2)
        self.assertIn("`pets`", lines[0])
        self.assertIn("excursion-planner wrote 3 `[food]`", lines[1])
        self.assertEqual(self.state["suggested"], [
            {"date": "2026-09-27", "agent": None, "action": "new_tag", "tag": "pets"},
            {"date": "2026-09-27", "agent": "excursion-planner", "action": "add", "tag": "food"},
        ])
        self.assertEqual(mstate.suggestions(self.state, {"excursion-planner": []}, ["food"], TODAY), [])

    def test_no_suggestion_once_acted_on(self):
        self._events(mstate.UNSUBSCRIBED_WRITE, "a", "food", 3)
        self._events(mstate.UNKNOWN_TAG, "a", "pets", 3)
        self.assertEqual(mstate.suggestions(self.state, {"a": ["food"]}, ["food", "pets"], TODAY), [])

    def test_unused_subscription(self):
        old = TODAY - datetime.timedelta(days=100)
        subs = {"a": ["food", "travel", "health"], "b": ["food"]}
        mtimes = {"food": old, "travel": TODAY, "health": None}
        writers = {"food": {"b"}}
        lines = mstate.unused_subscription_suggestions(self.state, subs, mtimes, writers, TODAY)
        self.assertEqual(lines, ["a reads `food`, unchanged for 90 days — drop it?",
                                 "a reads `health`, unchanged for 90 days — drop it?"])
        self.assertEqual(mstate.unused_subscription_suggestions(self.state, subs, mtimes, writers, TODAY), [])

    def test_file_date(self):
        path = os.path.join(self.pkm, "f")
        self.assertIsNone(mstate.file_date(path))
        store.write_atomic(path, "x")
        self.assertIsInstance(mstate.file_date(path), D)


if __name__ == "__main__":
    unittest.main()
