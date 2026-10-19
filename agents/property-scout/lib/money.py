"""Money as property-scout's output writes it: the ISO currency code, then the amount.

Zillow's amounts are US dollars, so they read "USD 849,000"; a price per square
foot keeps its cents ("USD 597.89"). A missing amount stays None, so a reply
says "n/a" or "none listed" instead of a made-up zero.
"""
from typing import Any, Dict, Optional

CURRENCY = "USD"

# The money fields in listing rows and in the criteria, with the decimals each shows.
MONEY_FIELDS = {
    "price": 0,
    "hoa_fee": 0,
    "rent_zestimate": 0,
    "estimated_annual_gross_rent": 0,
    "price_per_sqft": 2,
    "area_median_price_per_sqft": 2,
    "max_price": 0,
    "max_hoa": 0,
}


def usd(amount: Any, decimals: int = 0) -> Optional[str]:
    """`amount` as "USD 849,000", with `decimals` places; None when it isn't a number."""
    if isinstance(amount, bool) or amount is None:
        return None
    try:
        number = float(amount)
    except (TypeError, ValueError):
        return None
    sign = "-" if number < 0 else ""
    return f"{sign}{CURRENCY} {abs(number):,.{decimals}f}"


def with_currency(values: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of `values` with each money field written as `usd` writes it."""
    return {key: usd(value, MONEY_FIELDS[key]) if key in MONEY_FIELDS else value for key, value in values.items()}
