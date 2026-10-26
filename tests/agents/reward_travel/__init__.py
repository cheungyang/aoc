"""Tests for agents/reward-travel, with made-up card files in the shape of the real ones."""
import os
import tempfile
import unittest

STATEMENT_CREDITS = """# Statement Credits Tracker

**Last Updated:** August 30, 2026

## 🎯 This Month's Focus

* **Example Premium – $180 Airline Credit:**
  * Status: **$135 Remaining** ($45 used on a seat upgrade in May 2026).

---

### 1. Monthly Credits

| Credit & Card | Monthly Value | Win Rate (2026) | Log (2026) |
| :--- | :--- | :--- | :--- |
| **Combined Rideshare Credit** *(Example Premium + Dining)* | $15 / mo ($20 Dec) | **100% (8/8)** 🏆 | • Jul 2026: Redeemed (2026-07-19)<br>• Aug 2026: **Redeemed (2026-08-11)** 🏆 |
| **Sam's Coffee Credit** *(Example Dining)* | $5 / mo | **50% (3/6)** ⚠️ | • May 2026: **MISSED** ❌ (2026-06-04)<br>• Jun 2026: Redeemed (2026-06-15) |
| **Example Air Monthly Credits** | $10 transit, $12 grocery delivery | *Tracking Started* | • Monthly Transit ($10)<br>• Monthly Grocery Delivery ($7 + $5) |

---

### 2. Quarterly & Semi-Annual Credits

| Credit & Card | Value | Cycle | History |
| :--- | :--- | :--- | :--- |
| **Dining Credit** *(Example Premium)* | $50 / quarter | Quarterly | • 2026 Q1: Redeemed (2026-03-21)<br>• 2026 Q2 (Jul–Sep): **Pending / In Progress** |
| **Fitness Credit** *(Example Premium)* | $25 / quarter | Quarterly | • 2026 Q2: Redeemed (2026-06-09)<br>• 2026 Q3 (Oct–Dec): Upcoming |
| **Dining Credit** *(Example Dining)* | $40 / half-year | Semi-Annual | • 2026 H1: **MISSED** ❌ (2026-07-02)<br>• 2026 H2: **Active / Pending Use** |
| **Hotel Credit (Luxury/Boutique)** | $150 / $100 | Semi-Annual / Annual | • 2026 H1: Redeemed (2026-04-18)<br>• 2026 H2: Redeemed (2026-08-28) 🏆 |

---

### 3. Annual Credits

| Credit | Annual Value | Status & Log |
| :--- | :--- | :--- |
| **Example Travel Card Credit** | $250 / yr | • 2025–2026 Cycle: Redeemed (2026-01-20)<br>• 2026–2027 Cycle: Redeemed (2026-07-08)<br>• Next credit available after Jul 2027 renewal. |
| **Example Premium Airline Credit** | $180 / yr | • 2025: Redeemed via airline gift card 2x$90 (2025-11-30)<br>• 2026: $45 used (seat upgrade). **$135 Remaining**. |
| **Store Reward Certificate** | Variable | • 2026 Certificate Redeemed (2026-03-14). |
"""

PORTFOLIO = """# Household Card Portfolio

**Last Updated:** September 2026

## Players

### Player 1 (You)
* **Active Cards:** Example Travel Card, Example Air Card, Example Hotel Card.
* **5/24 Status:** ~3/24 (Example Air & Example Hotel in 2026).

### Player 2 (Pat — Managed by You)
* **Active Cards:** Example Premium Card, Example Dining Card.
* **5/24 Status:** ~0/24 to 1/24.

## Point Balances
* **Example Bank Points:** ~48,250 pts
"""


class CardFilesCase(unittest.TestCase):
    """A temp PKM with the card files in wallet/credit-card/, set as Config().pkm_dir."""

    def setUp(self):
        from core.config.settings import Config

        self._tmp = tempfile.TemporaryDirectory()
        self.pkm = self._tmp.name
        self.folder = os.path.join(self.pkm, "wallet", "credit-card")
        os.makedirs(self.folder)
        self.write("statement_credits.md", STATEMENT_CREDITS)
        self.write("portfolio_summary.md", PORTFOLIO)
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name, text):
        with open(os.path.join(self.folder, name), "w", encoding="utf-8") as f:
            f.write(text)
