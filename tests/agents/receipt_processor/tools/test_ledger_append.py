"""agents/receipt-processor/tools/ledger_append.py: the tool around the ledger."""
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import ANY, patch

from core.util.config import Config
from core.util.fx import FxError, Rate
from tests.helpers import envelope, execution_context
from agents.receipt_processor.tools import ledger_append as module
from agents.receipt_processor.tools.ledger_append import ledger_append

TWD = Rate("TWD", Decimal("0.03127"), "2026-07-09", "currency-api")
RECEIPT = {
    "date": "2026-07-09",
    "vendor": "範例咖啡館",
    "items": [{"name": "烏龍茶", "amount": 95}],
    "total": 95,
    "currency": "TWD",
    "category": "Dining/Cafe",
}


class TestLedgerAppendTool(unittest.TestCase):

    def setUp(self):
        self.pkm = tempfile.mkdtemp()
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.addCleanup(shutil.rmtree, self.pkm, ignore_errors=True)
        now = patch.object(module, "get_local_now", return_value=datetime(2026, 10, 16, 18, 0))
        now.start()
        self.addCleanup(now.stop)
        rate = patch("agents.receipt_processor.lib.ledger.usd_rate", return_value=TWD)
        self.usd_rate = rate.start()
        self.addCleanup(rate.stop)
        allow = patch("core.loaders.tools_loader.ToolsLoader.check_permission", return_value=True)
        self.check = allow.start()
        self.addCleanup(allow.stop)
        self.path = os.path.join(self.pkm, "wallet", "finance", "2026-07.md")

    def call(self, **overrides):
        with execution_context(agent_id="receipt-processor"):
            return envelope(ledger_append.invoke({**RECEIPT, **overrides}))

    def test_saves_and_returns_the_block(self):
        result = self.call()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["errors"], "None")
        self.assertIn("Saved to pkm/wallet/finance/2026-07.md. Total: 95 TWD = 2.97 USD.", result["payload"])
        self.assertIn("Rate: 1 TWD = 0.03127 USD (currency-api, 2026-07-09).", result["payload"])
        self.assertIn("Pending review: payment method.", result["payload"])
        self.assertIn("| 烏龍茶 | 95 TWD | $2.97 |", result["payload"])
        with open(self.path, encoding="utf-8") as f:
            self.assertIn("**Category:** Dining/Cafe | **Payment Method:** Unknown (Pending Review)", f.read())
        self.check.assert_any_call(ANY, "ledger_append", "append", self.path)
        self.usd_rate.assert_called_once_with("TWD", date(2026, 7, 9), date(2026, 10, 16))

    def test_a_weekend_receipt_says_its_rate_is_from_an_earlier_day(self):
        result = self.call(date="2026-07-11")
        self.assertEqual(result["status"], "ok")
        self.assertIn("Rate: 1 TWD = 0.03127 USD (currency-api, 2026-07-09), closest earlier rate; none for 2026-07-11.",
                      result["payload"])

    def test_a_total_that_doesnt_add_up_writes_nothing(self):
        result = self.call(total=105)
        self.assertEqual(result["status"], "failed")
        self.assertIn("the lines are 10 TWD short", result["errors"])
        self.assertIn("Nothing was saved", result["errors"])
        self.assertFalse(os.path.exists(self.path))

    def test_a_duplicate_writes_nothing_and_says_why(self):
        self.call()
        result = self.call()
        self.assertEqual(result["status"], "failed")
        self.assertIn("2026-07.md already has \"### 2026-07-09: 範例咖啡館\" with total 95 TWD. Nothing was saved.",
                      result["errors"])
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read().count("### 2026-07-09: 範例咖啡館"), 1)

    def test_lines_the_duplicate_check_cant_read_make_it_partial(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("Corner Market 84.12, paid cash\n")
        result = self.call()
        self.assertEqual(result["status"], "partial")
        self.assertIn("- pkm/wallet/finance/2026-07.md: 1 line not recognized\n    Corner Market 84.12, paid cash", result["issues"])
        self.assertIn("Saved to pkm/wallet/finance/2026-07.md.", result["payload"])

    def test_explain_lists_the_ledger_and_the_sums(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("### 2026-07-01: Cafe\n| **Total** | **$4.00** | **$4.00** |\n")
        result = self.call(explain=True)
        self.assertIn("- pkm/wallet/finance/2026-07.md (modified ", result["explain"])
        self.assertIn("1 record)", result["explain"])
        self.assertIn("- USD total: 95 TWD × 0.03127 USD/TWD (currency-api, 2026-07-09) = 2.97 USD", result["explain"])

    def test_no_rate_writes_nothing(self):
        self.usd_rate.side_effect = FxError("No USD rate for TWD on 2026-07-09 or the 3 days before: "
                                            "currency-api answered HTTP 503")
        result = self.call()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"], "Error: No USD rate for TWD on 2026-07-09 or the 3 days before: "
                                           "currency-api answered HTTP 503")
        self.assertFalse(os.path.exists(self.path))

    def test_denied_writes_nothing(self):
        # The tool is granted; appending to this month's file is not.
        self.check.side_effect = lambda ctx, tool, action=None, target=None, **kw: action is None
        result = self.call()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"], "Error: Agent 'receipt-processor' does not have permission to 'append' "
                                           "pkm/wallet/finance/2026-07.md.")
        self.assertFalse(os.path.exists(self.path))

    def test_needs_an_agent_run(self):
        self.assertIn("no active execution context", ledger_append.invoke(RECEIPT))

    def test_every_call_is_a_write(self):
        self.assertEqual(tuple(module.READ_ACTIONS), ())


if __name__ == "__main__":
    unittest.main()
