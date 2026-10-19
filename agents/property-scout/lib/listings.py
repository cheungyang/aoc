"""The hard filters: which Zillow listings pass the criteria, with the listing tool's numbers.

Listings arrive as zillow_query returns them, already enriched with
price_per_sqft and the rent, yield and cap-rate estimates; nothing here
recomputes those. What this adds:

- the filters each listing fails (`missed`), from `Criteria`;
- an area baseline: the median $/sqft of the active listings pulled in the
  same city (at least 3), and how far the listing sits from it;
- whether it meets the preferred layout, the first sort key;
- for explain mode, the inputs behind each match's numbers and examples of
  each filter's rejections (`Screen.traces`).

A missing price, bed or bath count or size fails its filter: it can't be
verified. A missing HOA, home type or story count passes: Zillow often leaves
them out (a null HOA usually means none). Money is written with its currency
code: "USD 1,000,000".
"""
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agents.property_scout.lib.criteria import Criteria, home_type_of
from agents.property_scout.lib.money import usd

DESCRIPTION_CHARS = 1200
MIN_AREA_SAMPLE = 3
NEAR_MISSES = 5
RULE_EXAMPLES = 3  # rejected listings named per filter in explain mode

# The listing tool's numbers, copied as they are.
TOOL_NUMBERS = (
    "price_per_sqft",
    "rent_zestimate",
    "estimated_annual_gross_rent",
    "estimated_cap_rate_percent",
    "estimated_gross_yield_percent",
    "estimated_monthly_gross_yield_percent",
)


@dataclass
class Screen:
    pulled: int
    matches: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    failed_by_rule: Dict[str, int] = field(default_factory=dict)
    near_misses: List[Dict[str, Any]] = field(default_factory=list)
    traces: List[str] = field(default_factory=list)  # the inputs behind the numbers, for explain mode


def screen(props: Iterable[Dict[str, Any]], criteria: Criteria) -> Screen:
    """Every listing checked against `criteria`, each zpid once.

    Matches come preferred layouts first, then lowest $/sqft. Each row carries
    `missed`: the filters it failed, empty for a match.
    """
    unique, seen = [], set()
    for prop in props:
        if not isinstance(prop, dict):
            continue
        key = prop.get("zpid") or prop.get("url") or _address(prop)
        if key not in seen:
            seen.add(key)
            unique.append(prop)

    areas = _area_medians(unique)
    result = Screen(pulled=len(unique))
    examples: Dict[str, List[str]] = {}
    for prop in unique:
        row = listing_row(prop)
        area = areas.get(_city(prop))
        if area and row["price_per_sqft"]:
            row["area_median_price_per_sqft"] = area[0]
            row["vs_area_median_pct"] = round((row["price_per_sqft"] / area[0] - 1) * 100, 1)
        if criteria.preferred_beds is not None or criteria.preferred_baths is not None:
            row["meets_preferred"] = _meets(row, criteria.preferred_beds, criteria.preferred_baths)
        missed = misses(row, criteria)
        row["missed"] = [f"{rule} ({detail})" if detail else rule for rule, detail in missed]
        if not missed:
            result.matches.append(row)
            result.traces += match_traces(row, area[1] if area else 0)
            continue
        result.rejected.append(row)
        for (rule, _), text in zip(missed, row["missed"]):
            result.failed_by_rule[rule] = result.failed_by_rule.get(rule, 0) + 1
            named = examples.setdefault(rule, [])
            if len(named) < RULE_EXAMPLES:
                named.append(f"{row['address']}{text[len(rule):]}")
        if len(missed) == 1 and len(result.near_misses) < NEAR_MISSES:
            result.near_misses.append(
                {"address": row["address"], "url": row["url"], "price": row["price"], "missed": row["missed"][0]}
            )
    result.matches.sort(key=_match_order)
    for rule, count in result.failed_by_rule.items():
        more = f", and {count - len(examples[rule])} more" if count > len(examples[rule]) else ""
        result.traces.append(f"{rule}: {count} listing{'' if count == 1 else 's'}: {'; '.join(examples[rule])}{more}")
    return result


def listing_row(prop: Dict[str, Any]) -> Dict[str, Any]:
    """The compact row a reply or report needs, with the listing tool's numbers."""
    row = {
        "address": _address(prop),
        "url": prop.get("url") or (f"https://www.zillow.com/homedetails/{prop['zpid']}_zpid/" if prop.get("zpid") else None),
        "zpid": prop.get("zpid"),
        "price": prop.get("price"),
        "beds": prop.get("bedrooms"),
        "baths": prop.get("bathrooms"),
        "sqft": prop.get("living_area"),
        "hoa_fee": prop.get("hoa_fee"),
        "home_type": home_type_of(prop.get("home_type") or prop.get("property_type")),
    }
    if prop.get("stories") is not None:
        row["stories"] = prop.get("stories")
    row.update({name: prop.get(name) for name in TOOL_NUMBERS})
    description = " ".join(str(prop.get("description") or "").split())
    row["description"] = description if len(description) <= DESCRIPTION_CHARS else description[:DESCRIPTION_CHARS] + "…"
    return row


def misses(row: Dict[str, Any], criteria: Criteria) -> List[Tuple[str, str]]:
    """(rule, detail) for each filter the row fails; empty when it passes them all."""
    out: List[Tuple[str, str]] = []
    price, beds, baths, sqft = (_number(row.get(k)) for k in ("price", "beds", "baths", "sqft"))
    if criteria.max_price is not None:
        if price is None:
            out.append(("price not listed", ""))
        elif price > criteria.max_price:
            out.append((f"over {usd(criteria.max_price)}", usd(price)))
    if criteria.min_beds is not None:
        if beds is None:
            out.append(("beds not listed", ""))
        elif beds < criteria.min_beds:
            out.append((f"under {criteria.min_beds:g} beds", f"{beds:g}"))
    if criteria.min_baths is not None:
        if baths is None:
            out.append(("baths not listed", ""))
        elif baths < criteria.min_baths:
            out.append((f"under {criteria.min_baths:g} baths", f"{baths:g}"))
    if criteria.min_sqft is not None:
        if sqft is None:
            out.append(("sqft not listed", ""))
        elif sqft < criteria.min_sqft:
            out.append((f"under {criteria.min_sqft:,.0f} sqft", f"{sqft:,.0f}"))
    hoa = _number(row.get("hoa_fee"))
    if criteria.max_hoa is not None and hoa is not None and hoa > criteria.max_hoa:
        out.append((f"HOA over {usd(criteria.max_hoa)}/mo", f"{usd(hoa)}/mo"))
    home_type = row.get("home_type")
    if criteria.home_types and home_type and home_type not in criteria.home_types:
        out.append((f"not {_type_names(criteria.home_types)}", _type_names([home_type])))
    stories = _number(row.get("stories"))
    if criteria.max_stories is not None and stories is not None and stories > criteria.max_stories:
        out.append((f"over {criteria.max_stories} stories", f"{stories:g}"))
    if criteria.min_cap_rate is not None:
        cap = _number(row.get("estimated_cap_rate_percent"))
        if cap is None:
            out.append(("no cap rate (no rent estimate)", ""))
        elif cap < criteria.min_cap_rate:
            out.append((f"cap rate under {criteria.min_cap_rate:g}%", f"{cap:g}%"))
    return out


def match_traces(row: Dict[str, Any], area_listings: int = 0) -> List[str]:
    """The inputs behind a match's numbers, one line each, for explain mode."""
    name = row["address"]
    price, sqft, per_sqft = (_number(row.get(k)) for k in ("price", "sqft", "price_per_sqft"))
    lines = []
    if per_sqft is not None and price and sqft:
        lines.append(f"{name}: {usd(per_sqft, 2)}/sqft = {usd(price)} / {sqft:,.0f} sqft")
    if row.get("area_median_price_per_sqft") is not None:
        lines.append(f"{name}: city median {usd(row['area_median_price_per_sqft'], 2)}/sqft over "
                     f"{area_listings} listings pulled in its city; {row['vs_area_median_pct']:+g}%")
    cap = _number(row.get("estimated_cap_rate_percent"))
    rent = _number(row.get("rent_zestimate"))
    if cap is None or rent is None:
        lines.append(f"{name}: no rent estimate, so no cap rate or yield")
    else:
        hoa = _number(row.get("hoa_fee")) or 0
        lines.append(f"{name}: est. cap rate {cap:g}% = ({usd(rent)} - HOA {usd(hoa)}) x 12 / {usd(price)}; "
                     f"gross yield {_pct(row.get('estimated_gross_yield_percent'))} = {usd(rent)} x 12 / {usd(price)}")
    return lines


def render_matches(matches: List[Dict[str, Any]]) -> str:
    """The matches as numbered plain-text blocks, numbers formatted for the reply and reports."""
    blocks = []
    for n, row in enumerate(matches, 1):
        value = [
            _money(row.get("price")),
            f"{_count(row.get('beds'))} bd / {_count(row.get('baths'))} ba",
            f"{_count(row.get('sqft'), ',.0f')} sqft",
            _per_sqft(row),
            "HOA none listed" if row.get("hoa_fee") is None else f"HOA {_money(row['hoa_fee'])}/mo",
        ]
        if row.get("home_type"):
            value.append(_type_names([row["home_type"]]))
        if "meets_preferred" in row:
            value.append("preferred layout" if row["meets_preferred"] else "below preferred layout")
        rent = row.get("rent_zestimate")
        income = (
            f"Rent est. {_money(rent)}/mo ({_money(row.get('estimated_annual_gross_rent'))}/yr) | "
            f"gross yield {_pct(row.get('estimated_gross_yield_percent'))} | "
            f"est. cap rate {_pct(row.get('estimated_cap_rate_percent'))}"
            if rent else "No rent estimate: cap rate n/a"
        )
        blocks.append(
            f"{n}. {row['address']}\n   {row.get('url') or 'no link'}\n   {' | '.join(value)}\n   {income}"
            + (f"\n   Description: {row['description']}" if row.get("description") else "")
        )
    return "\n\n".join(blocks)


# ----------------------------------------------------------------------------- helpers

def _address(prop: Dict[str, Any]) -> str:
    street = prop.get("street_address") or prop.get("address") or ""
    if isinstance(street, dict):
        street = street.get("streetAddress") or ""
    place = " ".join(str(p) for p in (prop.get("state"), prop.get("zipcode")) if p)
    return ", ".join(str(p) for p in (street, prop.get("city"), place) if p)


def _city(prop: Dict[str, Any]) -> str:
    return " ".join(str(prop.get("city") or "").lower().split())


def _area_medians(props: List[Dict[str, Any]]) -> Dict[str, Tuple[float, int]]:
    """City -> (median $/sqft, listings it was taken over), for cities with enough listings."""
    by_city: Dict[str, List[float]] = {}
    for prop in props:
        value = _number(prop.get("price_per_sqft"))
        if value and _city(prop):
            by_city.setdefault(_city(prop), []).append(value)
    return {city: (round(median(values), 2), len(values)) for city, values in by_city.items()
            if len(values) >= MIN_AREA_SAMPLE}


def _meets(row: Dict[str, Any], beds: Optional[float], baths: Optional[float]) -> bool:
    have_beds, have_baths = _number(row.get("beds")), _number(row.get("baths"))
    return (beds is None or (have_beds is not None and have_beds >= beds)) and (
        baths is None or (have_baths is not None and have_baths >= baths)
    )


def _match_order(row: Dict[str, Any]):
    per_sqft = _number(row.get("price_per_sqft"))
    return (not row.get("meets_preferred", False), per_sqft if per_sqft is not None else float("inf"))


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _type_names(types: Iterable[str]) -> str:
    return " or ".join(t.replace("_", " ").lower() for t in types)


def _money(value: Any, decimals: int = 0) -> str:
    return usd(value, decimals) or "n/a"


def _count(value: Any, spec: str = "g") -> str:
    number = _number(value)
    return "?" if number is None else format(number, spec)


def _pct(value: Any) -> str:
    number = _number(value)
    return "n/a" if number is None else f"{number:g}%"


def _per_sqft(row: Dict[str, Any]) -> str:
    per_sqft = _number(row.get("price_per_sqft"))
    if per_sqft is None:
        return "price/sqft n/a"
    text = f"{usd(per_sqft, 2)}/sqft"
    if row.get("area_median_price_per_sqft"):
        text += f" (city median {usd(row['area_median_price_per_sqft'], 2)}, {row['vs_area_median_pct']:+g}%)"
    return text
