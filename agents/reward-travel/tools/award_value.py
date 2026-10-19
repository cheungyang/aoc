"""award_value: the cents-per-point value of one award option."""
from typing import Union

from core.tooling import Report, ToolError, agent_tool, load_rules
from agents.reward_travel.lib.award import AwardValueError
from agents.reward_travel.lib.award import award_value as value_option
from agents.reward_travel.lib.money import usd
from agents.reward_travel.lib.rules import RULES


@agent_tool
def award_value(report: Report, points: int, taxes: str, cash: Union[float, str]) -> dict:
    """Value one award option in cents per point (CPP), hotel nights too. Value every option you present
    in one step, with the same seats or nights in all three numbers.

    Reads: today's exchange rate for non-USD amounts (ECB, else currency-api); RULES.md.
    Computes: CPP = (cash − taxes) ÷ points × 100 in USD; band poor below 1.3, fair, good above
    `cpp_alert_above` (2.0), exceptional above 3.0; alert above that line.
    Leaves out: whether it suits the trip (routing, comfort, transfer partners).

    Returns JSON: cpp, band, note, alert, points, taxes and cash in USD ("134.56 USD"), fx (rates used).

    Args:
        points: Points or miles the award costs.
        taxes: Taxes and fees as shown: "123.45 CAD" (seats_aero's total_taxes), "€123.45" or "$5.60" ($ is USD).
        cash: Cash price of the same flights and cabin: a USD number, or a string with its currency.
    """
    alert_above = load_rules("reward-travel", RULES, report)["cpp_alert_above"]
    try:
        value = value_option(points, taxes, cash, alert_above=alert_above, report=report)
    except AwardValueError as e:
        raise ToolError(str(e)) from None
    payload = {
        "cpp": float(value.cpp),
        "band": value.band,
        "note": value.note,
        "alert": value.alert,
        "points": value.points,
        "taxes": usd(value.taxes_usd),
        "cash": usd(value.cash_usd),
    }
    if value.rates:
        payload["fx"] = [rate.describe() for rate in value.rates]
    return payload
