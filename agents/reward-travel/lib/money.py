"""Money as reward-travel's tools show it: the amount, then its ISO currency code ("135 USD").

The card files are kept in US dollars and write them with a bare "$"; other
currency marks ("NT$", "€") are left as written.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
# A bare "$" before an amount: not "US$", "NT$" or "2x$90".
_DOLLARS = re.compile(r"(?<![A-Za-z0-9$])\$\s?(\d[\d,]*(?:\.\d+)?)")


def usd(amount: Decimal) -> str:
    """'2,400.00 USD': the amount to the cent, rounded half up."""
    return f"{amount.quantize(CENT, rounding=ROUND_HALF_UP):,.2f} USD"


def dollars_as_usd(text: str) -> str:
    """`text` with each bare "$" amount written as USD: "$15 / mo ($20 Dec)" -> "15 USD / mo (20 USD Dec)"."""
    return _DOLLARS.sub(lambda match: f"{match.group(1)} USD", text)
