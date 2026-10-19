"""agents/receipt-processor/lib/ledger.py: checked, converted blocks appended to the month's ledger."""
import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from core.tooling import Report
from core.util.fx import FxError, Rate
from agents.receipt_processor.lib.ledger import (
    PENDING,
    LedgerError,
    LedgerScan,
    SavedEntry,
    check_totals,
    entry_path,
    has_entry,
    iso_amount,
    parse_receipt,
    read_ledger,
    render_block,
    save_receipt,
    summary,
)

TODAY = date(2026, 10, 16)
USD = Rate("USD", Decimal(1), "2026-10-05", "par")
TWD = Rate("TWD", Decimal("0.03127"), "2026-07-09", "currency-api")

DINER = """### 2026-10-05: Sample Diner (Springfield)
**Category:** Restaurant | **Payment Method:** Visa (****0000)

| Item | Original Price (USD) | USD Price |
| :--- | :--- | :--- |
| 1 Club Sandwich | $16.50 | $16.50 |
| 1 Garden Salad | $12.00 | $12.00 |
| 1 Mushroom Risotto | $24.00 | $24.00 |
| 1 Crème Brûlée | $9.50 | $9.50 |
| Subtotal | $62.00 | $62.00 |
| Tax | $5.81 | $5.81 |
| Tip | $11.19 | $11.19 |
| **Total** | **$79.00** | **$79.00** |
"""

CAFE = """### 2026-07-09: 範例咖啡館
**Category:** Dining/Cafe | **Payment Method:** Credit Card (ending in 000)

| Item | Original Price (TWD) | USD Price |
| :--- | :--- | :--- |
| 烏龍茶 | 95 TWD | $2.97 |
| **Total** | **95 TWD** | **$2.97** |
"""


def diner(**overrides):
    fields = dict(
        date_text="2026-10-05",
        vendor="Sample Diner (Springfield)",
        items=[
            {"name": "1 Club Sandwich", "amount": 16.50},
            {"name": "1 Garden Salad", "amount": 12.00},
            {"name": "1 Mushroom Risotto", "amount": 24.00},
            {"name": "1 Crème Brûlée", "amount": 9.50},
        ],
        total=79.00,
        today=TODAY,
        category="Restaurant",
        payment_method="Visa (****0000)",
        taxes_and_fees=[{"name": "Tax", "amount": 5.81}],
        tip=11.19,
        subtotal=62.00,
    )
    fields.update(overrides)
    return parse_receipt(**fields)


def cafe(**overrides):
    fields = dict(
        date_text="2026-07-09", vendor="範例咖啡館", items=[{"name": "烏龍茶", "amount": 95}], total=95,
        today=TODAY, currency="twd", category="Dining/Cafe", payment_method="Credit Card (ending in 000)",
    )
    fields.update(overrides)
    return parse_receipt(**fields)


class TestRender(unittest.TestCase):

    def test_usd_restaurant_block_matches_the_ledger(self):
        self.assertEqual(render_block(diner(), USD), DINER)

    def test_foreign_block_shows_whole_units_and_converted_cents(self):
        self.assertEqual(render_block(cafe(), TWD), CAFE)

    def test_discounts_and_coupons_are_negative_rows_with_no_subtotal_without_tax(self):
        receipt = parse_receipt(
            date_text="2026-10-05", vendor="Corner Market", today=TODAY, total=7.98,
            items=[{"name": "PAPER TOWELS 6PK", "amount": 3.49}, {"name": "- You Saved: $0.50", "amount": -0.50},
                   {"name": "DISH SOAP 24OZ", "amount": 5.99}, {"name": "CPN: $1 off | coupon", "amount": -1.00}],
        )
        block = render_block(receipt, USD)
        self.assertIn("| - You Saved: $0.50 | -$0.50 | -$0.50 |", block)
        self.assertIn("| CPN: $1 off \\| coupon | -$1.00 | -$1.00 |", block)
        self.assertNotIn("Subtotal", block)
        self.assertTrue(block.endswith("| **Total** | **$7.98** | **$7.98** |\n"))

    def test_one_line_receipt_with_a_tip_skips_the_subtotal_row(self):
        receipt = parse_receipt(date_text="2026-10-06", vendor="Sample Grill", today=TODAY, total=48.00,
                                items=[{"name": "Subtotal / Amount", "amount": 40.00}], tip=8.00)
        self.assertEqual(render_block(receipt, USD).count("Subtotal"), 1)

    def test_decimals_kept_for_a_whole_unit_currency_when_any_amount_has_them(self):
        receipt = cafe(items=[{"name": "Tea", "amount": 45.5}, {"name": "Cake", "amount": 49.5}])
        self.assertIn("| Tea | 45.50 TWD |", render_block(receipt, TWD))

    def test_unknown_fields_are_pending_review(self):
        receipt = diner(date_text="", category="", payment_method=" ")
        self.assertEqual(receipt.pending, ["date", "category", "payment method"])
        block = render_block(receipt, USD)
        self.assertTrue(block.startswith(f"### {PENDING}: Sample Diner"))
        self.assertIn(f"**Category:** {PENDING} | **Payment Method:** {PENDING}", block)


class TestChecks(unittest.TestCase):

    def test_matching_totals_pass_and_say_the_sum(self):
        self.assertEqual(
            check_totals(diner()),
            "Items 62.00 USD + taxes and fees 5.81 USD + tip 11.19 USD = 79.00 USD, the printed total",
        )

    def test_a_missing_item_against_the_subtotal(self):
        receipt = diner(items=[{"name": "1 Crème Brûlée", "amount": 9.50}])
        with self.assertRaises(LedgerError) as caught:
            check_totals(receipt)
        self.assertIn("The items add up to 9.50 USD, but the printed subtotal is 62.00 USD "
                      "(the lines are 52.50 USD short)", str(caught.exception))
        self.assertIn("Nothing was saved", str(caught.exception))

    def test_a_wrong_tip_against_the_total(self):
        with self.assertRaises(LedgerError) as caught:
            check_totals(diner(tip=12.19))
        self.assertIn("= 80.00 USD, but the printed total is 79.00 USD (the lines are 1.00 USD over)",
                      str(caught.exception))

    def test_a_whole_unit_currency_keeps_whole_units_in_messages(self):
        with self.assertRaises(LedgerError) as caught:
            check_totals(cafe(total=105))
        self.assertIn("= 95 TWD, but the printed total is 105 TWD (the lines are 10 TWD short)", str(caught.exception))

    def test_bad_fields(self):
        bad = [
            dict(vendor=" "), dict(items=[]), dict(currency="dollars"), dict(date_text="10/05/2026"),
            dict(date_text="2026-10-18"), dict(tip=-1), dict(items=[{"name": "x"}]), dict(items=[{"amount": 1}]),
        ]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(LedgerError):
                diner(**overrides)

    def test_a_receipt_dated_tomorrow_is_from_ahead_of_us(self):
        self.assertEqual(diner(date_text="2026-10-17").date, date(2026, 10, 17))


class TestSave(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pkm = self._tmp.name
        patcher = patch("agents.receipt_processor.lib.ledger.usd_rate", side_effect=self.fake_rate)
        self.usd_rate = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def fake_rate(currency, on, today):
        return TWD if currency == "TWD" else Rate(currency, Decimal(1), str(on), "par")

    def ledger(self, month):
        return os.path.join(self.pkm, "wallet", "finance", f"{month}.md")

    def read(self, month):
        with open(self.ledger(month), encoding="utf-8") as f:
            return f.read()

    def write(self, month, text):
        os.makedirs(os.path.dirname(self.ledger(month)), exist_ok=True)
        with open(self.ledger(month), "w", encoding="utf-8") as f:
            f.write(text)

    def save(self, receipt, report=None, **kwargs):
        return save_receipt(receipt, pkm_dir=self.pkm, today=TODAY, report=report or Report(), **kwargs)

    def test_creates_the_month_file_and_converts_at_the_receipt_date(self):
        saved = self.save(cafe())
        self.assertEqual(saved.path, self.ledger("2026-07"))
        self.assertEqual(self.read("2026-07"), CAFE)
        self.assertEqual(saved.usd_total, Decimal("2.97"))
        self.usd_rate.assert_called_once_with("TWD", date(2026, 7, 9), TODAY)

    def test_appends_one_blank_line_after_whatever_the_file_ends_with(self):
        for ending in ("| **Total** | **$1.00** | **$1.00** |", "| **Total** | **$1.00** | **$1.00** |\n", "x |\n\n"):
            with self.subTest(ending=ending):
                self.write("2026-10", "### 2026-10-06: Sample Grill\n" + ending)
                self.save(diner())
                text = self.read("2026-10")
                self.assertTrue(text.startswith("### 2026-10-06: Sample Grill\n"))
                self.assertIn("|\n\n### 2026-10-05: Sample Diner", text)
                self.assertTrue(text.endswith(DINER))

    def test_refuses_a_duplicate_unless_allowed(self):
        self.save(diner())
        with self.assertRaises(LedgerError) as caught:
            self.save(diner())
        self.assertIn("2026-10.md already has \"### 2026-10-05: Sample Diner (Springfield)\" with total 79.00 USD. "
                      "Nothing was saved.", str(caught.exception))
        self.assertEqual(self.read("2026-10").count("### 2026-10-05: Sample Diner"), 1)
        self.save(diner(), allow_duplicate=True)
        self.assertEqual(self.read("2026-10").count("### 2026-10-05: Sample Diner"), 2)

    def test_same_vendor_and_day_with_another_total_is_not_a_duplicate(self):
        self.save(diner())
        self.save(diner(tip=None, total=67.81))
        self.assertEqual(self.read("2026-10").count("### 2026-10-05: Sample Diner"), 2)

    def test_nothing_is_written_when_the_total_is_off(self):
        with self.assertRaises(LedgerError):
            self.save(diner(total=80.00))
        self.assertFalse(os.path.exists(self.ledger("2026-10")))
        self.usd_rate.assert_not_called()

    def test_no_rate_is_a_ledger_error_and_writes_nothing(self):
        self.usd_rate.side_effect = FxError("No USD rate for TWD on 2026-07-09 or the 3 days before: "
                                            "currency-api answered HTTP 503")
        with self.assertRaises(LedgerError) as caught:
            self.save(cafe())
        self.assertEqual(str(caught.exception),
                         "No USD rate for TWD on 2026-07-09 or the 3 days before: currency-api answered HTTP 503")
        self.assertFalse(os.path.exists(self.ledger("2026-07")))

    def test_an_undated_receipt_goes_to_this_month(self):
        receipt = diner(date_text="")
        self.assertEqual(entry_path(receipt, self.pkm, TODAY), self.ledger("2026-10"))

    def test_has_entry_reads_the_total_row_case_insensitively(self):
        text = "### 2026-10-14: Corner Market\n| x | $1 | $1 |\n| **TOTAL** | **$84.12** | **$84.12** |\n"
        self.assertTrue(has_entry(text, "### 2026-10-14: Corner Market", "$84.12"))
        self.assertFalse(has_entry(text, "### 2026-10-14: Corner Market", "$84.13"))
        self.assertFalse(has_entry(text, "### 2026-10-15: Corner Market", "$84.12"))

    def test_a_clean_ledger_is_read_in_full(self):
        self.write("2026-10", DINER + "\n" + CAFE)
        report = Report(explain=True)
        self.save(diner(tip=None, total=67.81), report)
        self.assertEqual(report.status, "ok")
        self.assertIn("- pkm/wallet/finance/2026-10.md", report.render_explain().replace(self.pkm, "pkm"))
        self.assertIn("2 records", report.render_explain())

    def test_lines_the_duplicate_check_cant_read_are_partial_and_the_receipt_is_saved(self):
        self.write("2026-10", DINER + "Corner Market 84.12, paid cash\n\n### 2026-10-06: Sample Grill\n| Steak Frites | $38.00 | $38.00 |\n")
        report = Report()
        self.save(parse_receipt(date_text="2026-10-15", vendor="Sample Burger", items=[{"name": "Burger", "amount": 12}],
                                total=12, today=TODAY), report)
        self.assertEqual(report.status, "partial")
        self.assertEqual([issue.lines for issue in report.issues][0], ["Corner Market 84.12, paid cash"])
        self.assertTrue(report.issue_lines()[0].endswith("2026-10.md: 1 line not recognized"))
        self.assertTrue(report.issue_lines()[1].endswith(
            "2026-10.md: \"### 2026-10-06: Sample Grill\" has no **Total** row, so the duplicate check couldn't compare it"))
        self.assertIn("### 2026-10-15: Sample Burger", self.read("2026-10"))

    def test_explain_traces_the_sum_and_the_conversion(self):
        report = Report(explain=True)
        self.save(cafe(), report)
        self.assertEqual(report.traces, [
            "Items 95 TWD + taxes and fees 0 TWD + tip 0 TWD = 95 TWD, the printed total",
            "USD total: 95 TWD × 0.03127 USD/TWD (currency-api, 2026-07-09) = 2.97 USD",
        ])


class TestReadLedger(unittest.TestCase):

    def test_every_line_of_the_ledger_format_is_accounted_for(self):
        self.assertEqual(read_ledger(DINER + "\n" + CAFE), LedgerScan(2, [], []))

    def test_lines_outside_the_format_and_entries_without_a_total(self):
        text = "# October\n" + DINER + "Note: split with Sam\n### 2026-10-06: Sample Grill\n| Steak Frites | $38.00 | $38.00 |\n"
        self.assertEqual(read_ledger(text),
                         LedgerScan(2, ["# October", "Note: split with Sam"], ["### 2026-10-06: Sample Grill"]))

    def test_an_empty_file_has_nothing_to_read(self):
        self.assertEqual(read_ledger(""), LedgerScan(0, [], []))


class TestSummary(unittest.TestCase):

    def test_a_foreign_receipt_gives_both_totals_and_the_rate(self):
        saved = SavedEntry("/x/wallet/finance/2026-07.md", CAFE, Decimal("2.97"), TWD)
        self.assertEqual(summary(cafe(payment_method=""), saved), "\n".join([
            "Saved to pkm/wallet/finance/2026-07.md. Total: 95 TWD = 2.97 USD.",
            "Rate: 1 TWD = 0.03127 USD (currency-api, 2026-07-09).",
            "Pending review: payment method.",
        ]))

    def test_a_rate_from_an_earlier_day_says_so(self):
        saved = SavedEntry("/x/wallet/finance/2026-07.md", CAFE, Decimal("2.97"), TWD)
        self.assertEqual(summary(cafe(date_text="2026-07-11"), saved).splitlines()[1],
                         "Rate: 1 TWD = 0.03127 USD (currency-api, 2026-07-09), closest earlier rate; none for 2026-07-11.")
        undated = summary(cafe(date_text=""), saved)
        self.assertIn("Rate: 1 TWD = 0.03127 USD (currency-api, 2026-07-09).", undated)
        self.assertNotIn("closest earlier rate", undated)

    def test_a_usd_receipt_needs_no_rate(self):
        saved = SavedEntry("/x/wallet/finance/2026-10.md", DINER, Decimal("79.00"), USD)
        self.assertEqual(summary(diner(), saved), "Saved to pkm/wallet/finance/2026-10.md. Total: 79.00 USD.")

    def test_iso_amounts(self):
        self.assertEqual(iso_amount(Decimal("1234.5"), "USD"), "1,234.50 USD")
        self.assertEqual(iso_amount(Decimal("-6.4"), "EUR"), "-6.40 EUR")
        self.assertEqual(iso_amount(Decimal("1200"), "TWD", places=0), "1,200 TWD")
        self.assertEqual(iso_amount(Decimal("-0.001"), "USD"), "0.00 USD")


if __name__ == "__main__":
    unittest.main()
