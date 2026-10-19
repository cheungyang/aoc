"""agents/reward-travel/lib/card_files.py: reading the card files' tables and lines."""
import os
import unittest
from datetime import date

from core.tooling import Report
from tests.agents.reward_travel import PORTFOLIO, STATEMENT_CREDITS, CardFilesCase
from agents.reward_travel.lib.card_files import (
    AUTO,
    CARDMEMBER,
    MISSED,
    OPEN,
    UNKNOWN,
    USED,
    parse_card_file,
    parse_entry,
    read_card_files,
)

TABLE = "| Credit & Card | Monthly Value | Win Rate | Log |\n| --- | --- | --- | --- |\n"


class TestCreditRows(unittest.TestCase):

    def setUp(self):
        self.credits = {c.name: c for c in parse_card_file("statement_credits.md", STATEMENT_CREDITS).credits}

    def test_every_table_row_is_a_credit_with_its_cycle(self):
        cycles = {name: c.cycle for name, c in self.credits.items()}
        self.assertEqual(cycles, {
            "Combined Rideshare Credit (Example Premium + Dining)": "monthly",
            "Sam's Coffee Credit (Example Dining)": "monthly",
            "Example Air Monthly Credits": "monthly",
            "Dining Credit (Example Premium)": "quarterly",
            "Fitness Credit (Example Premium)": "quarterly",
            "Dining Credit (Example Dining)": "semiannual",
            "Hotel Credit (Luxury/Boutique)": "semiannual",
            "Example Travel Card Credit": CARDMEMBER,
            "Example Premium Airline Credit": "annual",
            "Store Reward Certificate": "annual",
        })

    def test_values_carry_iso_codes_and_sources(self):
        rideshare = self.credits["Combined Rideshare Credit (Example Premium + Dining)"]
        self.assertEqual((rideshare.value, rideshare.source), ("15 USD / mo (20 USD Dec)", "statement_credits.md"))
        self.assertEqual(self.credits["Store Reward Certificate"].value, "Variable")

    def test_log_entries_and_statuses(self):
        coffee = self.credits["Sam's Coffee Credit (Example Dining)"]
        self.assertEqual([(e.period.label, e.status) for e in coffee.entries], [("May 2026", MISSED), ("Jun 2026", USED)])
        airline = self.credits["Example Premium Airline Credit"].entries[-1]
        self.assertEqual((airline.status, airline.remaining), (OPEN, "135 USD"))

    def test_remarks_without_a_period_are_notes(self):
        air = self.credits["Example Air Monthly Credits"]
        self.assertEqual((air.entries, air.notes), ((), ("Monthly Transit ($10)", "Monthly Grocery Delivery ($7 + $5)")))
        travel = self.credits["Example Travel Card Credit"]
        self.assertEqual(travel.notes, ("Next credit available after Jul 2027 renewal.",))
        self.assertEqual(len(travel.entries), 2)

    def test_entry_statuses(self):
        cases = {
            "2026 H2: **Active / Pending Use**": OPEN,
            "2026 Q3 (Oct–Dec): Upcoming": OPEN,
            "2026 H1: **MISSED** ❌ (2026-07-02)": MISSED,
            "2025: Redeemed via airline gift card 2x$90 (2025-11-30)": USED,
            "2026: $180 used. $0 Remaining": USED,
            "2026: 45 USD used. 135 USD Remaining": OPEN,
            "Oct 2026: not used yet": OPEN,
            "Monthly Transit ($10)": UNKNOWN,
            "Auto (Example TV)": AUTO,
        }
        for text, status in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_entry(text).status, status)

    def test_auto_entries_name_what_uses_the_credit(self):
        self.assertEqual(parse_entry("Auto (Example TV)").by, "Example TV")
        self.assertEqual(parse_entry("Auto: Example TV Premium").by, "Example TV Premium")
        self.assertIsNone(parse_entry("Auto").by)
        for text in ("Automatic reload", "auto-credited monthly"):
            with self.subTest(text=text):
                self.assertNotEqual(parse_entry(text).status, AUTO)
        self.assertEqual(parse_entry("135 USD remaining").remaining, "135 USD")


class TestCoverage(unittest.TestCase):
    """What the parser can't read comes back raw, never dropped quietly."""

    def parse(self, text):
        report = Report()
        cards = parse_card_file("statement_credits.md", text, report)
        return cards, report

    def test_the_fixture_reads_cleanly(self):
        _, report = self.parse(STATEMENT_CREDITS)
        self.assertEqual(report.status, "ok")
        self.assertEqual((report.reads[0].records, report.reads[0].skipped), (10, 0))

    def test_entries_whose_use_or_period_cant_be_told_come_back_raw(self):
        text = TABLE + ("| **Sam's Coffee Credit** *(Example Dining)* | $5 / mo | 50% | • Sep 2026: grabbed coffee<br>"
                        "• Redeemed (2026-10-12)<br>• Oct 2026: Redeemed |\n")
        cards, report = self.parse(text)
        self.assertEqual(report.status, "partial")
        issue = report.issues[0]
        self.assertEqual(issue.problem, "2 lines not recognized")
        self.assertEqual(issue.lines, ["Sam's Coffee Credit (Example Dining): Sep 2026: grabbed coffee",
                                       "Sam's Coffee Credit (Example Dining): Redeemed (2026-10-12)"])
        self.assertEqual([e.period.label for e in cards.credits[0].entries], ["Oct 2026"])
        self.assertEqual(report.reads[0].skipped, 2)

    def test_rows_that_are_not_credits_come_back_raw(self):
        text = (TABLE + "|  | $5 / mo |  | • Oct 2026: Redeemed |\n|  |  |  |  |\n"
                "\n| Credit | Value |\n| --- | --- |\n| Bookstore | $30 / half-year |\n")
        cards, report = self.parse(text)
        self.assertEqual(cards.credits, [])
        self.assertEqual(report.issues[0].lines, ["|  | $5 / mo |  | • Oct 2026: Redeemed |", "| Bookstore | $30 / half-year |"])

    def test_a_row_without_a_cycle_is_an_issue(self):
        text = "| Benefit | Value | Log |\n| --- | --- | --- |\n| **Lounge Pass** | $120 | • 2026: Redeemed |\n"
        cards, report = self.parse(text)
        self.assertIsNone(cards.credits[0].cycle)
        self.assertEqual(report.status, "partial")
        self.assertIn("'Lounge Pass': no cycle", report.issue_lines()[0])

    def test_tables_without_a_value_column_and_prose_are_left_alone(self):
        text = "# Notes\n* Status: **$135 Remaining**\n\n| Card | Fee |\n| --- | --- |\n| Example Dining | $95 |\n" + TABLE
        _, report = self.parse(text)
        self.assertEqual(report.status, "ok")

    def test_an_unreadable_last_updated_date_is_an_issue(self):
        _, report = self.parse("**Last Updated:** sometime in fall\n")
        self.assertIn("**Last Updated:** 'sometime in fall' isn't a date", report.issue_lines()[0])


class TestFileLines(unittest.TestCase):

    def test_five_24_per_player(self):
        lines = parse_card_file("portfolio_summary.md", PORTFOLIO).five_24
        self.assertEqual([(p.player, p.count, p.text) for p in lines], [
            ("Player 1 (You)", 3, "~3/24 (Example Air & Example Hotel in 2026)"),
            ("Player 2 (Pat — Managed by You)", 1, "~0/24 to 1/24"),
        ])

    def test_last_updated(self):
        info = parse_card_file("statement_credits.md", STATEMENT_CREDITS).files[0]
        self.assertEqual((info.last_updated, info.updated), ("August 30, 2026", date(2026, 8, 30)))
        dated = parse_card_file("x.md", "**Last Updated:** October 9, 2026\n").files[0]
        self.assertEqual(dated.updated, date(2026, 10, 9))
        self.assertIsNone(parse_card_file("x.md", "# No date\n").files[0].last_updated)


class TestReadFolder(CardFilesCase):

    def test_reads_every_markdown_file(self):
        self.write("notes.txt", "| a | Value | log |\n| --- | --- | --- |\n| x | $1 / mo | Oct 2026: Redeemed |\n")
        report = Report()
        cards = read_card_files(self.folder, report)
        self.assertEqual(len(cards.credits), 10)
        self.assertEqual([f.name for f in cards.files], ["portfolio_summary.md", "statement_credits.md"])
        self.assertEqual(report.status, "ok")
        self.assertEqual([r.source for r in report.reads],
                         ["pkm/wallet/credit-card/portfolio_summary.md", "pkm/wallet/credit-card/statement_credits.md"])

    def test_a_missing_folder_is_required_data(self):
        report = Report()
        self.assertEqual(read_card_files(self.folder + "-gone", report).credits, [])
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.issue_lines(), ["pkm/wallet/credit-card-gone: folder not found"])

    def test_no_credit_table_is_required_data(self):
        os.remove(os.path.join(self.folder, "statement_credits.md"))
        report = Report()
        read_card_files(self.folder, report)
        self.assertEqual(report.status, "failed")
        self.assertIn("no statement-credit table", report.issue_lines()[0])


if __name__ == "__main__":
    unittest.main()
