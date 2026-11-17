"""scripts/learning_nightly.py: the learning loop's nightly friction scan and prompt ledger."""
import io
import os
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from core.config.settings import Config
from core.learning import ledger
from core.storage.learning import REFUSAL, LearningStore
from scripts import learning_nightly


class TestNightlyScan(unittest.TestCase):

    def setUp(self):
        self.store = LearningStore()  # this test's memory.db (tests/conftest.py)
        self.now = time.time()
        self.store.record_event(REFUSAL, at=self.now - 600, agent_id="home-steward", name="home_assistant",
                                detail={"action": "turn_on", "path": "light.porch"})
        patcher = patch.object(ledger, "snapshot", return_value=[])
        self.snapshot = patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_runs_unless_the_loop_is_off(self):
        with Config().overriding(learning_observe=True):
            self.assertEqual(learning_nightly.has_work(None), (True, "the nightly friction scan"))
        with Config().overriding(learning_observe=False):
            self.assertEqual(learning_nightly.has_work(None), (False, "LEARNING_OBSERVE is off"))

    def test_a_scan_records_the_friction_the_run_and_the_ledger(self):
        self.assertEqual(learning_nightly.scan(self.store, now=self.now), "", "nothing to post when all went well")
        [event] = self.store.friction(0)
        self.assertEqual((event["detector"], event["agent_id"]), ("refusal", "home-steward"))
        run = self.store.last_run()
        self.assertEqual((run["found"], run["added"], run["errors"]), (1, 1, ""))
        self.assertAlmostEqual(run["until"] - run["since"], 3 * 86400)
        self.assertEqual(self.snapshot.call_args.kwargs["day"], ledger.today(self.now))

        learning_nightly.scan(self.store, now=self.now + 60)
        self.assertEqual((self.store.last_run()["found"], self.store.last_run()["added"]), (1, 0),
                         "a day read twice adds nothing twice")

    def test_what_failed_is_posted_and_kept_with_the_run(self):
        self.snapshot.side_effect = ledger.LedgerError("planner: ValueError: bad entry")
        output = learning_nightly.scan(self.store, now=self.now)
        self.assertIn("🪞 The nightly friction scan found 1 (1 new), but part of it failed:", output)
        self.assertIn("- ledger: LedgerError: planner: ValueError: bad entry", output)
        self.assertIn("ledger: LedgerError", self.store.last_run()["errors"])
        self.assertEqual(len(self.store.friction(0)), 1, "what was found is still recorded")

    def test_a_dry_run_counts_and_records_nothing(self):
        output = learning_nightly.scan(self.store, days=1, now=self.now, dry_run=True)
        self.assertIn("1 friction events over 1 day(s) (nothing recorded):", output)
        self.assertIn("  refusal: 1", output)
        self.assertEqual((self.store.friction(0), self.store.last_run()), ([], None))
        self.snapshot.assert_not_called()

    def test_main_prints_only_when_something_failed(self):
        with Config().overriding(pkm_dir=os.path.join(os.path.dirname(Config().memory_db_path), "no-vault")):
            out = io.StringIO()
            with redirect_stdout(out):
                learning_nightly.main([])
            self.assertEqual(out.getvalue(), "")
            self.snapshot.side_effect = ledger.LedgerError("x")
            with redirect_stdout(out):
                learning_nightly.main(["--days", "1"])
        self.assertIn("part of it failed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
