# receipt-processor: how its tools work

## ledger_append

Saves one receipt as a block at the end of its month's ledger, `pkm/wallet/finance/YYYY-MM.md`: the receipt's month, or this month when the receipt shows no date. Every check below runs before anything is written. When one fails, nothing is saved and the error says why (status `failed`).

The examples use this receipt:

```python
>>> from datetime import date
>>> from decimal import Decimal
>>> from core.util.fx import MAX_DAYS_BACK, Rate
>>> from agents.receipt_processor.lib.ledger import (
...     SavedEntry, check_totals, has_entry, parse_receipt, read_ledger, render_block, summary)
>>> today = date(2026, 10, 16)
>>> diner = parse_receipt(
...     date_text="2026-10-05", vendor="Sample Diner (Springfield)", today=today,
...     items=[{"name": "1 Club Sandwich", "amount": 16.50}, {"name": "1 Mushroom Risotto", "amount": 24.00}],
...     subtotal=40.50, taxes_and_fees=[{"name": "Tax", "amount": 3.80}], tip=7.70, total=52.00,
...     category="Restaurant", payment_method="Visa (****0000)")
```

### The lines must add up

When the receipt prints a subtotal, the items must add up to it. Items, plus taxes and fees, plus the tip must equal the printed total, to the cent. Discounts, coupons and returns are negative items. A mismatch saves nothing; the error says how far off the lines are, so the agent re-reads the receipt. A truly illegible line goes in as an item named "Unknown (Pending Review)" holding the gap.

Sample Diner adds up, and the sum is what explain mode shows:

```python
>>> check_totals(diner)
'Items 40.50 USD + taxes and fees 3.80 USD + tip 7.70 USD = 52.00 USD, the printed total'
```

A tea for 95 TWD on a receipt that prints 105 TWD is 10 TWD short:

```python
>>> check_totals(parse_receipt(date_text="2026-07-11", vendor="範例咖啡館", today=today, currency="TWD",
...                            items=[{"name": "烏龍茶", "amount": 95}], total=105))
Traceback (most recent call last):
...
agents.receipt_processor.lib.ledger.LedgerError: Items 95 TWD + taxes and fees 0 TWD + tip 0 TWD = 95 TWD,
but the printed total is 105 TWD (the lines are 10 TWD short). Nothing was saved. ...
```

### No entry twice

An entry the month already has, with the same date, vendor and printed total, is refused, and nothing is saved. The total is compared as the ledger writes it ("$52.00", "95 TWD"). A second identical purchase goes in with `allow_duplicate=true`.

```python
>>> ledger = render_block(diner, Rate("USD", Decimal(1), "2026-10-05", "par"))
>>> has_entry(ledger, "### 2026-10-05: Sample Diner (Springfield)", "$52.00")
True
>>> has_entry(ledger, "### 2026-10-05: Sample Diner (Springfield)", "$53.00")
False
```

### Every line of the month's file is accounted for

For the duplicate check, every line of the month's file must belong to an entry: its `### date: vendor` header, the Category line, a table row, or a blank line. Any other line (a note, an entry written by hand) comes back raw under issues, and so does an entry with no **Total** row: the duplicate check couldn't see them. The status is `partial`, and the receipt is still saved.

```python
>>> read_ledger(ledger + "Corner Market 84.12, paid cash\n### 2026-10-06: Sample Grill\n| Steak Frites | $38.00 | $38.00 |\n")
LedgerScan(entries=2, unrecognized=['Corner Market 84.12, paid cash'], no_total=['### 2026-10-06: Sample Grill'])
```

### Exchange rates

Every amount is converted to USD at the rate for the receipt's date:
- the ECB's reference rate, through Frankfurter, for the currencies the ECB publishes;
- fawazahmed0's currency-api for every other currency (TWD is one), and when Frankfurter doesn't answer.

A day with no rate (a weekend, a holiday, a day currency-api skipped) takes the closest earlier one, up to 3 days back (`MAX_DAYS_BACK`): the ECB's last business day, or currency-api's last file. An older ECB rate doesn't count, and currency-api is asked instead. A receipt dated today, or tomorrow (from a time zone ahead), takes the latest rate. When no source has a rate within those 3 days, nothing is saved and the error says what each source answered. USD needs no rate.

```python
>>> MAX_DAYS_BACK
3
```

A Saturday receipt (Jul 11) that got Thursday's currency-api rate (Jul 9). Each line converts on its own, rounded to the cent:

```python
>>> tea = parse_receipt(date_text="2026-07-11", vendor="範例咖啡館", today=today, currency="TWD",
...                     items=[{"name": "烏龍茶", "amount": 95}], total=95, category="Dining/Cafe")
>>> rate = Rate("TWD", Decimal("0.03127"), "2026-07-09", "currency-api")
>>> print(render_block(tea, rate))
### 2026-07-11: 範例咖啡館
**Category:** Dining/Cafe | **Payment Method:** Unknown (Pending Review)
<BLANKLINE>
| Item | Original Price (TWD) | USD Price |
| :--- | :--- | :--- |
| 烏龍茶 | 95 TWD | $2.97 |
| **Total** | **95 TWD** | **$2.97** |
```

### What the tool says

Above the saved block: the file, the total in the receipt's currency and in USD, the rate with its source and its own date, and the fields saved as "Unknown (Pending Review)", for the agent to ask about. A rate from a day before the receipt's says so: "closest earlier rate; none for <date>". Money in what the tool says carries ISO codes; the block keeps the ledger's cells, whose headers name the currencies. A whole-unit currency (TWD, JPY, KRW, ...) shows no decimals when every amount on the receipt is whole.

```python
>>> print(summary(tea, SavedEntry("/pkm/wallet/finance/2026-07.md", "", Decimal("2.97"), rate)))
Saved to pkm/wallet/finance/2026-07.md. Total: 95 TWD = 2.97 USD.
Rate: 1 TWD = 0.03127 USD (currency-api, 2026-07-09), closest earlier rate; none for 2026-07-11.
Pending review: payment method.
```

A rate from the receipt's own day needs no note:

```python
>>> same_day = Rate("TWD", Decimal("0.03127"), "2026-07-11", "currency-api")
>>> print(summary(tea, SavedEntry("/pkm/wallet/finance/2026-07.md", "", Decimal("2.97"), same_day)).splitlines()[1])
Rate: 1 TWD = 0.03127 USD (currency-api, 2026-07-11).
```

### Other refusals

- `date` must be YYYY-MM-DD, or empty when the receipt shows none. A date more than a day after today is refused.
- `vendor` is required, `currency` must be an ISO code, and `items` must list at least one line, each with a name and an amount. A tip can't be negative.
- The agent must be allowed to `append` to the month's file.
