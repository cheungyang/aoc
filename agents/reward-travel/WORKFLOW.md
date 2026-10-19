# reward-travel: how its tools work

Two tools and one scan. The thresholds are in `pkm/agents/reward-travel/RULES.md`; a key it doesn't set uses the default given here.

## Money

Money in tool output carries its ISO code after the amount: `134.56 USD`, `56.20 CAD`. The card files are kept in US dollars with a bare `$`, so the tools read it as USD; other marks (`NT$`, `€`) stay as written.

```python
>>> from decimal import Decimal
>>> from agents.reward_travel.lib.money import dollars_as_usd, usd
>>> usd(Decimal("2400"))
'2,400.00 USD'
>>> dollars_as_usd("$15 / mo ($20 Dec)")
'15 USD / mo (20 USD Dec)'
```

## award_value

**CPP.** Cents per point = (cash fare − taxes and fees) ÷ points × 100, all in USD, rounded half up to 2 decimals.

A 75,000-point award with 134.56 USD of taxes, against a 2,400 USD fare:

```python
>>> from agents.reward_travel.lib.award import award_value, cents_per_point, value_band
>>> cents_per_point(75000, taxes_usd=Decimal("134.56"), cash_usd=Decimal("2400"))
Decimal('3.02')
```

**Amounts.** Taxes and cash may be a number (USD), an amount with a symbol (`€123.45`, `$5.60`; a bare `$` is USD) or with an ISO code (`123.45 CAD`, the way `seats_aero` gives trip taxes). An amount whose currency can't be told (`N/A`, or a trip whose currency seats.aero didn't give) is an error, never taken as USD. Points must be a whole number above 0, taxes can't be negative, and cash must be above 0.

**Exchange rates** (core/util/fx.py). A foreign amount converts at today's rate: the ECB's reference rate through Frankfurter, or fawazahmed0's currency-api for currencies the ECB doesn't publish (like TWD) and when Frankfurter can't answer. A day with no published rate uses the closest earlier one, up to 3 days back, a safety limit in code. No rate from any source makes the call `failed`, never an estimate. The converted amount is rounded to the cent, half up; €123.45 at 1.09:

```python
>>> from core.util.fx import Rate, to_usd
>>> to_usd(Decimal("123.45"), Rate("EUR", Decimal("1.09"), "2026-10-16", "ECB"))
Decimal('134.56')
```

**Bands and the alert.** Poor below 1.3; fair from 1.3 up to the alert line; good above the alert line; exceptional above 3.0. The alert line is `cpp_alert_above` (default 2.0); `alert` is true only above it, not at it. The award-search schedule alerts only on `alert`.

```python
>>> [value_band(Decimal(cpp), alert_above=Decimal("2.0")) for cpp in ("1.29", "1.30", "2.00", "2.01", "3.00", "3.01")]
['poor', 'fair', 'fair', 'good', 'good', 'exceptional']
```

70,000 points and 5.60 USD of taxes for a 1,000 USD fare is fair; with `cpp_alert_above: 2.5`, a 2.40 option is fair too, and doesn't alert:

```python
>>> option = award_value(70000, "$5.60", 1000, alert_above=2.0)
>>> option.cpp, option.band, option.note, option.alert
(Decimal('1.42'), 'fair', '1.3 to 2.0', False)
>>> option = award_value(100000, "0 USD", 2400, alert_above=2.5)
>>> option.cpp, option.band, option.note, option.alert
(Decimal('2.40'), 'fair', '1.3 to 2.5', False)
```

## credit_windows

**What it reads.** Every `.md` file in `pkm/wallet/credit-card/`:
- each table with a Value column: each row is a credit, the first column names it and its card, and the last column is its log;
- each file's `**Last Updated:**` line;
- each `5/24 Status` line: the highest N/24 on it, for the player under whose heading it sits.

Text outside those tables (focus lists, watchlists, balances) is left to the agent. No folder, or no credit table in it, makes the call `failed`.

**Cycle.** From a Cycle column, else the value (`$10 / mo`), else the value column's header or the section heading (`Monthly Credits`). A log entry spanning two years (`2025–2026 Cycle`), or one naming a renewal or anniversary, makes it a cardmember year: its dates aren't known, so it gets a status and never a flag. A row with no cycle is an issue.

**Periods.** Calendar months, quarters, halves and years. An entry's month range in parentheses wins over its quarter number:

```python
>>> from datetime import date
>>> from agents.reward_travel.lib.periods import current_period, parse_period
>>> current_period("quarterly", date(2026, 10, 16)).label
'Q4 2026 (Oct–Dec)'
>>> period = parse_period("2026 Q2 (Jul–Sep): Pending")
>>> period.start, period.end
(datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))
```

**Log entries.** Separated by `<br>` or `•`, each starting with its period. Its words say what happened: missed, expired, forfeited or lapsed is missed; pending, active, upcoming, unused or available is open (not used yet); redeemed, used, done or applied is used; `$135 Remaining` (or `135 USD remaining`) is partly used, or used at 0. `Auto (Example TV)` marks a credit used automatically.

```python
>>> from agents.reward_travel.lib.card_files import parse_entry
>>> [parse_entry(text).status for text in ("Oct 2026: Redeemed (2026-10-12)", "2026 H2: Active / Pending Use", "May 2026: MISSED", "Auto (Example TV)")]
['used', 'open', 'missed', 'auto']
>>> entry = parse_entry("2026: $45 used. **$135 Remaining**")
>>> entry.status, entry.remaining
('open', '135 USD')
```

An entry with no period and no word of use (`Monthly Transit ($10)`) is a note: it comes back with its credit, under `notes`. An entry whose use or period can't be told (`Sep 2026: grabbed coffee`, or `Redeemed (2026-10-12)` without its month) comes back raw as an unrecognized line, and the call is `partial`; so does a row of a Value table that isn't a credit.

**Status this period.** From the last entry for the current period: used, missed, unused, or partly used (with what's left, `remaining`). Without one, an `Auto` entry makes it `auto` (with what uses it, `by`); with neither, it's `not logged`, which counts as unused.

**Flags.** Only an unused credit in a known period gets one:
- `lapsing`: its deadline is `lapse_days` or fewer days away, by cycle (defaults: monthly 7, quarterly 14, semiannual 21, annual 30);
- else `behind`: more than `behind_after_pct` (default 50) of the period's days have gone, today included.

The coffee credit (monthly, not logged in October): on Oct 15, 15 of 31 days are gone, not more than half; on Oct 16 it's behind; on Oct 24, with 7 days left, it's lapsing:

```python
>>> from agents.reward_travel.lib.credits import stage_on
>>> october = current_period("monthly", date(2026, 10, 16))
>>> [stage_on(october, date(2026, 10, day), behind_after_pct=50, lapse_days=7) for day in (15, 16, 24)]
[None, 'behind', 'lapsing']
```

A tracker with the coffee credit and a streaming credit, which Example TV uses every month, on Oct 24:

```python
>>> from agents.reward_travel.lib.card_files import parse_card_file
>>> from agents.reward_travel.lib.credits import Thresholds, window_for
>>> limits = Thresholds(behind_after_pct=50, lapse_days={"monthly": 7, "quarterly": 14, "semiannual": 21, "annual": 30}, stale_after_months=3)
>>> tracker = '''| Credit & Card | Monthly Value | Win Rate | Log |
... | --- | --- | --- | --- |
... | **Sam's Coffee Credit** *(Example Dining)* | $5 / mo | 50% | • Sep 2026: Redeemed (2026-09-13) |
... | **Streaming Credit** *(Example Premium)* | $9 / mo | Auto | • Auto (Example TV) |'''
>>> for row in parse_card_file("statement_credits.md", tracker).credits:
...     window = window_for(row, date(2026, 10, 24), limits)
...     print(f"{window.name} | {window.value} | {window.status} | {window.days_left} days left | {window.flag}")
Sam's Coffee Credit (Example Dining) | 5 USD / mo | not logged | 7 days left | lapsing
Streaming Credit (Example Premium) | 9 USD / mo | auto | 7 days left | None
```

The same tracker with the coffee credit's September entry missing its month:

```python
>>> from core.tooling import Report
>>> report = Report()
>>> cards = parse_card_file("statement_credits.md", tracker.replace("Sep 2026: Redeemed", "Redeemed"), report)
>>> print(report.render_issues())
- statement_credits.md: 1 line not recognized
    Sam's Coffee Credit (Example Dining): Redeemed (2026-09-13)
```

**Order.** Lapsing, then behind, then other unused credits, then the rest; within each, fewest days left first, then by name.

**File age.** Calendar months from the `**Last Updated:**` month to today; stale when more than `stale_after_months` (default 3). A date it can't read (`sometime in fall`) is an issue; a file without the line has no age.

```python
>>> from agents.reward_travel.lib.card_files import FileInfo
>>> from agents.reward_travel.lib.credits import file_age
>>> file_age(FileInfo("statement_credits.md", "August 2026", date(2026, 8, 1)), date(2026, 12, 2), stale_after_months=3)
FileAge(name='statement_credits.md', last_updated='August 2026', months_old=4, stale=True)
```

**RULES.md values.** A value out of range (`behind_after_pct` outside 0 to 100, a negative number of days or months), or a `lapse_days` entry for no cycle, is an issue, and the default applies. Cycle names read in any case, with or without a hyphen (`Semi-Annual`).

## The credit-windows scan

It runs before the scheduled credit check, with the same rules, and wakes the agent only for credits whose flag is new since the last successful run. So each credit comes up at most twice a period: when it falls behind, then when it's about to lapse. Auto credits never come up. Unrecognized lines ride along with the findings; on a quiet day they're posted in the channel, once a day.

The coffee credit was behind on Oct 16: new against a run on Oct 14, old news against one on Oct 16:

```python
>>> from agents.reward_travel.lib.credits import CreditSummary, newly_flagged
>>> coffee = window_for(parse_card_file("statement_credits.md", tracker).credits[0], date(2026, 10, 16), limits)
>>> summary = CreditSummary(date(2026, 10, 16), [coffee], [], [])
>>> [window.name for window in newly_flagged(summary, since=date(2026, 10, 14), limits=limits)]
["Sam's Coffee Credit (Example Dining)"]
>>> newly_flagged(summary, since=date(2026, 10, 16), limits=limits)
[]
```
