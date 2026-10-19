"""property_screen wakes property-scout only when new listings pass criteria.md. Zillow is faked."""
import time

from core.scheduler.preconditions import PreconditionError, build_precondition
from core.scheduler.spec import Findings, Problems, ScheduleContext
from tests.agents.property_scout import CRITERIA_MD, CRITERIA_WITH_STATE, REPORT_MD, FakeZillow, PkmCase, listing
from agents.property_scout.preconditions.property_screen import PropertyScreen
from agents.property_scout.lib.scout import SCAN_POLLING

AGENT = "property-scout"


def build(**config):
    return build_precondition({"type": "property_screen", **config}, agent_id=AGENT)


def ctx():
    return ScheduleContext(schedule_id="property-scout:weekly-scout", agent_id=AGENT, now=time.time())


class TestPropertyScreen(PkmCase):

    def setUp(self):
        super().setUp()
        self.write("criteria.md", CRITERIA_WITH_STATE)

    def test_is_declared_by_its_type_for_property_scout(self):
        self.assertIsInstance(build(), PropertyScreen)
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "property_screen"}, agent_id="market-scout")

    def test_takes_no_args(self):
        with self.assertRaises(PreconditionError):
            build(args={"max_price": 3_000_000})

    def test_new_matches_run_with_their_numbers(self):
        self.write("properties/120_Maple_Dr_Springfield.md", REPORT_MD)
        zillow = FakeZillow(
            results=[
                listing(1, "120 Maple Dr"),
                listing(2, "15 Birch Ave", city="Capital City", zipcode="62704", price=828_500, sqft=1840,
                        rent=3800, description="Big backyard."),
                listing(3, "8 Pricey Rd", price=1_100_000),
            ],
            statuses=("processing", "processing", "complete"),
        ).install(self)

        decision = build().evaluate(ctx())

        self.assertTrue(decision)
        self.assertEqual(decision.reason, "1 new matches of 3 listings")
        self.assertIsInstance(decision.detail, Findings)
        self.assertEqual(decision.detail.source, "property_screen")
        self.assertEqual(decision.detail.issues, ())
        text = decision.detail.text
        self.assertTrue(text.startswith("1 new listing pass criteria.md (3 pulled; 1 more matches were already evaluated)."))
        self.assertIn("1. 15 Birch Ave, Capital City, IL 62704", text)
        self.assertIn("USD 828,500 | 3 bd / 2 ba | 1,840 sqft | USD 450.27/sqft", text)
        self.assertIn("Rent est. USD 3,800/mo (USD 45,600/yr) | gross yield 5.5% | est. cap rate 5.5%", text)
        self.assertNotIn("120 Maple", text)
        self.assertNotIn("Pricey", text)
        # criteria.md's lines the filters don't cover come along, for the verdict.
        self.assertIn("criteria.md lines the filters don't cover; judge each listing against them:\n"
                      "- **Target Corridor**: Springfield metro, east side (Springfield to Capital City)\n"
                      "- **Dealbreakers**:\n"
                      "  - Low-rated schools", text)
        self.assertNotIn("couldn't read", text)
        # The request budget: one submit and sparse reads.
        self.assertEqual((len(zillow.posts), len(zillow.gets)), (1, 3))
        self.assertEqual(zillow.sleeps, [SCAN_POLLING.first_wait, SCAN_POLLING.interval, SCAN_POLLING.interval])

    def test_a_criteria_line_that_doesnt_read_rides_along_with_the_findings(self):
        self.write("criteria.md", CRITERIA_WITH_STATE + "- **Min Baths**: plenty\n")
        FakeZillow(results=[listing(2, "2 B St")]).install(self)
        decision = build().evaluate(ctx())
        self.assertTrue(decision)
        self.assertEqual(decision.detail.issues, ("pkm/wallet/real-estate/criteria.md: 1 line not recognized",))
        self.assertIn("criteria.md lines the screen couldn't read, so their filters weren't applied:\n"
                      "- **Min Baths**: plenty", decision.detail.text)

    def test_a_dry_week_skips(self):
        FakeZillow(results=[listing(3, "8 Pricey Rd", price=1_100_000)]).install(self)
        decision = build().evaluate(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "no new matches: 1 listings pulled, 0 matches already evaluated")
        self.assertIsNone(decision.detail)

    def test_a_dry_week_with_a_criteria_problem_is_posted(self):
        self.write("criteria.md", CRITERIA_WITH_STATE + "- **Min Baths**: plenty\n")
        FakeZillow(results=[listing(3, "8 Pricey Rd", price=1_100_000)]).install(self)
        decision = build().evaluate(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "no new matches: 1 listings pulled, 0 matches already evaluated (with issues)")
        self.assertEqual(decision.detail, Problems("property_screen", ("pkm/wallet/real-estate/criteria.md: 1 line not recognized",)))

    def test_only_already_evaluated_matches_skips(self):
        self.write("properties/120_Maple_Dr_Springfield.md", REPORT_MD)
        self.write("passed.md", "- 2026-10-09 | 9 Oak Ct, Springfield, IL 62701 | https://www.zillow.com/homedetails/9_zpid/ | busy road\n")
        FakeZillow(results=[listing(1, "120 Maple Dr"), listing(9, "9 Oak Ct")]).install(self)
        decision = build().evaluate(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "no new matches: 2 listings pulled, 2 matches already evaluated")

    def test_zillow_failing_every_url_is_posted(self):
        FakeZillow(errors=[{"url": "x", "error": "blocked"}]).install(self)
        decision = build().evaluate(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "property_screen can't run: Zillow: x: blocked; Zillow: job job-1 returned no listings")
        self.assertIsInstance(decision.detail, Problems)

    def test_a_job_still_running_fails_closed_within_the_budget(self):
        zillow = FakeZillow(statuses=("processing",)).install(self)
        decision = build().evaluate(ctx())
        self.assertFalse(decision)
        self.assertIn("still processing after 8 result reads", decision.reason)
        self.assertIsNone(decision.detail)  # a timeout: logged, not posted
        self.assertEqual(zillow.requests, 1 + SCAN_POLLING.max_reads)

    def test_a_spent_quota_or_a_rejected_key_is_posted(self):
        for status in (429, 401):
            with self.subTest(status=status):
                zillow = FakeZillow(status_code=status).install(self)
                decision = build().evaluate(ctx())
                self.assertFalse(decision)
                self.assertEqual(decision.detail, Problems(
                    "property_screen", (f"the check failed (HTTPError: {status} Client Error)",)))
                self.assertEqual(zillow.requests, 1)  # the submit, refused

    def test_a_quota_spent_on_a_result_read_is_posted(self):
        zillow = QuotaSpentOnRead(statuses=("processing", "complete")).install(self)
        decision = build().evaluate(ctx())
        self.assertEqual(decision.detail, Problems(
            "property_screen", ("the check failed (HTTPError: 429 Client Error)",)))
        self.assertEqual((len(zillow.posts), len(zillow.gets)), (1, 1))

    def test_a_server_error_is_only_logged(self):
        FakeZillow(status_code=503).install(self)
        decision = build().evaluate(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "property_screen failed (HTTPError: 503 Server Error)")
        self.assertIsNone(decision.detail)

    def test_criteria_without_a_state_are_posted_before_any_request(self):
        self.write("criteria.md", CRITERIA_MD)
        zillow = FakeZillow().install(self)
        decision = build().evaluate(ctx())
        self.assertFalse(decision)
        self.assertIn("property_screen can't run: pkm/wallet/real-estate/criteria.md: names no state", decision.reason)
        self.assertIsInstance(decision.detail, Problems)
        self.assertEqual(zillow.requests, 0)


class QuotaSpentOnRead(FakeZillow):
    """Zillow takes the job, then refuses the result read with a 429: the quota ran out in between."""

    def get(self, url, headers=None, timeout=None):
        self.status_code = 429
        return super().get(url, headers, timeout)
