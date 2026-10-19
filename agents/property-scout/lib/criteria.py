"""The user's real-estate criteria (pkm/wallet/real-estate/criteria.md), read as hard filters.

The file is markdown written for people, and every line is accounted for:

- A "Label: value" line whose label names a filter is read as that filter. The
  first line for a filter wins; a later line that sets it to something else is
  reported.
- A filter line whose value doesn't read comes back raw, as unrecognized
  (partial): that filter isn't applied.
- Blank lines, headings and rules are structure.
- Every other line (strategy, condition, dealbreakers such as school ratings)
  comes back as written, for the agent's judgment (`to_judge`).

Labels it knows, with examples:

    Max Purchase Price: $1,000,000 (Hard Ceiling)       max_price ("USD 1,000,000" reads too)
    Minimum Layout: 3 Beds, 2 Baths, ≥ 1,400 Sq Ft (Preferred: 4 Beds, 2.5 Baths)
                                                        min_beds, min_baths, min_sqft,
                                                        preferred_beds, preferred_baths
    Min Beds: 3 / Min Baths: 2 / Min Sq Ft: 1,400       the same, one per line
    Max HOA: $0 / No HOA                                max_hoa
    Property Type: Single Family Home (1 or 2 stories)  home_types, max_stories
    Key Target Cities: Springfield, Shelbyville         cities
    Target Zip Codes: 62701, 62704                      zipcodes (searched instead of cities)
    State: CA                                           state (needed to search by city)
    Min Cap Rate: 4%                                    min_cap_rate

Other "Max ..." labels ("Max $/SqFt") and lot sizes name no filter, so they are
for judgment.
"""
import os
import re
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.tooling import Report, pkm_path
from agents.property_scout.lib.money import with_currency

# Zillow's home types, and how people write them. More specific patterns come
# first: their text is removed before the next one is tried ("town house").
HOME_TYPE_PATTERNS = (
    ("TOWNHOUSE", r"town\s*(?:house|home)s?"),
    ("MULTI_FAMILY", r"multi[\s-]*family|duplex|triplex|fourplex"),
    ("CONDO", r"\bcondo(?:minium)?s?\b"),
    ("SINGLE_FAMILY", r"single[\s-]*family|\bsfh\b|\bhouses?\b"),
)

_MONEY = re.compile(r"(?:\$|\bUS\$|\bUSD\b)\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m|mil|million|thousand)?\b", re.I)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_BEDS = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:beds?|bedrooms?|bd|br)\b", re.I)
_BATHS = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:baths?|bathrooms?|ba)\b", re.I)
_SQFT = re.compile(r"(\d[\d,]*)\s*\+?\s*(?:sq\.?\s*ft\.?|sqft|square\s*feet|sf)(?![a-z])", re.I)
_PREFERRED = re.compile(r"\(?\s*prefer(?:red|s)?\b", re.I)
_STORIES = re.compile(
    r"(\d+|one|two|three|single)(?:\s*(?:or|to|-|–|/)\s*(\d+|one|two|three))?\s*[- ]?stor(?:y|ies)", re.I
)
_WORDS = {"single": 1, "one": 1, "two": 2, "three": 3}
_ZIP = re.compile(r"\b\d{5}\b")
_STATE = re.compile(r"\b([A-Z]{2})\b")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_UNITS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mil": 1e6, "million": 1e6}
_RULE = re.compile(r"[-*_](?:\s*[-*_]){2,}")  # a markdown rule: ---, ***, ___


@dataclass(frozen=True)
class Criteria:
    """The hard filters, plus the preferred layout (a sort key, never a filter)."""
    cities: Tuple[str, ...] = ()
    zipcodes: Tuple[str, ...] = ()
    state: Optional[str] = None
    max_price: Optional[float] = None
    min_beds: Optional[float] = None
    min_baths: Optional[float] = None
    min_sqft: Optional[float] = None
    max_hoa: Optional[float] = None
    home_types: Tuple[str, ...] = ()
    max_stories: Optional[int] = None
    min_cap_rate: Optional[float] = None
    preferred_beds: Optional[float] = None
    preferred_baths: Optional[float] = None

    def area_problem(self) -> Optional[str]:
        """Why one Zillow job can't search these criteria, or None when it can."""
        if self.zipcodes:
            return None
        if not self.cities:
            return "names no target cities or zip codes"
        if not self.state:
            return "names no state for its target cities: add a line like '- **State**: CA', or list zip codes"
        return None

    def search_areas(self) -> List[str]:
        """What one Zillow job searches: the zip codes, else "City, ST" for each city."""
        problem = self.area_problem()
        if problem:
            raise ValueError(f"criteria.md {problem}")
        if self.zipcodes:
            return list(self.zipcodes)
        return [f"{city}, {self.state}" for city in self.cities]

    def in_force(self) -> Dict[str, Any]:
        """The filters that are set, for the reply."""
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items() if v not in (None, ())}


@dataclass
class ParsedCriteria:
    """criteria.md, every line accounted for."""
    criteria: Criteria
    lines: Dict[str, int]  # each filter read: the number of the line it came from
    to_judge: Tuple[str, ...] = ()  # lines that aren't filters, as written, for the agent's judgment
    unread: Tuple[str, ...] = ()  # filter lines whose value didn't read: those filters aren't applied
    conflicts: Tuple[str, ...] = ()  # later lines that set a filter to another value; the first line wins


def criteria_path() -> str:
    return pkm_path("wallet", "real-estate", "criteria.md")


def load_criteria(report: Report) -> ParsedCriteria:
    """criteria.md, read. A missing file stops the call (failed); what doesn't read goes in `report`."""
    path = criteria_path()
    if not os.path.isfile(path):
        report.missing(path, "file not found")
        report.raise_if_failed()
    with open(path, encoding="utf-8") as f:
        parsed = parse_criteria(f.read())
    report.unparsed(path, parsed.unread)
    for conflict in parsed.conflicts:
        report.problem(path, conflict)
    report.read(path, records=len(parsed.lines), skipped=len(parsed.unread))
    shown = with_currency(parsed.criteria.in_force())
    for key, number in parsed.lines.items():
        report.trace(f"criteria.md line {number}: {key} = {shown[key]}")
    return parsed


def parse_criteria(text: str) -> ParsedCriteria:
    found: Dict[str, Any] = {}
    lines: Dict[str, int] = {}
    to_judge: List[str] = []
    unread: List[str] = []
    conflicts: List[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or _RULE.fullmatch(stripped):
            continue
        values = filter_values(raw)
        if values is None:
            to_judge.append(raw.rstrip())
        elif not values:
            unread.append(raw.rstrip())
        for key, value in (values or {}).items():
            if key not in found:
                found[key], lines[key] = value, number
            elif found[key] != value:
                conflicts.append(f"line {number} ('{stripped}') sets {key} again; line {lines[key]}'s value applies")
    return ParsedCriteria(Criteria(**found), lines, tuple(to_judge), tuple(unread), tuple(conflicts))


def filter_values(line: str) -> Optional[Dict[str, Any]]:
    """The filters a line sets: None when it isn't a filter line, {} when it is but its value doesn't read."""
    text = re.sub(r"^[\s>*+-]*", "", line.replace("**", "").replace("__", ""))
    label, sep, value = text.partition(":")
    if not sep or len(label) > 60:
        return None
    label = " ".join(re.sub(r"[^a-z0-9]+", " ", label.lower()).split())
    read = _reader(label, set(label.split()))
    if read is None:
        return None
    return {key: found for key, found in read(value.strip()).items() if found not in (None, ())}


def _reader(label: str, words: set) -> Optional[Callable[[str], Dict[str, Any]]]:
    """How to read the value of a line with this label; None when the label names no filter."""
    if "budget" in words or ("price" in words and words & {"max", "maximum", "ceiling", "limit"}):
        return lambda value: {"max_price": _money(value)}
    if "layout" in words:
        return _layout
    if "hoa" in words:
        return _hoa
    if "type" in words and words & {"property", "home"}:
        return lambda value: {"home_types": home_types_in(value), "max_stories": _max_stories(value)}
    if "cities" in words or label in ("city", "target city"):
        return _cities
    if any(w.startswith("zip") for w in words):
        return lambda value: {"zipcodes": tuple(dict.fromkeys(_ZIP.findall(value)))}
    if label in ("state", "target state"):
        return lambda value: {"state": _first_group(_STATE, value)}
    if "cap rate" in label and words & {"min", "minimum", "target"}:
        return lambda value: {"min_cap_rate": _first(_PERCENT, value)}
    if words & {"max", "maximum"} or "lot" in words:
        return None  # e.g. "Max $/SqFt", "Lot Size": not a minimum, and not a filter this file defines
    if "bed" in label:
        return lambda value: {"preferred_beds" if "prefer" in label else "min_beds": _first_number(value)}
    if "bath" in label:
        return lambda value: {"preferred_baths" if "prefer" in label else "min_baths": _first_number(value)}
    if words & {"sqft", "size", "square"} or "sq ft" in label or "living area" in label:
        return lambda value: {"min_sqft": _first_number(value)}
    return None


def with_overrides(criteria: Criteria, overrides: Dict[str, Any]) -> Criteria:
    """`criteria` with some filters replaced for one call; None drops a filter.

    Naming cities without zip codes searches those cities instead of the
    file's zip codes. Raises ValueError for an unknown key or a bad value.
    """
    names = [f.name for f in fields(Criteria)]
    unknown = sorted(set(overrides) - set(names))
    if unknown:
        raise ValueError(f"unknown criteria key(s): {', '.join(unknown)}; allowed: {', '.join(names)}")
    changes: Dict[str, Any] = {}
    for key, value in overrides.items():
        if key in ("cities", "zipcodes", "home_types"):
            items = value if isinstance(value, (list, tuple)) else re.split(r"[,;]", str(value or ""))
            items = tuple(str(i).strip() for i in items if str(i).strip())
            if key == "home_types":
                items = tuple(home_type_of(i) for i in items)
            changes[key] = items
        elif value is None:
            changes[key] = None
        elif key == "state":
            changes[key] = str(value).strip().upper()
        else:
            try:
                number = float(re.sub(r"(?i)\bUSD\b|US\$|[$,%\s]", "", str(value)))
            except ValueError:
                raise ValueError(f"criteria '{key}' must be a number, got {value!r}") from None
            changes[key] = int(number) if key == "max_stories" else number
    if "cities" in changes and "zipcodes" not in changes:
        changes["zipcodes"] = ()
    return replace(criteria, **changes)


def home_types_in(text: str) -> Tuple[str, ...]:
    """Zillow home types a phrase names: "Single Family House" -> ("SINGLE_FAMILY",)."""
    found, rest = [], text.lower()
    for home_type, pattern in HOME_TYPE_PATTERNS:
        if re.search(pattern, rest):
            found.append(home_type)
            rest = re.sub(pattern, " ", rest)
    return tuple(found)


def home_type_of(value: Any) -> Optional[str]:
    """A listing's home type in Zillow's spelling ("SINGLE_FAMILY"), whatever form it came in."""
    if not value:
        return None
    text = str(value)
    compact = re.sub(r"[^A-Z]", "", text.upper())
    for home_type, _ in HOME_TYPE_PATTERNS:
        if compact.startswith(home_type.replace("_", "")):
            return home_type
    named = home_types_in(text.replace("_", " "))
    return named[0] if named else re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")


def _layout(value: str) -> Dict[str, Any]:
    minimum, preferred = _split_preferred(value)
    return {
        "min_beds": _first(_BEDS, minimum),
        "min_baths": _first(_BATHS, minimum),
        "min_sqft": _first(_SQFT, minimum),
        "preferred_beds": _first(_BEDS, preferred),
        "preferred_baths": _first(_BATHS, preferred),
    }


def _hoa(value: str) -> Dict[str, Any]:
    amount = _money(value)
    if amount is None and re.search(r"\bno hoa\b|\bnone\b", value, re.I):
        amount = 0.0
    return {"max_hoa": amount}


def _cities(value: str) -> Dict[str, Any]:
    cities = (re.sub(r"\(.*?\)", "", c).strip(" .") for c in re.split(r"[,;]", value))
    return {"cities": tuple(c for c in cities if c)}


def _split_preferred(value: str) -> Tuple[str, str]:
    """Splits "3 Beds, 2 Baths (Preferred: 4 Beds, 3 Baths)" into its minimum and preferred parts."""
    match = _PREFERRED.search(value)
    if not match:
        return value, ""
    return value[:match.start()], value[match.end():]


def _money(text: str) -> Optional[float]:
    match = _MONEY.search(text)
    if not match:
        return None
    return float(match.group(1).replace(",", "")) * _UNITS.get((match.group(2) or "").lower(), 1)


def _first(pattern: re.Pattern, text: str) -> Optional[float]:
    match = pattern.search(text or "")
    return float(match.group(1).replace(",", "")) if match else None


def _first_group(pattern: re.Pattern, text: str) -> Optional[str]:
    match = pattern.search(text or "")
    return match.group(1) if match else None


def _first_number(text: str) -> Optional[float]:
    match = _NUMBER.search(text)
    return float(match.group(0).replace(",", "")) if match else None


def _max_stories(text: str) -> Optional[int]:
    match = _STORIES.search(text)
    if not match:
        return None
    counts = [int(_WORDS.get(n.lower(), n)) for n in match.groups() if n]
    return max(counts)
