"""Calendar periods for recurring card credits: months, quarters, halves and years.

`current_period(cycle, today)` is the period a credit is in on `today`.
`parse_period(text)` reads the period a tracker log entry starts with:

    "Aug 2026"             the month
    "2026 Q1", "Q1 2026"   the quarter
    "2026 H2", "H2 2026"   the half year
    "2026"                 the calendar year
    "2025–2026 Cycle"      a cardmember year, whose dates aren't known

A month range in parentheses wins over the quarter or half number, so
"2026 Q2 (Jul–Sep)" is July to September.
"""
import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

CYCLES = ("monthly", "quarterly", "semiannual", "annual")
CARDMEMBER = "cardmember year"

_MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_abbr) if name}
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?(?![a-z])"
)
_RANGE = re.compile(rf"\(\s*({_MONTH})\s*[–—-]\s*({_MONTH})\s*\)", re.IGNORECASE)
_MONTH_YEAR = re.compile(rf"({_MONTH})\s+(\d{{4}})\b", re.IGNORECASE)
_QUARTER = re.compile(r"(?:(\d{4})\s*Q([1-4])|Q([1-4])\s*(\d{4}))\b", re.IGNORECASE)
_HALF = re.compile(r"(?:(\d{4})\s*H([12])|H([12])\s*(\d{4}))\b", re.IGNORECASE)
_SPAN = re.compile(r"(\d{4})\s*[–—-]\s*(\d{4})\b")
_YEAR = re.compile(r"(\d{4})\b")


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # the last day, inclusive
    label: str
    cardmember: bool = False  # a cardmember-year span: the years are known, not the dates

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def same_as(self, other: "Period") -> bool:
        """Same calendar dates; cardmember spans never match a calendar period."""
        return not (self.cardmember or other.cardmember) and (self.start, self.end) == (other.start, other.end)


def months(year: int, first: int, last: int, label: str) -> Period:
    """`first` to `last` month of `year`; a range like Nov–Jan ends in the next year."""
    end_year = year + 1 if last < first else year
    return Period(date(year, first, 1), date(end_year, last, calendar.monthrange(end_year, last)[1]), label)


def current_period(cycle: str, today: date) -> Period:
    """The period of a `cycle` credit that contains `today`."""
    year, month = today.year, today.month
    if cycle == "monthly":
        return months(year, month, month, f"{calendar.month_abbr[month]} {year}")
    if cycle == "quarterly":
        first = 3 * ((month - 1) // 3) + 1
        return months(year, first, first + 2, f"Q{(first + 2) // 3} {year} ({_range(first, first + 2)})")
    if cycle == "semiannual":
        first = 1 if month <= 6 else 7
        return months(year, first, first + 5, f"H{1 if first == 1 else 2} {year} ({_range(first, first + 5)})")
    if cycle == "annual":
        return months(year, 1, 12, str(year))
    raise ValueError(f"unknown cycle {cycle!r}; expected one of {', '.join(CYCLES)}")


def parse_period(text: str) -> Optional[Period]:
    """The period a log entry starts with, or None when it starts with none."""
    head = re.sub(r"[*_]", "", text).strip(" \t•-")
    label = head.split(":", 1)[0].strip()
    if match := _MONTH_YEAR.match(head):
        month = _MONTHS[match.group(1)[:3].lower()]
        return months(int(match.group(2)), month, month, label)
    if match := _QUARTER.match(head):
        year, quarter = int(match.group(1) or match.group(4)), int(match.group(2) or match.group(3))
        period = months(year, 3 * quarter - 2, 3 * quarter, label)
    elif match := _HALF.match(head):
        year, half = int(match.group(1) or match.group(4)), int(match.group(2) or match.group(3))
        period = months(year, 6 * half - 5, 6 * half, label)
    elif match := _SPAN.match(head):
        return Period(date(int(match.group(1)), 1, 1), date(int(match.group(2)), 12, 31), label, cardmember=True)
    elif match := _YEAR.match(head):
        period = months(int(match.group(1)), 1, 12, label)
    else:
        return None
    if explicit := _RANGE.search(label):
        first, last = (_MONTHS[explicit.group(i)[:3].lower()] for i in (1, 2))
        period = months(period.start.year, first, last, label)
    return period


def _range(first: int, last: int) -> str:
    return f"{calendar.month_abbr[first]}–{calendar.month_abbr[last]}"
