"""The monthly finance ledger: one Markdown block per receipt in pkm/wallet/finance/YYYY-MM.md.

Blocks keep the format receipt-processor has always written:

    ### 2026-10-05: Sample Diner (Springfield)
    **Category:** Restaurant | **Payment Method:** Visa (****0000)

    | Item | Original Price (USD) | USD Price |
    | :--- | :--- | :--- |
    | 1 Club Sandwich | $16.50 | $16.50 |
    | 1 Mushroom Risotto | $24.00 | $24.00 |
    | Subtotal | $40.50 | $40.50 |
    | Tax | $3.80 | $3.80 |
    | Tip | $7.70 | $7.70 |
    | **Total** | **$52.00** | **$52.00** |

`save_receipt` checks the arithmetic before anything is written (items + taxes and
fees + tip must equal the printed total, to the cent), refuses an entry the month
already has, converts at the receipt date's rate, and appends. What the duplicate
check couldn't read in the month's file goes in the tool's report (`read_ledger`).

The blocks keep the ledger's own cells ("$24.00", "95 TWD"). What the tool says
around them labels every amount with its ISO code ("24.00 USD", `iso_amount`).
"""
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import cached_property
from typing import Any, Iterable, List, Optional, Tuple

from core.tooling import Report
from core.util.fx import FxError, MoneyError, Rate, cents, format_usd, parse_money, to_decimal, to_usd, usd_rate

PENDING = "Unknown (Pending Review)"
ZERO = Decimal(0)
FIX_HINT = (
    "Nothing was saved. Re-read the receipt for a missed or misread line and call again; "
    f"if a line is truly illegible, add the gap as an item named \"{PENDING}\"."
)
# Currencies priced in whole units in practice: the ledger shows "95 TWD", not "95.00 TWD".
ZERO_DECIMAL = frozenset({"JPY", "KRW", "TWD", "VND", "CLP", "ISK", "HUF", "IDR", "COP", "PYG", "UGX"})
_TOTAL_ROW = re.compile(r"^\|\s*\*\*total\*\*\s*\|\s*\*\*(.+?)\*\*\s*\|", re.IGNORECASE)


class LedgerError(ValueError):
    """Fields that can't be saved: invalid, not adding up, already in the ledger, or with no exchange rate."""


@dataclass(frozen=True)
class Line:
    name: str
    amount: Decimal


@dataclass(frozen=True)
class Receipt:
    vendor: str
    currency: str
    items: Tuple[Line, ...]
    total: Decimal
    date: Optional[date] = None
    category: str = PENDING
    payment_method: str = PENDING
    taxes_and_fees: Tuple[Line, ...] = ()
    tip: Optional[Decimal] = None
    subtotal: Optional[Decimal] = None

    @property
    def items_sum(self) -> Decimal:
        return sum((line.amount for line in self.items), ZERO)

    @property
    def header(self) -> str:
        return f"### {self.date.isoformat() if self.date else PENDING}: {self.vendor}"

    @property
    def pending(self) -> List[str]:
        """The fields saved as "Unknown (Pending Review)"."""
        missing = [("date", self.date is None), ("category", self.category == PENDING),
                   ("payment method", self.payment_method == PENDING)]
        return [name for name, is_missing in missing if is_missing]

    @cached_property
    def places(self) -> int:
        """0 for a whole-unit currency whose amounts here are all whole, else 2."""
        amounts = [line.amount for line in self.items + self.taxes_and_fees] + [self.total]
        amounts += [a for a in (self.tip, self.subtotal) if a is not None]
        whole = all(a == a.to_integral_value() for a in amounts)
        return 0 if self.currency in ZERO_DECIMAL and whole else 2

    def show(self, amount: Decimal) -> str:
        """`amount` as the receipt's original-price cell: "$12.50", "95 TWD", "-12.50 EUR"."""
        if self.currency == "USD":
            return format_usd(amount)
        return self.iso(amount)

    def iso(self, amount: Decimal) -> str:
        """`amount` with the receipt's ISO code, the way the tool's messages show money: "12.50 USD", "95 TWD"."""
        return iso_amount(amount, self.currency, self.places)


@dataclass(frozen=True)
class SavedEntry:
    path: str
    block: str
    usd_total: Decimal
    rate: Rate


@dataclass(frozen=True)
class LedgerScan:
    """What the duplicate check can read in a month's ledger (`read_ledger`)."""
    entries: int
    unrecognized: List[str]  # lines that belong to no entry
    no_total: List[str]  # headers of entries with no **Total** row


def parse_receipt(
    *,
    date_text: str,
    vendor: str,
    items: Iterable[Any],
    total: Any,
    today: date,
    currency: str = "USD",
    category: str = "",
    payment_method: str = "",
    taxes_and_fees: Optional[Iterable[Any]] = None,
    tip: Any = None,
    subtotal: Any = None,
) -> Receipt:
    """A Receipt from tool arguments, or LedgerError naming the field that's wrong."""
    name = _text(vendor)
    if not name:
        raise LedgerError(f"'vendor' is required; use \"{PENDING}\" if it's illegible.")
    code = (currency or "USD").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise LedgerError(f"'currency' must be an ISO code like USD, TWD or EUR, got {currency!r}.")
    lines = _lines(items, "items")
    if not lines:
        raise LedgerError("'items' must list at least one line.")
    tip_amount = None if tip is None else _amount(tip, "tip")
    if tip_amount is not None and tip_amount < 0:
        raise LedgerError("'tip' can't be negative.")
    return Receipt(
        vendor=name,
        currency=code,
        items=lines,
        total=_amount(total, "total"),
        date=_date(date_text, today),
        category=_text(category) or PENDING,
        payment_method=_text(payment_method) or PENDING,
        taxes_and_fees=_lines(taxes_and_fees or (), "taxes_and_fees"),
        tip=tip_amount or None,
        subtotal=None if subtotal is None else _amount(subtotal, "subtotal"),
    )


def check_totals(receipt: Receipt) -> str:
    """The sum behind the printed total, in words.

    Raises LedgerError when the lines don't add up to the printed subtotal or total.
    """
    money = receipt.iso
    items = receipt.items_sum
    if receipt.subtotal is not None and cents(items) != cents(receipt.subtotal):
        raise LedgerError(
            f"The items add up to {money(items)}, but the printed subtotal is "
            f"{money(receipt.subtotal)} ({_gap(receipt.subtotal - items, money)}). {FIX_HINT}"
        )
    fees = sum((line.amount for line in receipt.taxes_and_fees), ZERO)
    tip = receipt.tip or ZERO
    computed = items + fees + tip
    sum_text = f"Items {money(items)} + taxes and fees {money(fees)} + tip {money(tip)} = {money(computed)}"
    if cents(computed) != cents(receipt.total):
        raise LedgerError(
            f"{sum_text}, but the printed total is {money(receipt.total)} "
            f"({_gap(receipt.total - computed, money)}). {FIX_HINT}"
        )
    return f"{sum_text}, the printed total"


def render_block(receipt: Receipt, rate: Rate) -> str:
    """The ledger block, ending with a newline."""
    rows = [(line.name, line.amount) for line in receipt.items]
    if (receipt.taxes_and_fees or receipt.tip) and len(receipt.items) > 1:
        rows.append(("Subtotal", receipt.items_sum))
    rows += [(line.name, line.amount) for line in receipt.taxes_and_fees]
    if receipt.tip:
        rows.append(("Tip", receipt.tip))

    def usd(amount: Decimal) -> str:
        return format_usd(to_usd(amount, rate))

    lines = [
        receipt.header,
        f"**Category:** {receipt.category} | **Payment Method:** {receipt.payment_method}",
        "",
        f"| Item | Original Price ({receipt.currency}) | USD Price |",
        "| :--- | :--- | :--- |",
        *(f"| {_cell(name)} | {receipt.show(amount)} | {usd(amount)} |" for name, amount in rows),
        f"| **Total** | **{receipt.show(receipt.total)}** | **{usd(receipt.total)}** |",
    ]
    return "\n".join(lines) + "\n"


def entry_path(receipt: Receipt, pkm_dir: str, today: date) -> str:
    """The month's ledger file: the receipt's month, or this month for an undated receipt."""
    month = (receipt.date or today).strftime("%Y-%m")
    return os.path.join(pkm_dir, "wallet", "finance", f"{month}.md")


def has_entry(text: str, header: str, total_cell: str) -> bool:
    """Whether `text` has a block under `header` whose **Total** reads `total_cell`."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() != header:
            continue
        for following in lines[i + 1:]:
            if following.startswith("### "):
                break
            match = _TOTAL_ROW.match(following.strip())
            if match:
                if match.group(1).strip() == total_cell:
                    return True
                break
    return False


def read_ledger(text: str) -> LedgerScan:
    """Accounts for every line of a month's ledger, for the duplicate check.

    An entry is a `### date: vendor` header, then its Category line, table rows
    and blank lines, up to the next header. Any other line, before the first
    header or inside an entry, is unrecognized: an entry written there by hand
    isn't seen by the duplicate check. Nor is an entry with no **Total** row.
    """
    entries, unrecognized, no_total = 0, [], []
    header, has_total = None, False
    for line in text.splitlines():
        if line.startswith("### "):
            if header is not None and not has_total:
                no_total.append(header)
            header, has_total = line.strip(), False
            entries += 1
        elif not line.strip():
            continue
        elif header is not None and (line.startswith("**Category:**") or line.lstrip().startswith("|")):
            has_total = has_total or bool(_TOTAL_ROW.match(line.strip()))
        else:
            unrecognized.append(line)
    if header is not None and not has_total:
        no_total.append(header)
    return LedgerScan(entries, unrecognized, no_total)


def save_receipt(receipt: Receipt, *, pkm_dir: str, today: date, report: Report,
                 allow_duplicate: bool = False) -> SavedEntry:
    """Checks, converts and appends `receipt`. Raises LedgerError, writing nothing.

    What the duplicate check couldn't read in the month's file goes in `report`
    (`read_ledger`); the receipt is still saved. The sums go in as traces.
    """
    report.trace(check_totals(receipt))
    path = entry_path(receipt, pkm_dir, today)
    existing = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
        scan = read_ledger(existing)
        report.read(path, records=scan.entries, skipped=len(scan.unrecognized))
        report.unparsed(path, scan.unrecognized)
        for header in scan.no_total:
            report.problem(path, f"\"{header}\" has no **Total** row, so the duplicate check couldn't compare it")
    total_cell = receipt.show(receipt.total)
    if not allow_duplicate and has_entry(existing, receipt.header, total_cell):
        raise LedgerError(
            f"{os.path.basename(path)} already has \"{receipt.header}\" with total {receipt.iso(receipt.total)}. "
            "Nothing was saved. If it's a separate purchase, call again with allow_duplicate=true."
        )
    try:
        rate = usd_rate(receipt.currency, receipt.date, today)
    except FxError as e:
        raise LedgerError(str(e)) from e
    block = render_block(receipt, rate)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # One blank line between blocks, whatever the file ended with.
    gap = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(gap + block)
    usd_total = to_usd(receipt.total, rate)
    if receipt.currency != "USD":
        report.trace(f"USD total: {receipt.iso(receipt.total)} × {rate.usd:.6g} USD/{receipt.currency} "
                     f"({rate.source}, {rate.date}) = {iso_amount(usd_total, 'USD')}")
    return SavedEntry(path=path, block=block, usd_total=usd_total, rate=rate)


def summary(receipt: Receipt, saved: SavedEntry) -> str:
    """What ledger_append says above the saved block: the file, the total, the rate, the fields pending review.

    A rate from a day before the receipt's says so: "closest earlier rate; none for <date>".
    """
    usd = iso_amount(saved.usd_total, "USD")
    total = usd if receipt.currency == "USD" else f"{receipt.iso(receipt.total)} = {usd}"
    lines = [f"Saved to pkm/wallet/finance/{os.path.basename(saved.path)}. Total: {total}."]
    if receipt.currency != "USD":
        rate = saved.rate.describe()
        if receipt.date and saved.rate.date < receipt.date.isoformat():
            rate += f", closest earlier rate; none for {receipt.date.isoformat()}"
        lines.append(f"Rate: {rate}.")
    if receipt.pending:
        lines.append(f"Pending review: {', '.join(receipt.pending)}.")
    return "\n".join(lines)


def iso_amount(amount: Decimal, currency: str, places: int = 2) -> str:
    """`amount` with its ISO code: "2.97 USD", "1,200 TWD" (places=0), "-6.40 EUR"."""
    rounded = amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f"{abs(rounded) if rounded == 0 else rounded:,.{places}f} {currency}"


def _gap(difference: Decimal, show) -> str:
    return f"the lines are {show(abs(difference))} {'short' if difference > 0 else 'over'}"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _cell(text: str) -> str:
    """A table cell: an unescaped pipe would start a new column."""
    return text.replace("|", "\\|")


def _date(text: Any, today: date) -> Optional[date]:
    raw = _text(text)
    if not raw or raw == PENDING:
        return None
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        raise LedgerError(f"'date' must be YYYY-MM-DD (or empty when the receipt shows none), got {text!r}.") from None
    if day > today + timedelta(days=1):
        raise LedgerError(f"'date' {raw} is in the future.")
    return day


def _amount(value: Any, field: str) -> Decimal:
    try:
        return parse_money(value)[0] if isinstance(value, str) else to_decimal(value)
    except MoneyError as e:
        raise LedgerError(f"'{field}': {e}.") from None


def _lines(raw: Iterable[Any], field: str) -> Tuple[Line, ...]:
    lines = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise LedgerError(f"'{field}[{i}]' must be {{\"name\": ..., \"amount\": ...}}.")
        name = _text(entry.get("name"))
        if not name:
            raise LedgerError(f"'{field}[{i}]' needs a 'name'.")
        if entry.get("amount") is None:
            raise LedgerError(f"'{field}[{i}]' ({name}) needs an 'amount'.")
        lines.append(Line(name, _amount(entry["amount"], f"{field}[{i}].amount")))
    return tuple(lines)
