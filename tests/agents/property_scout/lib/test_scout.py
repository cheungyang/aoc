"""The scout run: criteria, one Zillow job, the filters, the archive. The RapidAPI endpoints are faked."""
import os

from core.tooling import DataProblem, Report
from tests.agents.property_scout import CRITERIA_MD, CRITERIA_WITH_STATE, REPORT_MD, FakeZillow, PkmCase, listing
from agents.property_scout.lib.scout import SCAN_POLLING, SCAN_REQUESTS, TOOL_POLLING, JobPending, run_screen

CITIES = ("Springfield", "Shelbyville", "Ogdenville", "North-Haverbrook", "Capital-City")


class TestRunScreen(PkmCase):

    def setUp(self):
        super().setUp()
        self.write("criteria.md", CRITERIA_WITH_STATE)
        self.report = Report(explain=True)

    def test_one_job_for_every_city_then_new_matches_only(self):
        self.write("properties/120_Maple_Dr_Springfield.md", REPORT_MD)
        zillow = FakeZillow(
            results=[
                listing(1, "120 Maple Dr"),                                     # archived
                listing(2, "15 Birch Ave", city="Capital City", sqft=1840, price=828_500, rent=3800),
                listing(3, "5 Condo Way", home_type="CONDO"),
                listing(4, "6 Big House Ln", price=1_160_000),
            ],
            statuses=("processing", "complete"),
        ).install(self)

        run = run_screen(self.report)

        self.assertEqual(len(zillow.posts), 1)
        urls = zillow.posts[0]["json"]["urls"]
        self.assertEqual(urls, [f"https://www.zillow.com/homes/for_sale/{c}-IL/0-1000000_price/" for c in CITIES])
        self.assertEqual(zillow.posts[0]["headers"]["x-rapidapi-key"], "test-key")
        self.assertEqual(len(zillow.gets), 2)
        self.assertEqual(zillow.sleeps, [TOOL_POLLING.first_wait, TOOL_POLLING.interval])
        self.assertEqual(run.pulled, 4)
        self.assertEqual([m["zpid"] for m in run.matches], [2])
        self.assertEqual(run.matches[0]["price_per_sqft"], 450.27)  # rows keep plain numbers
        self.assertEqual(run.already_evaluated, 1)
        self.assertEqual(self.report.status, "ok")

        payload = run.payload()
        self.assertEqual(payload["pulled"], 4)
        self.assertEqual([m["zpid"] for m in payload["matches"]], [2])
        self.assertNotIn("missed", payload["matches"][0])
        self.assertEqual(payload["matches"][0]["price"], "USD 828,500")
        self.assertEqual(payload["matches"][0]["price_per_sqft"], "USD 450.27")
        self.assertEqual(payload["matches"][0]["rent_zestimate"], "USD 3,800")
        self.assertIsNone(payload["matches"][0]["hoa_fee"])
        self.assertEqual(payload["matches"][0]["estimated_cap_rate_percent"], 5.5)
        self.assertEqual(payload["already_evaluated"], 1)
        self.assertEqual(payload["failed_by_rule"], {"not single family": 1, "over USD 1,000,000": 1})
        self.assertEqual([n["price"] for n in payload["near_misses"]], ["USD 849,000", "USD 1,160,000"])
        self.assertEqual(payload["criteria"]["max_price"], "USD 1,000,000")
        self.assertEqual(payload["criteria"]["max_hoa"], "USD 0")
        self.assertIn("  - Busy streets or highway noise", payload["criteria_to_judge"])
        self.assertNotIn("errors", payload)

    def test_explain_traces_the_requests_the_filters_and_the_archive(self):
        self.write("properties/120_Maple_Dr_Springfield.md", REPORT_MD)
        FakeZillow(results=[listing(1, "120 Maple Dr"), listing(2, "2 B St", price=1_160_000)],
                   statuses=("processing", "complete")).install(self)
        run_screen(self.report)
        traces = self.report.traces
        self.assertIn("criteria.md line 7: max_price = USD 1,000,000", traces)
        self.assertIn("Zillow: 3 requests, 1 submit and 2 result reads (at most 10)", traces)
        self.assertIn("120 Maple Dr, Springfield, IL 62701: already evaluated (properties/120_Maple_Dr_Springfield.md)", traces)
        self.assertIn("over USD 1,000,000: 1 listing: 2 B St, Springfield, IL 62701 (USD 1,160,000)", traces)
        explain = self.report.render_explain()
        self.assertIn("- Zillow job job-1 (2 records)", explain)
        self.assertIn("pkm/wallet/real-estate/properties (modified", explain)

    def test_include_evaluated_keeps_archived_matches(self):
        self.write("properties/120_Maple_Dr_Springfield.md", REPORT_MD)
        FakeZillow(results=[listing(1, "120 Maple Dr")]).install(self)
        run = run_screen(self.report, include_evaluated=True)
        self.assertEqual([m["zpid"] for m in run.matches], [1])
        self.assertNotIn("already_evaluated", run.payload())

    def test_zip_codes_are_searched_instead_of_cities(self):
        self.write("criteria.md", CRITERIA_MD + "- Target Zip Codes: 62701, 62704\n")
        zillow = FakeZillow().install(self)
        run_screen(self.report)
        self.assertEqual(zillow.posts[0]["json"]["urls"], [
            "https://www.zillow.com/homes/for_sale/62701_rb/0-1000000_price/",
            "https://www.zillow.com/homes/for_sale/62704_rb/0-1000000_price/",
        ])

    def test_overrides_change_the_search_and_the_filters(self):
        zillow = FakeZillow(results=[listing(1, "1 A St", city="Brockway", beds=3)]).install(self)
        run = run_screen(self.report, criteria={"cities": ["Brockway"], "min_beds": 4})
        self.assertEqual(zillow.posts[0]["json"]["urls"], ["https://www.zillow.com/homes/for_sale/Brockway-IL/0-1000000_price/"])
        self.assertEqual(run.matches, [])
        self.assertEqual(run.payload()["failed_by_rule"], {"under 4 beds": 1})

    def test_an_override_can_supply_the_state(self):
        self.write("criteria.md", CRITERIA_MD)
        zillow = FakeZillow().install(self)
        run_screen(self.report, criteria={"state": "IL"})
        self.assertEqual(len(zillow.posts[0]["json"]["urls"]), len(CITIES))

    def test_a_listing_the_user_sent_comes_back_with_its_misses(self):
        self.write("properties/120_Maple_Dr_Springfield.md", REPORT_MD)
        zillow = FakeZillow(results=[listing(1, "120 Maple Dr", hoa=120)]).install(self)
        url = "https://www.zillow.com/homedetails/1_zpid/"
        payload = run_screen(self.report, urls=url).payload()
        self.assertEqual(zillow.posts[0]["json"], {"urls": [url]})
        self.assertEqual([(l["zpid"], l["missed"], l["hoa_fee"]) for l in payload["listings"]],
                         [(1, ["HOA over USD 0/mo (USD 120/mo)"], "USD 120")])
        self.assertNotIn("matches", payload)

    def test_a_listing_the_user_sent_needs_no_search_area(self):
        self.write("criteria.md", CRITERIA_MD)  # no state: a search couldn't run, a listing check can
        FakeZillow(results=[listing(1, "1 A St")]).install(self)
        payload = run_screen(self.report, urls=["https://www.zillow.com/homedetails/1_zpid/"]).payload()
        self.assertEqual([l["missed"] for l in payload["listings"]], [[]])
        self.assertEqual(self.report.status, "ok")

    def test_a_pending_job_reads_at_most_max_reads_times(self):
        zillow = FakeZillow(statuses=("processing",)).install(self)
        with self.assertRaises(JobPending) as raised:
            run_screen(self.report, polling=SCAN_POLLING)
        self.assertEqual(raised.exception.job_id, "job-1")
        self.assertEqual((len(zillow.posts), len(zillow.gets)), (1, SCAN_POLLING.max_reads))
        self.assertEqual(zillow.sleeps, [SCAN_POLLING.first_wait] + [SCAN_POLLING.interval] * (SCAN_POLLING.max_reads - 1))

    def test_the_weekly_scan_stays_within_nine_requests(self):
        self.assertEqual((SCAN_REQUESTS, SCAN_POLLING.requests, TOOL_POLLING.requests), (9, 9, 10))
        self.assertEqual(SCAN_POLLING.read_times(), [15, 30, 45, 60, 75, 90, 105, 120])
        self.assertEqual(TOOL_POLLING.read_times(), [8, 14, 20, 26, 32, 38, 44, 50, 56])

    def test_a_job_id_is_read_once_without_a_new_search(self):
        zillow = FakeZillow(results=[listing(2, "2 B St")]).install(self)
        run = run_screen(self.report, job_id="job-1")
        self.assertEqual((len(zillow.posts), len(zillow.gets), zillow.sleeps), (0, 1, []))
        self.assertEqual([m["zpid"] for m in run.matches], [2])
        self.assertIn("Zillow: 1 request, a result read of job job-1", self.report.traces)

    def test_a_job_id_still_running_is_pending(self):
        zillow = FakeZillow(statuses=("processing",)).install(self)
        with self.assertRaises(JobPending):
            run_screen(self.report, job_id="job-1")
        self.assertEqual(zillow.requests, 1)

    def test_criteria_without_a_state_fail_before_any_request(self):
        zillow = FakeZillow().install(self)
        self.write("criteria.md", CRITERIA_MD)
        with self.assertRaises(DataProblem):
            run_screen(self.report)
        self.assertEqual(self.report.status, "failed")
        self.assertEqual(self.report.issue_lines(), [
            "pkm/wallet/real-estate/criteria.md: names no state for its target cities: add a line like "
            "'- **State**: CA', or list zip codes",
        ])
        self.assertEqual(zillow.requests, 0)

    def test_no_criteria_file_fails_before_any_request(self):
        zillow = FakeZillow().install(self)
        os.remove(os.path.join(self.base, "criteria.md"))
        with self.assertRaises(DataProblem):
            run_screen(self.report)
        self.assertEqual(self.report.issue_lines(), ["pkm/wallet/real-estate/criteria.md: file not found"])
        self.assertEqual(zillow.requests, 0)

    def test_a_missing_key_fails_before_any_request(self):
        zillow = FakeZillow().install(self)
        from core.util.config import Config
        Config().rapidapi_key = ""
        with self.assertRaisesRegex(ValueError, "RAPIDAPI_KEY"):
            run_screen(self.report)
        self.assertEqual(zillow.requests, 0)

    def test_a_criteria_line_that_doesnt_read_is_partial_and_the_rest_applies(self):
        self.write("criteria.md", CRITERIA_WITH_STATE + "- **Min Baths**: plenty\n")
        FakeZillow(results=[listing(2, "2 B St")]).install(self)
        run = run_screen(self.report)
        self.assertEqual([m["zpid"] for m in run.matches], [2])
        self.assertEqual(run.unread, ("- **Min Baths**: plenty",))
        self.assertEqual(self.report.status, "partial")
        self.assertEqual(self.report.issues[0].lines, ["- **Min Baths**: plenty"])

    def test_zillow_errors_for_some_urls_are_partial(self):
        FakeZillow(results=[listing(2, "2 B St")], errors=[{"url": "x", "error": "blocked"}]).install(self)
        run = run_screen(self.report)
        self.assertEqual([m["zpid"] for m in run.matches], [2])
        self.assertEqual(self.report.status, "partial")
        self.assertEqual(self.report.issue_lines(), ["Zillow: x: blocked"])
        self.assertNotIn("errors", run.payload())

    def test_no_listings_back_with_errors_is_required_data(self):
        FakeZillow(errors=[{"url": "x", "error": "blocked"}]).install(self)
        with self.assertRaises(DataProblem):
            run_screen(self.report)
        self.assertEqual(self.report.issue_lines(), ["Zillow: x: blocked", "Zillow: job job-1 returned no listings"])

    def test_no_listings_back_without_errors_is_a_dry_search(self):
        FakeZillow().install(self)
        run = run_screen(self.report)
        self.assertEqual((run.pulled, run.matches, self.report.status), (0, [], "ok"))
