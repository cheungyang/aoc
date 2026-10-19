"""Scans decide whether the model runs, hand it what they found, and never skip quietly over a problem."""
import unittest
from http import HTTPStatus

import requests

from core.scheduler import preconditions as pre
from core.scheduler.preconditions import PreconditionError
from core.scheduler.preconditions.scan import Scan, is_network_error
from core.scheduler.spec import Findings, Problems, WorkDecision
from core.tooling import DataProblem, Report
from tests.core.scheduler.preconditions import ctx


class FakeScan(Scan):
    type_name = "fake_scan"

    def __init__(self, config, result):
        self.result = result
        super().__init__(config)

    def scan(self, ctx, report, **args):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result(ctx, report, **args) if callable(self.result) else self.result


def run(result, **config):
    return FakeScan({"type": "fake_scan", **config}, result).evaluate(ctx())


class WrappedClientError(Exception):
    """Stands in for a client that wraps a network error, as Home Assistant's does."""


def http_error(status):
    """The HTTPError requests raises for a response with `status`, from `raise_for_status()`."""
    response = requests.Response()
    response.status_code = status
    response.reason = HTTPStatus(status).phrase
    response.url = "https://api.example/v1/properties"
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        return error


class TestScan(unittest.TestCase):

    def test_args_must_be_an_object_and_keys_are_checked(self):
        with self.assertRaises(PreconditionError):
            FakeScan({"type": "fake_scan", "args": [1]}, None)
        with self.assertRaises(PreconditionError):
            FakeScan({"type": "fake_scan", "name": "x"}, None)

    def test_a_finding_runs_with_its_text_as_findings(self):
        def scan(ctx, report, limit=3):
            return WorkDecision(True, "2 tickers moved", detail=f"CAT -6%\nXOM +5% (limit {limit})")

        decision = run(scan, args={"limit": 2})
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "2 tickers moved")
        self.assertEqual(decision.detail, Findings("fake_scan", "CAT -6%\nXOM +5% (limit 2)"))

    def test_nothing_found_skips(self):
        decision = run((False, "quiet day"))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "quiet day")
        self.assertIsNone(decision.detail)

    def test_the_lines_a_scan_couldnt_read_go_with_its_notice(self):
        def scan(ctx, report):
            report.unparsed("criteria.md", ["Lot: big yard", "Near a park"])
            return False, "nothing new"

        decision = run(scan)
        self.assertEqual(decision.detail.issues, ("criteria.md: 2 lines not recognized",))
        self.assertEqual(decision.detail.raw, (("criteria.md: 2 lines not recognized", ("Lot: big yard", "Near a park")),))
        self.assertIn("- criteria.md: 2 lines not recognized\n> Lot: big yard\n> Near a park\n", decision.detail.message())

    def test_the_lines_a_scan_couldnt_read_go_with_its_findings(self):
        def scan(ctx, report):
            report.unparsed("watchlist.md", ["* xyz corp"])
            return WorkDecision(True, "1 ticker moved", detail="XYZ -6%")

        decision = run(scan)
        self.assertIn("> * xyz corp", decision.detail.as_block())

    def test_a_network_error_fails_closed_without_a_notice(self):
        decision = run(ConnectionError("yahoo is down"))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "fake_scan failed (ConnectionError: yahoo is down)")
        self.assertIsNone(decision.detail)

    def test_a_network_error_wrapped_by_a_client_is_still_only_logged(self):
        def scan(ctx, report):
            try:
                raise ConnectionRefusedError("connection refused")
            except OSError:
                raise WrappedClientError("Could not reach Home Assistant") from None

        decision = run(scan)
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "fake_scan failed (WrappedClientError: Could not reach Home Assistant)")
        self.assertIsNone(decision.detail)

    def test_missing_required_data_skips_with_problems(self):
        def scan(ctx, report):
            report.missing("pkm/wallet/real-estate/criteria.md", "names no state")
            report.raise_if_failed()

        decision = run(scan)
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "fake_scan can't run: pkm/wallet/real-estate/criteria.md: names no state")
        self.assertEqual(
            decision.detail, Problems("fake_scan", ("pkm/wallet/real-estate/criteria.md: names no state",))
        )

    def test_an_unexpected_error_is_a_problem_not_a_quiet_skip(self):
        decision = run(KeyError("Close"))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "fake_scan can't run: the check failed (KeyError: 'Close')")
        self.assertEqual(decision.detail, Problems("fake_scan", ("the check failed (KeyError: 'Close')",)))

    def test_an_error_keeps_the_issues_recorded_before_it(self):
        def scan(ctx, report):
            report.problem("pkm/wallet/stock/watchlist.md", "'XYZ' has no symbol")
            raise ValueError("no tickers to screen")

        decision = run(scan)
        self.assertEqual(decision.detail.issues, (
            "pkm/wallet/stock/watchlist.md: 'XYZ' has no symbol",
            "the check failed (ValueError: no tickers to screen)",
        ))

    def test_issues_on_a_quiet_day_are_problems(self):
        def scan(ctx, report):
            report.unparsed("pkm/wallet/credit-card/card-a.md", ["| Q5 | ??? |"])
            return WorkDecision(False, "nothing due")

        decision = run(scan)
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "nothing due (with issues)")
        self.assertEqual(decision.detail.issues, ("pkm/wallet/credit-card/card-a.md: 1 line not recognized",))

    def test_issues_on_a_day_with_work_ride_along_in_the_findings(self):
        def scan(ctx, report):
            report.problem("pkm/wallet/stock/watchlist.md", "'XYZ' has no quote")
            return WorkDecision(True, "1 ticker moved", detail="CAT -6%")

        decision = run(scan)
        self.assertTrue(decision)
        self.assertEqual(decision.detail.issues, ("pkm/wallet/stock/watchlist.md: 'XYZ' has no quote",))
        self.assertIn("don't fill the gap with a guess", decision.detail.as_block())

    def test_issues_reach_the_run_even_without_findings_text(self):
        def scan(ctx, report):
            report.problem("pkm/a.md", "row 3 unreadable")
            return WorkDecision(True, "always run")

        decision = run(scan)
        self.assertEqual(decision.detail, Findings("fake_scan", "", ("pkm/a.md: row 3 unreadable",)))

    def test_a_data_problem_raised_directly_carries_its_report(self):
        report = Report()
        report.missing("pkm/wallet/kitchen/INVENTORY.md", "file not found")
        decision = run(DataProblem(report))
        self.assertEqual(decision.detail.issues, ("pkm/wallet/kitchen/INVENTORY.md: file not found",))

    def test_each_scan_lives_in_its_agents_folder_in_a_module_named_after_its_type(self):
        from core.loaders.agent_code import agents_with_code, package_for

        found = 0
        for agent_id in agents_with_code():
            for name, cls in pre.agent_preconditions(agent_id).items():
                with self.subTest(agent=agent_id, scan=name):
                    self.assertTrue(issubclass(cls, Scan))
                    self.assertEqual(cls.__module__, f"agents.{package_for(agent_id)}.preconditions.{name}")
                    found += 1
        self.assertTrue(found)


class TestNetworkErrors(unittest.TestCase):

    def test_network_errors_and_what_wraps_them(self):
        self.assertTrue(is_network_error(TimeoutError("slow")))
        self.assertTrue(is_network_error(requests.ConnectionError("dns")))
        try:
            try:
                raise requests.Timeout("read timed out")
            except requests.RequestException as e:
                raise WrappedClientError("Could not reach the service") from e
        except WrappedClientError as wrapped:
            self.assertTrue(is_network_error(wrapped))

    def test_everything_else_needs_a_person(self):
        self.assertFalse(is_network_error(OSError("disk gone")))
        self.assertFalse(is_network_error(ValueError("criteria.md names no state")))
        self.assertFalse(is_network_error(WrappedClientError("the token was rejected (401)")))


class TestHttpErrors(unittest.TestCase):
    """An HTTP 4xx other than 408 needs a person (a rejected key, a spent quota); 408 and 5xx are hiccups."""

    def test_a_client_error_is_posted(self):
        for status in (401, 404, 429):
            with self.subTest(status=status):
                decision = run(http_error(status))
                self.assertFalse(decision)
                self.assertEqual(decision.detail, Problems("fake_scan", (
                    f"the check failed (HTTPError: {status} Client Error: {HTTPStatus(status).phrase} "
                    f"for url: https://api.example/v1/properties)",
                )))

    def test_a_timeout_a_dropped_connection_or_a_server_error_is_only_logged(self):
        for error in (http_error(408), http_error(500), http_error(503),
                      requests.Timeout("read timed out"), requests.ConnectionError("connection refused")):
            with self.subTest(error=str(error)):
                decision = run(error)
                self.assertFalse(decision)
                self.assertEqual(decision.reason, f"fake_scan failed ({type(error).__name__}: {error})")
                self.assertIsNone(decision.detail)

    def test_a_client_error_a_client_wraps_is_posted(self):
        """The chain is followed as for network errors: `raise ... from e`, or `from None` while handling it."""
        def wrapping(keep_cause):
            def scan(ctx, report):
                try:
                    raise http_error(429)
                except requests.HTTPError as e:
                    raise WrappedClientError("the monthly quota is spent") from (e if keep_cause else None)
            return scan

        for keep_cause in (True, False):
            with self.subTest(keep_cause=keep_cause):
                decision = run(wrapping(keep_cause))
                self.assertFalse(decision)
                self.assertEqual(decision.detail, Problems(
                    "fake_scan", ("the check failed (WrappedClientError: the monthly quota is spent)",)))


if __name__ == "__main__":
    unittest.main()
