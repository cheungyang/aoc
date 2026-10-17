"""Scans decide whether the model runs, and hand it what they found."""
import unittest

from core.scheduler import preconditions as pre
from core.scheduler.preconditions import PreconditionError
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import Findings, WorkDecision
from tests.core.scheduler.preconditions import ctx


class FakeScan(Scan):
    type_name = "fake_scan"

    def __init__(self, config, result):
        self.result = result
        super().__init__(config)

    def scan(self, ctx, **args):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result(ctx, **args) if callable(self.result) else self.result


def run(result, **config):
    return FakeScan({"type": "fake_scan", **config}, result).evaluate(ctx())


class TestScan(unittest.TestCase):

    def test_args_must_be_an_object_and_keys_are_checked(self):
        with self.assertRaises(PreconditionError):
            FakeScan({"type": "fake_scan", "args": [1]}, None)
        with self.assertRaises(PreconditionError):
            FakeScan({"type": "fake_scan", "name": "x"}, None)

    def test_a_finding_runs_with_its_text_as_findings(self):
        def scan(ctx, limit=3):
            return WorkDecision(True, "2 tickers moved", detail=f"NVDA -6%\nTSLA +5% (limit {limit})")

        decision = run(scan, args={"limit": 2})
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "2 tickers moved")
        self.assertEqual(decision.detail, Findings("fake_scan", "NVDA -6%\nTSLA +5% (limit 2)"))

    def test_nothing_found_skips(self):
        decision = run((False, "quiet day"))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "quiet day")

    def test_an_error_fails_closed(self):
        decision = run(ConnectionError("yahoo is down"))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "fake_scan failed (ConnectionError: yahoo is down)")

    def test_each_scan_lives_in_a_module_named_after_its_type(self):
        scans = {name: cls for name, cls in pre.REGISTRY.items() if issubclass(cls, Scan)}
        self.assertTrue(scans)
        for name, cls in scans.items():
            with self.subTest(scan=name):
                self.assertEqual(cls.__module__, f"core.scheduler.preconditions.{name}")


if __name__ == "__main__":
    unittest.main()
