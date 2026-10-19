"""The property_screen tool: one call, an envelope with JSON, issues or a clear error. The RapidAPI endpoints are faked."""
import json

from tests.helpers import envelope, execution_context
from tests.agents.property_scout import CRITERIA_MD, CRITERIA_WITH_STATE, REPORT_MD, FakeZillow, PkmCase, listing
from agents.property_scout.tools.property_screen import property_screen

AGENT = "property-scout"


class TestPropertyScreenTool(PkmCase):

    def setUp(self):
        super().setUp()
        self.write("criteria.md", CRITERIA_WITH_STATE)

    def call(self, **args):
        with execution_context(agent_id=AGENT):
            return envelope(property_screen.invoke(args))

    def test_returns_the_matches(self):
        FakeZillow(results=[listing(2, "15 Birch Ave", city="Capital City")]).install(self)
        out = self.call()
        self.assertEqual((out["status"], out["issues"], out["errors"]), ("ok", "None", "None"))
        payload = json.loads(out["payload"])
        self.assertEqual([m["address"] for m in payload["matches"]], ["15 Birch Ave, Capital City, IL 62701"])
        self.assertEqual(payload["matches"][0]["price"], "USD 849,000")
        self.assertEqual(payload["criteria"]["max_price"], "USD 1,000,000")
        self.assertIn("- **Dealbreakers**:", payload["criteria_to_judge"])
        self.assertNotIn("explain", out)

    def test_explain_lists_what_was_read_and_the_inputs(self):
        self.write("properties/120_Maple_Dr_Springfield.md", REPORT_MD)
        FakeZillow(results=[listing(1, "120 Maple Dr"), listing(2, "2 B St", sqft=2000)]).install(self)
        out = self.call(explain=True)
        self.assertEqual(out["status"], "ok")
        self.assertIn("- pkm/wallet/real-estate/criteria.md (modified", out["explain"])
        self.assertIn("- Zillow job job-1 (2 records)", out["explain"])
        self.assertIn("criteria.md line 7: max_price = USD 1,000,000", out["explain"])
        self.assertIn("2 B St, Springfield, IL 62701: USD 424.50/sqft = USD 849,000 / 2,000 sqft", out["explain"])
        self.assertIn("120 Maple Dr, Springfield, IL 62701: already evaluated (properties/120_Maple_Dr_Springfield.md)",
                      out["explain"])

    def test_a_criteria_line_that_doesnt_read_comes_back_raw(self):
        self.write("criteria.md", CRITERIA_WITH_STATE + "- **Max HOA**: ask me first\n")
        FakeZillow(results=[listing(2, "2 B St")]).install(self)
        out = self.call()
        self.assertEqual(out["status"], "partial")
        self.assertEqual(out["issues"], "- pkm/wallet/real-estate/criteria.md: 1 line not recognized\n"
                                        "    - **Max HOA**: ask me first")
        self.assertEqual(len(json.loads(out["payload"])["matches"]), 1)

    def test_a_pending_job_says_how_to_read_it_later(self):
        FakeZillow(statuses=("processing",)).install(self)
        out = self.call()
        self.assertEqual((out["status"], out["payload"]), ("failed", ""))
        self.assertIn('Zillow is still processing job job-1. Call property_screen(job_id="job-1")', out["errors"])

    def test_criteria_without_a_state_fail(self):
        self.write("criteria.md", CRITERIA_MD)
        zillow = FakeZillow().install(self)
        out = self.call()
        self.assertEqual(out["status"], "failed")
        self.assertIn("pkm/wallet/real-estate/criteria.md: names no state for its target cities", out["issues"])
        self.assertIn("(required)", out["issues"])
        self.assertEqual(zillow.requests, 0)

    def test_a_bad_override_is_an_error(self):
        out = self.call(criteria={"schools": "good"})
        self.assertEqual(out["status"], "failed")
        self.assertIn("Error: unknown criteria key(s): schools", out["errors"])

    def test_zillow_errors_for_some_urls_are_partial(self):
        FakeZillow(results=[listing(2, "2 B St")], errors=[{"url": "x", "error": "blocked"}]).install(self)
        out = self.call()
        self.assertEqual((out["status"], out["issues"], out["errors"]), ("partial", "- Zillow: x: blocked", "None"))

    def test_the_quota_message_comes_through(self):
        zillow = FakeZillow(status_code=429).install(self)
        zillow._response = _quota(zillow._response)
        out = self.call()
        self.assertEqual(out["status"], "failed")
        self.assertIn("Network or HTTP error from the Zillow Property Data API", out["errors"])
        self.assertIn("MONTHLY quota", out["errors"])

    def test_an_agent_without_the_grant_is_refused(self):
        zillow = FakeZillow().install(self)
        with execution_context(agent_id="market-scout"):
            out = envelope(property_screen.invoke({}))
        self.assertEqual(out["status"], "failed")
        self.assertIn("does not have permission", out["errors"])
        self.assertEqual(zillow.requests, 0)

    def test_a_call_outside_an_agent_run_is_refused(self):
        zillow = FakeZillow().install(self)
        out = envelope(property_screen.invoke({}))
        self.assertEqual(out["status"], "failed")
        self.assertIn("no active execution context", out["errors"])
        self.assertEqual(zillow.requests, 0)


def _quota(respond):
    def wrapped(body):
        resp = respond(body)
        resp.json.return_value = {"message": "You have exceeded the MONTHLY quota for Requests on your current plan, BASIC."}
        return resp
    return wrapped
