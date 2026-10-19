"""What an award redemption is worth: cents per point (CPP) and a value band.

CPP = (cash fare − award taxes and fees) ÷ points × 100, everything in USD;
foreign amounts are converted at today's rate (core/util/fx.py). The bands:

    poor         below 1.3
    fair         1.3 up to the alert line
    good         above the alert line: worth an award-search alert
    exceptional  above 3.0

The alert line is the user's `cpp_alert_above` in RULES.md (2.0 by default).
What a band means for a redemption is the flight_search skill's call.
"""
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Tuple, Union

from core.tooling import DataProblem, Report
from core.util.fx import Amount, FxError, MoneyError, Rate, cents, parse_money, to_usd, usd_rate
from agents.reward_travel.lib.money import usd

POOR_BELOW = Decimal("1.3")
EXCEPTIONAL_ABOVE = Decimal("3.0")
RATE_SOURCES = "exchange rates (ECB, currency-api)"


class AwardValueError(ValueError):
    """Inputs that can't be valued."""


@dataclass(frozen=True)
class AwardValue:
    points: int
    taxes_usd: Decimal
    cash_usd: Decimal
    cpp: Decimal  # to 2 decimals
    band: str
    note: str  # the band's range in words
    alert: bool  # above the alert line
    rates: Tuple[Rate, ...]  # the exchange rates used, if any


def value_band(cpp: Decimal, alert_above: Decimal) -> str:
    if cpp > EXCEPTIONAL_ABOVE:
        return "exceptional"
    if cpp > alert_above:
        return "good"
    if cpp >= POOR_BELOW:
        return "fair"
    return "poor"


def band_note(band: str, alert_above: Decimal) -> str:
    """The band's range in words: "above 2.0: worth an alert"."""
    return {
        "poor": f"below {POOR_BELOW}",
        "fair": f"{POOR_BELOW} to {alert_above}",
        "good": f"above {alert_above}: worth an alert",
        "exceptional": f"above {EXCEPTIONAL_ABOVE}",
    }[band]


def cents_per_point(points: int, taxes_usd: Decimal, cash_usd: Decimal) -> Decimal:
    if points <= 0:
        raise AwardValueError("'points' must be a positive number.")
    return ((cash_usd - taxes_usd) / Decimal(points) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def award_value(points: int, taxes: Amount, cash: Amount, alert_above: Union[float, Decimal],
                today: Optional[date] = None, report: Optional[Report] = None) -> AwardValue:
    """CPP, band and alert for one option.

    Raises AwardValueError for inputs that can't be valued, and DataProblem
    (with `report`) when no source has a rate a foreign amount needs.
    """
    report = report if report is not None else Report()
    if isinstance(points, bool) or not isinstance(points, int):
        raise AwardValueError(f"'points' must be a whole number, got {points!r}.")
    line = Decimal(str(alert_above))
    taxes_usd, tax_rate = _usd(taxes, "taxes", today, report)
    cash_usd, cash_rate = _usd(cash, "cash", today, report)
    if taxes_usd < 0:
        raise AwardValueError("'taxes' can't be negative.")
    if cash_usd <= 0:
        raise AwardValueError("'cash' must be the cash fare, above zero.")
    cpp = cents_per_point(points, taxes_usd, cash_usd)
    band, alert = value_band(cpp, line), cpp > line
    report.trace(f"CPP {cpp} = ({usd(cash_usd)} cash − {usd(taxes_usd)} taxes) ÷ {points:,} points × 100")
    report.trace(f"band {band} ({band_note(band, line)}); alert: {cpp} {'>' if alert else '<='} "
                 f"cpp_alert_above {line}")
    rates = tuple(rate for rate in (tax_rate, cash_rate) if rate is not None)
    return AwardValue(points, taxes_usd, cash_usd, cpp, band, band_note(band, line), alert, rates)


def _usd(value: Amount, field: str, today: Optional[date], report: Report) -> Tuple[Decimal, Optional[Rate]]:
    try:
        amount, currency = parse_money(value, default_currency="USD")
    except MoneyError as e:
        hint = " Take the taxes from the trip's total_taxes (a `trips` call)." if field == "taxes" else ""
        raise AwardValueError(f"'{field}': {e}.{hint}") from None
    if currency == "USD":
        return cents(amount), None
    try:
        rate = usd_rate(currency, None, today)
    except FxError as e:
        report.missing(RATE_SOURCES, str(e))
        raise DataProblem(report) from None
    converted = to_usd(amount, rate)
    report.read(f"exchange rate {rate.describe()}")
    report.trace(f"{field} {amount} {currency} × {rate.usd} = {usd(converted)}")
    return converted, rate
