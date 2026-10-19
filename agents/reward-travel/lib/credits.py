"""Statement-credit windows: where each recurring card credit stands today.

For every credit in the card files: its current period, the period's last day,
days left, its status this period, and a flag when it needs using:

- "lapsing": not used, and its deadline is close (`lapse_days` in RULES.md, by cycle);
- "behind": not used, and more than `behind_after_pct` of its period has gone.

A credit with no log entry for the current period counts as not used. A credit
logged "Auto (...)" is used automatically, so it is never flagged. Credits on a
cardmember year have no known dates, so they get a status but never a flag.
"""
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.tooling import Report
from agents.reward_travel.lib.card_files import (
    AUTO,
    CARDMEMBER,
    MISSED,
    OPEN,
    USED,
    CreditRow,
    FileInfo,
    FiveTwentyFour,
    LogEntry,
    read_card_files,
)
from agents.reward_travel.lib.periods import CYCLES, Period, current_period
from agents.reward_travel.lib.rules import RULES

UNUSED_STATUSES = ("unused", "partly used", "not logged")
_FLAG_RANK = {None: 0, "behind": 1, "lapsing": 2}
_DEFAULTS = {rule.key: rule.default for rule in RULES}


@dataclass(frozen=True)
class Thresholds:
    """The RULES.md values the windows use; lib/rules.py has the defaults."""
    behind_after_pct: int
    lapse_days: Mapping[str, int]  # by cycle
    stale_after_months: int


@dataclass(frozen=True)
class CreditWindow:
    name: str
    value: str
    cycle: Optional[str]
    period: Optional[Period]  # None on a cardmember year or an unknown cycle
    days_left: Optional[int]  # days after today until the period ends (0 on its last day)
    status: str  # "used", "auto", "missed", or one of UNUSED_STATUSES
    remaining: Optional[str] = None  # "135 USD" when partly used
    flag: Optional[str] = None  # "lapsing", "behind" or None
    by: Optional[str] = None  # what uses an auto credit: "Example TV"
    notes: Tuple[str, ...] = ()  # the row's remarks, as written

    @property
    def unused(self) -> bool:
        return self.status in UNUSED_STATUSES

    def describe(self) -> str:
        """One line for a reminder: "Sam's Coffee Credit (Example Dining), 5 USD / mo: not logged in Oct 2026, ..."."""
        status = f"partly used ({self.remaining} left)" if self.remaining else self.status
        if self.period is None:
            return f"{self.name}, {self.value}: {status} ({self.cycle or 'cycle unknown'})"
        return (
            f"{self.name}, {self.value}: {status} in {self.period.label}, "
            f"{self.days_left} days left (ends {self.period.end.isoformat()})"
        )


@dataclass(frozen=True)
class FileAge:
    name: str
    last_updated: Optional[str]  # as written in the file
    months_old: Optional[int]  # calendar months since that update
    stale: bool  # older than `stale_after_months`


@dataclass(frozen=True)
class CreditSummary:
    today: date
    windows: List[CreditWindow]  # most urgent first
    five_24: List[FiveTwentyFour]
    files: List[FileAge]

    @property
    def flagged(self) -> List[CreditWindow]:
        return [w for w in self.windows if w.flag]


def thresholds(rules: Dict[str, Any], report: Report, source: str) -> Thresholds:
    """The windows' thresholds from `load_rules`' values.

    A value out of range, or a `lapse_days` entry for no cycle, goes in `report`
    (named after `source`, the RULES.md path) and the default applies.
    """
    def checked(key: str, value: int, low: int, high: Optional[int], default: int) -> int:
        if value < low or (high is not None and value > high):
            span = f"{low} to {high}" if high is not None else f"{low} or more"
            report.problem(source, f"{key}: {value} is out of range ({span}); using the default {default}")
            return default
        return value

    lapse_days = dict(_DEFAULTS["lapse_days"])
    for name, days in rules["lapse_days"].items():
        cycle = name.strip().lower().replace("-", "").replace(" ", "")
        if cycle not in CYCLES:
            report.problem(source, f"lapse_days: '{name}' is no cycle ({', '.join(CYCLES)}); ignored")
            continue
        lapse_days[cycle] = checked(f"lapse_days {name}", days, 0, None, _DEFAULTS["lapse_days"][cycle])
    return Thresholds(
        behind_after_pct=checked("behind_after_pct", rules["behind_after_pct"], 0, 100, _DEFAULTS["behind_after_pct"]),
        lapse_days=lapse_days,
        stale_after_months=checked("stale_after_months", rules["stale_after_months"], 0, None,
                                   _DEFAULTS["stale_after_months"]),
    )


def credit_windows(folder: str, today: date, limits: Thresholds, report: Report) -> CreditSummary:
    """Every credit's window on `today`, the 5/24 lines, and each file's age.

    What the files can't tell goes in `report` (card_files.read_card_files).
    """
    cards = read_card_files(folder, report)
    windows = sorted((window_for(row, today, limits, report) for row in cards.credits), key=_urgency)
    ages = [file_age(info, today, limits.stale_after_months, report) for info in cards.files]
    return CreditSummary(today, windows, cards.five_24, ages)


def window_for(row: CreditRow, today: date, limits: Thresholds, report: Optional[Report] = None) -> CreditWindow:
    period = current_period(row.cycle, today) if row.cycle in CYCLES else None
    entry = _entry_for(row, period, today)
    status, remaining = _status(entry)
    window = CreditWindow(
        name=row.name,
        value=row.value,
        cycle=row.cycle,
        period=period,
        days_left=(period.end - today).days if period else None,
        status=status,
        remaining=remaining,
        by=entry.by if entry is not None and entry.status == AUTO else None,
        notes=row.notes,
    )
    window = replace(window, flag=flag_on(window, today, limits))
    if report is not None:
        report.trace(_trace(window, entry, today, limits))
    return window


def stage_on(period: Period, day: date, behind_after_pct: int, lapse_days: int) -> Optional[str]:
    """The flag an unused credit has on `day` of `period`: "lapsing", "behind" or None.

    Lapsing: `lapse_days` or fewer days left after `day`. Behind: more than
    `behind_after_pct` of the period's days gone, `day` included.
    """
    if (period.end - day).days <= lapse_days:
        return "lapsing"
    if ((day - period.start).days + 1) * 100 > behind_after_pct * period.days:
        return "behind"
    return None


def flag_on(window: CreditWindow, day: date, limits: Thresholds) -> Optional[str]:
    """The flag the credit has on `day` of its current period, given its status today."""
    period = window.period
    if not window.unused or period is None or not period.contains(day):
        return None
    return stage_on(period, day, limits.behind_after_pct, limits.lapse_days[window.cycle])


def newly_flagged(summary: CreditSummary, since: Optional[date], limits: Thresholds) -> List[CreditWindow]:
    """Flagged credits whose flag is new since `since` (None: all of them).

    So each credit comes up when it falls behind, and again when it starts to
    lapse, rather than on every check in between.
    """
    return [w for w in summary.flagged
            if since is None or _FLAG_RANK[w.flag] > _FLAG_RANK[flag_on(w, since, limits)]]


def file_age(info: FileInfo, today: date, stale_after_months: int, report: Optional[Report] = None) -> FileAge:
    if info.updated is None:
        return FileAge(info.name, info.last_updated, None, False)
    months_old = (today.year - info.updated.year) * 12 + today.month - info.updated.month
    age = FileAge(info.name, info.last_updated, months_old, months_old > stale_after_months)
    if report is not None:
        report.trace(f"{info.name}: Last Updated {info.last_updated} -> {months_old} months old; "
                     f"stale after {stale_after_months} (stale_after_months): {'yes' if age.stale else 'no'}")
    return age


def _entry_for(row: CreditRow, period: Optional[Period], today: date) -> Optional[LogEntry]:
    """The log entry for the current period: the last one with its dates, or, on a
    cardmember year, the latest cycle that includes this year. Without one, an
    "Auto" entry stands for every period."""
    if period is not None:
        matches = [e for e in row.entries if e.period and e.period.same_as(period)]
    elif row.cycle == CARDMEMBER:
        cycles = [e for e in row.entries if e.period and e.period.cardmember and e.period.contains(today)]
        matches = sorted(cycles, key=lambda e: e.period.end)
    else:
        matches = []
    if not matches:
        matches = [e for e in row.entries if e.status == AUTO]
    return matches[-1] if matches else None


def _status(entry: Optional[LogEntry]):
    if entry is None:
        return "not logged", None
    if entry.status == USED:
        return "used", None
    if entry.status == AUTO:
        return "auto", None
    if entry.status == MISSED:
        return "missed", None
    if entry.status == OPEN:
        return ("partly used", entry.remaining) if entry.remaining else ("unused", None)
    return "not logged", None


def _trace(window: CreditWindow, entry: Optional[LogEntry], today: date, limits: Thresholds) -> str:
    """How a credit's status and flag were reached, for explain mode."""
    logged = f"'{entry.text}'" if entry is not None else "no entry for this period"
    head = f"{window.name}: {logged} -> {window.status}"
    period = window.period
    if period is None:
        return f"{head}; {window.cycle or 'no cycle'}, so no window and no flag"
    gone = (today - period.start).days + 1
    head += (f"; {period.label}, day {gone} of {period.days}, {window.days_left} days left "
             f"(lapse_days {window.cycle} {limits.lapse_days[window.cycle]}, behind_after_pct {limits.behind_after_pct})")
    return f"{head} -> {window.flag or 'no flag'}"


def _urgency(window: CreditWindow):
    rank = {"lapsing": 0, "behind": 1}.get(window.flag, 2 if window.unused else 3)
    return rank, window.days_left if window.days_left is not None else 10**6, window.name
