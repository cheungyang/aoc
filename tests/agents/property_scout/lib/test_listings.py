"""The hard filters over Zillow listings, with the listing tool's numbers copied as they are."""
import unittest

from tests.agents.property_scout import enriched
from agents.property_scout.lib.criteria import Criteria
from agents.property_scout.lib.listings import listing_row, match_traces, misses, render_matches, screen
from agents.property_scout.lib.money import usd, with_currency

CRITERIA = Criteria(
    max_price=1_000_000, min_beds=3, min_baths=2, min_sqft=1400, max_hoa=0,
    home_types=("SINGLE_FAMILY",), max_stories=2, preferred_beds=4, preferred_baths=2.5,
)


def missed(**overrides):
    row = listing_row(enriched(1, "1 Main St", **overrides))
    return [rule for rule, _ in misses(row, CRITERIA)]


class TestMoney(unittest.TestCase):

    def test_amounts_carry_their_currency_code(self):
        self.assertEqual((usd(849_000), usd(597.887, 2), usd(0), usd(None), usd("n/a")),
                         ("USD 849,000", "USD 597.89", "USD 0", None, None))

    def test_with_currency_writes_only_the_money_fields(self):
        row = {"price": 849_000, "hoa_fee": None, "price_per_sqft": 597.89, "sqft": 1420, "max_hoa": 0}
        self.assertEqual(with_currency(row), {"price": "USD 849,000", "hoa_fee": None, "price_per_sqft": "USD 597.89",
                                              "sqft": 1420, "max_hoa": "USD 0"})


class TestListingRow(unittest.TestCase):

    def test_carries_the_tool_numbers_unchanged(self):
        row = listing_row(enriched(10000001, "120 Maple Dr"))
        self.assertEqual(row["address"], "120 Maple Dr, Springfield, IL 62701")
        self.assertEqual(row["url"], "https://www.zillow.com/homedetails/10000001_zpid/")
        self.assertEqual((row["price"], row["beds"], row["baths"], row["sqft"]), (849_000, 3, 2.0, 1420))
        # The report on file for this house: $597.89/sqft, cap rate ~5.16%.
        self.assertEqual(row["price_per_sqft"], 597.89)
        self.assertEqual(row["estimated_cap_rate_percent"], 5.16)
        self.assertEqual(row["estimated_annual_gross_rent"], 43800.0)
        self.assertEqual(row["home_type"], "SINGLE_FAMILY")
        self.assertNotIn("stories", row)

    def test_long_descriptions_are_clipped(self):
        row = listing_row(enriched(1, "1 Main St", description="word " * 400))
        self.assertTrue(row["description"].endswith("…"))
        self.assertLessEqual(len(row["description"]), 1201)


class TestMisses(unittest.TestCase):

    def test_each_boundary_passes_and_one_past_it_fails(self):
        self.assertEqual(missed(price=1_000_000, beds=3, baths=2, sqft=1400, hoa=0), [])
        self.assertEqual(missed(price=1_000_001), ["over USD 1,000,000"])
        self.assertEqual(missed(beds=2), ["under 3 beds"])
        self.assertEqual(missed(baths=1.5), ["under 2 baths"])
        self.assertEqual(missed(sqft=1399), ["under 1,400 sqft"])
        self.assertEqual(missed(hoa=1), ["HOA over USD 0/mo"])
        self.assertEqual(missed(home_type="CONDO"), ["not single family"])

    def test_a_missing_core_fact_fails_but_missing_extras_pass(self):
        self.assertEqual(missed(price=None, sqft=None), ["price not listed", "sqft not listed"])
        self.assertEqual(missed(hoa=None, home_type=None), [])

    def test_stories_and_cap_rate(self):
        row = listing_row({**enriched(1, "1 Main St"), "stories": 3})
        self.assertEqual([r for r, _ in misses(row, CRITERIA)], ["over 2 stories"])
        strict = Criteria(min_cap_rate=6.0)
        self.assertEqual(misses(listing_row(enriched(1, "1 Main St")), strict), [("cap rate under 6%", "5.16%")])
        self.assertEqual(misses(listing_row(enriched(1, "1 Main St", rent=None)), strict), [("no cap rate (no rent estimate)", "")])

    def test_a_fail_names_the_listings_value(self):
        row = listing_row(enriched(1, "1 Main St", hoa=350))
        self.assertEqual(misses(row, CRITERIA), [("HOA over USD 0/mo", "USD 350/mo")])


class TestScreen(unittest.TestCase):

    def test_matches_preferred_first_then_lowest_price_per_sqft(self):
        result = screen([
            enriched(1, "1 Small Pl", price=800_000, sqft=1600),                  # 500/sqft
            enriched(2, "2 Big Ave", price=960_000, sqft=2400, beds=4, baths=3),  # preferred, 400/sqft
            enriched(3, "3 Value Ct", price=720_000, sqft=2000),                  # 360/sqft
            enriched(4, "4 Condo Way", home_type="CONDO"),
        ], CRITERIA)
        self.assertEqual([m["zpid"] for m in result.matches], [2, 3, 1])
        self.assertEqual([m["meets_preferred"] for m in result.matches], [True, False, False])
        self.assertEqual(result.pulled, 4)
        self.assertEqual([r["zpid"] for r in result.rejected], [4])
        self.assertEqual(result.rejected[0]["missed"], ["not single family (condo)"])

    def test_counts_rejections_by_filter_and_keeps_one_filter_near_misses(self):
        result = screen([
            enriched(1, "1 A St", hoa=350),
            enriched(2, "2 B St", price=1_040_000, sqft=1300),
            enriched(3, "3 C St", price=1_080_000),
        ], CRITERIA)
        self.assertEqual(result.matches, [])
        self.assertEqual(result.failed_by_rule, {"HOA over USD 0/mo": 1, "over USD 1,000,000": 2, "under 1,400 sqft": 1})
        self.assertEqual(
            [(n["address"], n["missed"]) for n in result.near_misses],
            [("1 A St, Springfield, IL 62701", "HOA over USD 0/mo (USD 350/mo)"),
             ("3 C St, Springfield, IL 62701", "over USD 1,000,000 (USD 1,080,000)")],
        )

    def test_city_baseline_needs_three_listings(self):
        result = screen([
            enriched(1, "1 A St", city="Shelbyville", price=800_000, sqft=2000),  # 400/sqft
            enriched(2, "2 B St", city="Shelbyville", price=880_000, sqft=2000),  # 440
            enriched(3, "3 C St", city="Shelbyville", price=960_000, sqft=2000),  # 480
            enriched(4, "4 D St", city="Ogdenville", price=800_000, sqft=2000),
        ], CRITERIA)
        by_zpid = {m["zpid"]: m for m in result.matches}
        self.assertEqual(by_zpid[1]["area_median_price_per_sqft"], 440.0)
        self.assertEqual(by_zpid[1]["vs_area_median_pct"], -9.1)
        self.assertNotIn("area_median_price_per_sqft", by_zpid[4])

    def test_each_listing_once(self):
        result = screen([enriched(1, "1 A St"), enriched(1, "1 A St"), "not a listing"], CRITERIA)
        self.assertEqual((result.pulled, len(result.matches)), (1, 1))


class TestTraces(unittest.TestCase):

    def test_a_match_shows_the_inputs_behind_its_numbers(self):
        result = screen([
            enriched(1, "1 A St", city="Shelbyville", price=800_000, sqft=2000, rent=2400),
            enriched(2, "2 B St", city="Shelbyville", price=880_000, sqft=2000, rent=None),
            enriched(3, "3 C St", city="Shelbyville", price=960_000, sqft=2000, hoa=50),
        ], Criteria(max_price=1_000_000))
        self.assertEqual(result.traces[:3], [
            "1 A St, Shelbyville, IL 62701: USD 400.00/sqft = USD 800,000 / 2,000 sqft",
            "1 A St, Shelbyville, IL 62701: city median USD 440.00/sqft over 3 listings pulled in its city; -9.1%",
            "1 A St, Shelbyville, IL 62701: est. cap rate 3.6% = (USD 2,400 - HOA USD 0) x 12 / USD 800,000; "
            "gross yield 3.6% = USD 2,400 x 12 / USD 800,000",
        ])
        self.assertIn("2 B St, Shelbyville, IL 62701: no rent estimate, so no cap rate or yield", result.traces)
        self.assertIn("3 C St, Shelbyville, IL 62701: est. cap rate 4.5% = (USD 3,650 - HOA USD 50) x 12 / USD 960,000; "
                      "gross yield 4.56% = USD 3,650 x 12 / USD 960,000", result.traces)

    def test_each_filter_names_up_to_three_rejections(self):
        result = screen([enriched(n, f"{n} Pricey Rd", price=1_100_000 + n) for n in range(1, 6)], CRITERIA)
        self.assertEqual(result.traces, [
            "over USD 1,000,000: 5 listings: 1 Pricey Rd, Springfield, IL 62701 (USD 1,100,001); "
            "2 Pricey Rd, Springfield, IL 62701 (USD 1,100,002); 3 Pricey Rd, Springfield, IL 62701 (USD 1,100,003), and 2 more",
        ])

    def test_match_traces_without_a_city_median(self):
        row = listing_row(enriched(10000001, "120 Maple Dr"))
        self.assertEqual(match_traces(row), [
            "120 Maple Dr, Springfield, IL 62701: USD 597.89/sqft = USD 849,000 / 1,420 sqft",
            "120 Maple Dr, Springfield, IL 62701: est. cap rate 5.16% = (USD 3,650 - HOA USD 0) x 12 / USD 849,000; "
            "gross yield 5.16% = USD 3,650 x 12 / USD 849,000",
        ])


class TestRenderMatches(unittest.TestCase):

    def test_formats_the_tool_numbers(self):
        rows = screen([enriched(10000001, "120 Maple Dr")], CRITERIA).matches
        text = render_matches(rows)
        self.assertIn("1. 120 Maple Dr, Springfield, IL 62701", text)
        self.assertIn("https://www.zillow.com/homedetails/10000001_zpid/", text)
        self.assertIn("USD 849,000 | 3 bd / 2 ba | 1,420 sqft | USD 597.89/sqft | HOA none listed | single family | "
                      "below preferred layout", text)
        self.assertIn("Rent est. USD 3,650/mo (USD 43,800/yr) | gross yield 5.16% | est. cap rate 5.16%", text)
        self.assertIn("Description: Updated kitchen.", text)
        self.assertNotIn("$", text)

    def test_no_rent_estimate(self):
        rows = screen([enriched(1, "1 A St", rent=None)], CRITERIA).matches
        self.assertIn("No rent estimate: cap rate n/a", render_matches(rows))

    def test_a_city_median_and_an_hoa(self):
        rows = screen([enriched(n, f"{n} A St", price=2_000_000 + n * 100_000, sqft=2000, hoa=120) for n in range(3)],
                      Criteria()).matches
        text = render_matches(rows)
        self.assertIn("USD 1,000.00/sqft (city median USD 1,050.00, -4.8%) | HOA USD 120/mo", text)


if __name__ == "__main__":
    unittest.main()
