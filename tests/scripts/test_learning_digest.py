"""scripts/learning_digest.py: the learning loop's weekly digest for #learning-lab."""
import io
import time
import unittest
from contextlib import redirect_stdout

from core.config.settings import Config
from core.storage.learning import LearningStore
from scripts import learning_digest


def friction(store, at):
    store.record_friction([{"at": at, "detector": "correction", "source": "turn_ledger", "source_id": "1",
                            "agent_id": "day-planner", "session": "s1", "evidence": "no, only the first three",
                            "evidence_role": "user", "severity": 2}])


class TestWeeklyDigest(unittest.TestCase):

    def test_no_digest_when_the_loop_is_off_or_the_week_is_quiet(self):
        with Config().overriding(learning_observe=False):
            self.assertEqual(learning_digest.has_work(None), (False, "LEARNING_OBSERVE is off"))
        with Config().overriding(learning_observe=True):
            self.assertEqual(learning_digest.has_work(None), (False, "no memory.db yet"))
            friction(LearningStore(), time.time() - 30 * 86400)
            self.assertEqual(learning_digest.has_work(None), (False, "nothing to report this week"))

    def test_a_week_with_friction_is_posted(self):
        friction(LearningStore(), time.time() - 3600)
        with Config().overriding(learning_observe=True):
            self.assertEqual(learning_digest.has_work(None), (True, "a week of friction to report"))
        out = io.StringIO()
        with redirect_stdout(out):
            learning_digest.main()
        self.assertTrue(out.getvalue().startswith("🪞 **Weekly friction digest**"))
        self.assertIn("**Label these 1**", out.getvalue())

    def test_a_quiet_week_prints_nothing(self):
        out = io.StringIO()
        with redirect_stdout(out):
            learning_digest.main()
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
