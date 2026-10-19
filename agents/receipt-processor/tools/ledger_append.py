"""ledger_append: save a receipt to the monthly finance ledger."""
from typing import List, Optional

from typing_extensions import TypedDict

from core.tooling import Report, ToolError, agent_tool, get_local_now, pkm_path, require_permission
from agents.receipt_processor.lib.ledger import LedgerError, entry_path, parse_receipt, save_receipt, summary

WRITE_ACTION = "append"
# Every call writes, so a read-only run (the voice replay) refuses it.
READ_ACTIONS = ()


class ReceiptLine(TypedDict):
    """One printed line of the receipt."""
    name: str
    amount: float


@agent_tool
def ledger_append(
    report: Report,
    date: str,
    vendor: str,
    items: List[ReceiptLine],
    total: float,
    currency: str = "USD",
    category: str = "",
    payment_method: str = "",
    taxes_and_fees: Optional[List[ReceiptLine]] = None,
    tip: Optional[float] = None,
    subtotal: Optional[float] = None,
    allow_duplicate: bool = False,
) -> str:
    """Save a receipt to the monthly ledger, pkm/wallet/finance/YYYY-MM.md, in the ledger's format.

    Reads: the month's ledger, for an entry it already has; the exchange rate on the receipt's date.
    Computes: every amount in USD at that rate; that items + taxes and fees + tip equal the printed total.
    Leaves out: reading the receipt and choosing its category; both are yours.

    Converts every amount to USD at the receipt date's exchange rate, checks that items +
    taxes and fees + tip equal the printed total, and appends the block. Nothing is saved
    when the lines don't add up: the error says by how much, so re-read the receipt and call
    again (if a line is truly illegible, add the gap as an item named "Unknown (Pending Review)").
    Also refuses an entry the month already has (same date, vendor and total), and a currency
    whose rate can't be fetched.

    Returns the file, the total (in the receipt's currency and in USD), the exchange rate used,
    any fields pending review, and the saved block to show the user.

    Args:
        date: Receipt date, YYYY-MM-DD; empty if the receipt shows none.
        vendor: Merchant name as printed, in its own script.
        items: Line items in receipt order, each {"name", "amount"}. Discounts, coupons and
            returns are negative amounts.
        total: The printed grand total.
        currency: ISO code of the receipt's currency, e.g. "USD", "TWD", "EUR".
        category: e.g. Restaurant, Grocery, Personal Care, Travel, Entertainment; empty if unclear.
        payment_method: e.g. "Visa (****0000)"; empty if the receipt doesn't show it.
        taxes_and_fees: Lines after the subtotal, in receipt order: taxes, service charges,
            surcharges, fees. Each {"name", "amount"}.
        tip: The tip, if any.
        subtotal: The printed subtotal, if any; the items are checked against it.
        allow_duplicate: Save even though the same entry is already in the ledger.
    """
    today = get_local_now().date()
    pkm_dir = pkm_path()
    try:
        receipt = parse_receipt(
            date_text=date, vendor=vendor, items=items, total=total, today=today, currency=currency,
            category=category, payment_method=payment_method, taxes_and_fees=taxes_and_fees,
            tip=tip, subtotal=subtotal,
        )
        path = entry_path(receipt, pkm_dir, today)
        require_permission(WRITE_ACTION, path)
        saved = save_receipt(receipt, pkm_dir=pkm_dir, today=today, report=report, allow_duplicate=allow_duplicate)
    except LedgerError as e:
        raise ToolError(str(e)) from e
    return summary(receipt, saved) + "\n\n" + saved.block
