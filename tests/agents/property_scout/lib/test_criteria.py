"""criteria.md, read as hard filters: a full file in the user's format, other phrasings, every line accounted for, overrides."""
import os
import shutil
import tempfile
import unittest

from agents.property_scout.lib.criteria import (
    Criteria,
    filter_values,
    home_type_of,
    home_types_in,
    load_criteria,
    parse_criteria,
    with_overrides,
)
from core.tooling import DataProblem, Report
from core.config.settings import Config

# Made up, in the shape of pkm/wallet/real-estate/criteria.md.
CRITERIA_MD = """# Home Criteria

## 1. The Home
- **Target Corridor**: Springfield metro, east side (Springfield to Capital City)
  - Key Target Cities: Springfield, Shelbyville, Ogdenville, North Haverbrook, Capital City
- **Max Purchase Price**: $1,000,000 (Hard Ceiling)
- **Minimum Layout**: 3 Beds, 2 Baths, ≥ 1,400 Sq Ft (Preferred: 4 Beds, 2.5 Baths)
- **Property Type**: Single Family Home (1 or 2 stories)
- **Max HOA**: $0 / No HOA
- **Condition**: Move-in ready, or light cosmetic work
- **Dealbreakers**:
  - Low-rated schools
  - Busy streets or highway noise

## 2. Money
- **Plans**:
  - **Plan A**: Buy a starter home ($900k cap), rent out the spare room.
- **Financing**: 20% down payment (~$180k on $900k).
- **Rental Math**:
  - *Rent-to-Price*: Here ($900k SFH), rents run $3,000–$4,000/mo (Gross Yield ~4% - 5.3%).
  - *Monthly Carry*: With 20% down + property taxes (1.1%) + insurance, monthly PITI is ~$5,500 - $6,000/mo.
"""


class TestParseCriteria(unittest.TestCase):

    def test_a_full_file(self):
        c = parse_criteria(CRITERIA_MD).criteria
        self.assertEqual(c.cities, ("Springfield", "Shelbyville", "Ogdenville", "North Haverbrook", "Capital City"))
        self.assertEqual(c.max_price, 1_000_000)
        self.assertEqual((c.min_beds, c.min_baths, c.min_sqft), (3, 2, 1400))
        self.assertEqual((c.preferred_beds, c.preferred_baths), (4, 2.5))
        self.assertEqual(c.max_hoa, 0)
        self.assertEqual(c.home_types, ("SINGLE_FAMILY",))
        self.assertEqual(c.max_stories, 2)
        self.assertIsNone(c.state)
        self.assertEqual(c.zipcodes, ())
        self.assertIsNone(c.min_cap_rate)

    def test_a_full_file_accounts_for_every_line(self):
        parsed = parse_criteria(CRITERIA_MD)
        self.assertEqual((parsed.unread, parsed.conflicts), ((), ()))
        self.assertEqual(parsed.lines["max_price"], 6)
        self.assertEqual(parsed.lines["min_beds"], 7)
        # Every line that isn't a filter, a heading or blank comes back as written.
        self.assertEqual(parsed.to_judge, (
            "- **Target Corridor**: Springfield metro, east side (Springfield to Capital City)",
            "- **Condition**: Move-in ready, or light cosmetic work",
            "- **Dealbreakers**:",
            "  - Low-rated schools",
            "  - Busy streets or highway noise",
            "- **Plans**:",
            "  - **Plan A**: Buy a starter home ($900k cap), rent out the spare room.",
            "- **Financing**: 20% down payment (~$180k on $900k).",
            "- **Rental Math**:",
            "  - *Rent-to-Price*: Here ($900k SFH), rents run $3,000–$4,000/mo (Gross Yield ~4% - 5.3%).",
            "  - *Monthly Carry*: With 20% down + property taxes (1.1%) + insurance, monthly PITI is "
            "~$5,500 - $6,000/mo.",
        ))

    def test_strategy_and_investment_lines_set_no_filter(self):
        c = parse_criteria(CRITERIA_MD.split("## 2.")[0] + "## 2.\n" + CRITERIA_MD.split("## 2.")[1]).criteria
        self.assertEqual(c.max_price, 1_000_000)  # not the $180k or $900k of the strategy lines

    def test_other_phrasings(self):
        parsed = parse_criteria("\n".join([
            "- Budget: $1.8M",
            "- Min Beds: 4",
            "- Min Baths: 2.5",
            "- Min Sq Ft: 2,000",
            "- Max HOA: none",
            "- Home Type: Townhouse or condo, single-story",
            "- Target Zip Codes: 62701, 62704, 62701",
            "- State: IL",
            "- Min Cap Rate: 4.5%",
            "- Max $/SqFt: $1,200",
        ]))
        c = parsed.criteria
        self.assertEqual(c.max_price, 1_800_000)
        self.assertEqual((c.min_beds, c.min_baths, c.min_sqft), (4, 2.5, 2000))
        self.assertEqual(c.max_hoa, 0)
        self.assertEqual(c.home_types, ("TOWNHOUSE", "CONDO"))
        self.assertEqual(c.max_stories, 1)
        self.assertEqual(c.zipcodes, ("62701", "62704"))
        self.assertEqual(c.state, "IL")
        self.assertEqual(c.min_cap_rate, 4.5)
        # Not a filter the screen has: the agent judges it.
        self.assertEqual(parsed.to_judge, ("- Max $/SqFt: $1,200",))

    def test_money_with_its_currency_code_reads(self):
        c = parse_criteria("- **Max Purchase Price**: USD 1,000,000\n- **Max HOA**: US$200 a month\n").criteria
        self.assertEqual((c.max_price, c.max_hoa), (1_000_000, 200))

    def test_a_lot_size_is_not_the_living_area(self):
        parsed = parse_criteria("- Min Sq Ft: 1,500\n- Lot Size: 6,000 sq ft\n")
        self.assertEqual(parsed.criteria.min_sqft, 1500)
        self.assertEqual(parsed.to_judge, ("- Lot Size: 6,000 sq ft",))

    def test_a_filter_line_that_doesnt_read_comes_back_raw(self):
        parsed = parse_criteria("\n".join([
            "- **State**: Illinois",
            "- **Max HOA**: ask me",
            "- **Minimum Layout**: roomy",
            "- **Key Target Cities**:",
            "- **Max Purchase Price**: $2,000,000",
        ]))
        self.assertEqual(parsed.unread, (
            "- **State**: Illinois", "- **Max HOA**: ask me", "- **Minimum Layout**: roomy", "- **Key Target Cities**:",
        ))
        self.assertEqual(parsed.criteria, Criteria(max_price=2_000_000))
        self.assertEqual(parsed.to_judge, ())

    def test_headings_rules_and_blank_lines_are_structure(self):
        parsed = parse_criteria("# Criteria\n\n---\n## Money\n***\n- Max Price: $1,000,000\n")
        self.assertEqual((parsed.to_judge, parsed.unread), ((), ()))
        self.assertEqual(parsed.lines, {"max_price": 6})

    def test_the_first_line_for_a_filter_wins_and_a_conflict_is_reported(self):
        parsed = parse_criteria("- Max Price: $1,000,000\n- Max Price: $2,000,000\n- Budget: $1M\n")
        self.assertEqual(parsed.criteria.max_price, 1_000_000)
        self.assertEqual(parsed.conflicts, ("line 2 ('- Max Price: $2,000,000') sets max_price again; line 1's value applies",))

    def test_filter_values(self):
        self.assertEqual(filter_values("- **Max HOA**: $350/mo"), {"max_hoa": 350})
        self.assertEqual(filter_values("- **Max HOA**: ask me"), {})
        self.assertIsNone(filter_values("- **Dealbreakers**: busy roads"))
        self.assertIsNone(filter_values("  - Low-rated schools"))


class TestSearchAreas(unittest.TestCase):

    def test_zip_codes_win_over_cities(self):
        c = Criteria(cities=("Shelbyville",), zipcodes=("62702",), state="IL")
        self.assertEqual(c.search_areas(), ["62702"])
        self.assertIsNone(c.area_problem())

    def test_cities_need_a_state(self):
        self.assertIn("names no state", Criteria(cities=("Shelbyville",)).area_problem())
        with self.assertRaisesRegex(ValueError, "names no state"):
            Criteria(cities=("Shelbyville",)).search_areas()
        self.assertEqual(Criteria(cities=("Shelbyville", "Ogdenville"), state="IL").search_areas(),
                         ["Shelbyville, IL", "Ogdenville, IL"])

    def test_nowhere_to_search(self):
        self.assertEqual(Criteria(state="IL").area_problem(), "names no target cities or zip codes")
        with self.assertRaisesRegex(ValueError, "no target cities or zip codes"):
            Criteria(state="IL").search_areas()

    def test_in_force_lists_only_the_set_filters(self):
        self.assertEqual(Criteria(max_price=2e6, home_types=("CONDO",)).in_force(), {"max_price": 2e6, "home_types": ["CONDO"]})


class TestOverrides(unittest.TestCase):

    def test_overrides_replace_coerce_and_drop(self):
        base = parse_criteria(CRITERIA_MD).criteria
        c = with_overrides(base, {"cities": ["Brockway"], "state": "il", "min_beds": "4", "max_price": "$2,000,000",
                                  "max_hoa": None, "home_types": "single family, condo", "max_stories": 3.0})
        self.assertEqual((c.cities, c.state, c.min_beds, c.max_price), (("Brockway",), "IL", 4.0, 2_000_000))
        self.assertIsNone(c.max_hoa)
        self.assertEqual(c.home_types, ("SINGLE_FAMILY", "CONDO"))
        self.assertEqual(c.max_stories, 3)
        self.assertEqual(c.min_sqft, base.min_sqft)

    def test_money_as_the_tool_writes_it(self):
        c = with_overrides(Criteria(), {"max_price": "USD 3,000,000", "max_hoa": "USD 0"})
        self.assertEqual((c.max_price, c.max_hoa), (3_000_000, 0))

    def test_naming_cities_searches_them_instead_of_the_files_zips(self):
        c = with_overrides(Criteria(zipcodes=("62701",), state="IL"), {"cities": "Shelbyville"})
        self.assertEqual(c.search_areas(), ["Shelbyville, IL"])

    def test_bad_overrides(self):
        with self.assertRaisesRegex(ValueError, "unknown criteria key"):
            with_overrides(Criteria(), {"school_rating": 9})
        with self.assertRaisesRegex(ValueError, "must be a number"):
            with_overrides(Criteria(), {"min_beds": "four"})


class TestHomeTypes(unittest.TestCase):

    def test_phrases_and_zillow_spellings(self):
        self.assertEqual(home_types_in("Single Family Home (1 or 2 stories)"), ("SINGLE_FAMILY",))
        self.assertEqual(home_types_in("town house"), ("TOWNHOUSE",))
        for value, expected in [("SINGLE_FAMILY", "SINGLE_FAMILY"), ("Single Family Residence", "SINGLE_FAMILY"),
                                ("SingleFamily", "SINGLE_FAMILY"), ("TOWNHOUSE", "TOWNHOUSE"), ("CONDO", "CONDO"),
                                ("LOT", "LOT"), ("Manufactured", "MANUFACTURED"), (None, None)]:
            with self.subTest(value=value):
                self.assertEqual(home_type_of(value), expected)


class TestLoadCriteria(unittest.TestCase):

    def setUp(self):
        self.pkm = tempfile.mkdtemp()
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.addCleanup(shutil.rmtree, self.pkm, True)
        self.path = os.path.join(self.pkm, "wallet", "real-estate", "criteria.md")

    def write(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_a_missing_file_is_required_data(self):
        report = Report()
        with self.assertRaises(DataProblem):
            load_criteria(report)
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.issue_lines(), ["pkm/wallet/real-estate/criteria.md: file not found"])

    def test_reads_the_file_under_pkm(self):
        self.write(CRITERIA_MD)
        report = Report(explain=True)
        self.assertEqual(load_criteria(report).criteria.max_price, 1_000_000)
        self.assertEqual(report.status, "ok")
        self.assertIn("pkm/wallet/real-estate/criteria.md (modified", report.render_explain())
        self.assertIn("10 records", report.render_explain())
        self.assertIn("criteria.md line 6: max_price = USD 1,000,000", report.traces)

    def test_lines_that_dont_read_and_conflicts_make_it_partial(self):
        self.write("- State: Illinois\n- Max Price: $1,000,000\n- Budget: $2M\n")
        report = Report()
        load_criteria(report)
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.issue_lines(), [
            "pkm/wallet/real-estate/criteria.md: 1 line not recognized",
            "pkm/wallet/real-estate/criteria.md: line 3 ('- Budget: $2M') sets max_price again; line 2's value applies",
        ])
        self.assertEqual(report.issues[0].lines, ["- State: Illinois"])


if __name__ == "__main__":
    unittest.main()
