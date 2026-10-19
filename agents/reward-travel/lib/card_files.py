"""The card files in pkm/wallet/credit-card/: the statement-credit tracker's tables,
each file's **Last Updated:** line, and each player's 5/24 status.

The tracker is Markdown the agent keeps. What this parser relies on:

- each recurring credit is a row of a table with a "Value" column; the first
  column names the credit and its card, and the last column is its log;
- its cycle comes from a "Cycle" column, else the value ("$10 / mo"), else the
  value column's header or the section heading ("Monthly Credits");
- log entries are separated by "<br>" or "•", and each starts with its period:
  "Aug 2026: Redeemed (2026-08-11)", "2026 H2: Active / Pending Use",
  "2026: $45 used. **$135 Remaining**" (see periods.parse_period);
- an entry "Auto (Example TV)" marks a credit that is used automatically;
- an entry with no period and no word of use ("Monthly Transit ($10)") is a note.

Nothing is dropped quietly: a log entry whose period or use can't be told, a
row of a Value table that isn't a credit, and a **Last Updated:** date that
doesn't read go in the Report. Text outside the credit tables is left to the
agent.
"""
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Iterator, List, Optional, Tuple

from core.tooling import Report
from agents.reward_travel.lib.money import dollars_as_usd
from agents.reward_travel.lib.periods import CARDMEMBER, Period, parse_period

USED, MISSED, OPEN, AUTO, UNKNOWN = "used", "missed", "open", "auto", "unknown"

# Tried in order, so "Semi-Annual" isn't read as "Annual".
_CYCLE_WORDS = (
    ("semiannual", re.compile(r"semi-?annual|half-?year", re.IGNORECASE)),
    ("quarterly", re.compile(r"quarter", re.IGNORECASE)),
    ("monthly", re.compile(r"month|/\s*mo\b", re.IGNORECASE)),
    ("annual", re.compile(r"annual|yearly|/\s*yr\b|/\s*year", re.IGNORECASE)),
)
_CARDMEMBER_WORDS = re.compile(r"renewal|anniversary|card\s?member year", re.IGNORECASE)
# "$135 Remaining", or "135 USD remaining" as the tools write money.
_REMAINING = re.compile(r"(?:\$\s?([\d,]+(?:\.\d{1,2})?)|([\d,]+(?:\.\d{1,2})?)\s*USD)\s*remaining", re.IGNORECASE)
_MISSED = re.compile(r"\b(missed|expired|forfeited|lapsed)\b", re.IGNORECASE)
_OPEN = re.compile(r"\b(pending|active|in progress|in window|upcoming|unused|not used|available|needs use)\b", re.IGNORECASE)
_USED = re.compile(r"\b(redeemed|used|done|applied)\b", re.IGNORECASE)
# "Auto", "Auto (Example TV)", "Auto: Example TV"; not "Automatic" or "auto-credited".
_AUTO = re.compile(r"^auto(?=$|[\s(:])\s*:?\s*(?:\((?P<paren>[^)]*)\)|(?P<rest>.*))", re.IGNORECASE)
_LAST_UPDATED = re.compile(r"\*\*Last Updated:?\*\*:?\s*(.+)", re.IGNORECASE)
_FIVE_24 = re.compile(r"5/24 status\s*:?\s*\**\s*:?\s*(.+)$", re.IGNORECASE)
_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%B %Y", "%b %Y", "%Y-%m-%d", "%Y-%m")


@dataclass(frozen=True)
class LogEntry:
    period: Optional[Period]
    status: str  # USED, MISSED, OPEN, AUTO or UNKNOWN
    remaining: Optional[str]  # "135 USD" when part of the credit is left
    text: str
    by: Optional[str] = None  # what uses an AUTO credit: "Example TV"

    @property
    def is_note(self) -> bool:
        """No period and no word of use: a remark, not a log of any period."""
        return self.period is None and self.status in (OPEN, UNKNOWN) and not self.remaining

    @property
    def readable(self) -> bool:
        """The parser can tell what the entry says: a period and its use, Auto, or a note."""
        if self.status == AUTO or self.is_note:
            return True
        return self.period is not None and self.status != UNKNOWN


@dataclass(frozen=True)
class CreditRow:
    name: str
    value: str  # as written, a bare "$" as USD: "5 USD / mo"
    cycle: Optional[str]  # one of CYCLES, CARDMEMBER, or None when the row doesn't say
    entries: Tuple[LogEntry, ...]  # every readable entry but the notes
    source: str  # the file it's in
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FiveTwentyFour:
    player: str
    count: Optional[int]  # the highest count the line gives ("~0/24 to 1/24" is 1)
    text: str


@dataclass(frozen=True)
class FileInfo:
    name: str
    last_updated: Optional[str]  # as written, e.g. "August 2026"
    updated: Optional[date]  # parsed; the 1st for a month-only date


@dataclass
class CardFiles:
    credits: List[CreditRow] = field(default_factory=list)
    five_24: List[FiveTwentyFour] = field(default_factory=list)
    files: List[FileInfo] = field(default_factory=list)


def read_card_files(folder: str, report: Report) -> CardFiles:
    """Every .md file in `folder`, parsed; what can't be read goes in `report`.

    Required: the folder, and at least one credit table in it.
    """
    cards = CardFiles()
    if not os.path.isdir(folder):
        report.missing(folder, "folder not found")
        return cards
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            report.problem(path, f"can't be read ({type(e).__name__}: {e})")
            continue
        parsed = parse_card_file(name, text, report, source=path)
        cards.credits += parsed.credits
        cards.five_24 += parsed.five_24
        cards.files += parsed.files
    if not cards.credits:
        report.missing(folder, "no statement-credit table (a table with a Value column) in any card file")
    return cards


def parse_card_file(name: str, text: str, report: Optional[Report] = None, source: Optional[str] = None) -> CardFiles:
    """One card file. Lines it can't read go in `report`, named after `source` (default `name`)."""
    report = report if report is not None else Report()
    source = source or name
    lines = text.splitlines()
    cards = CardFiles(files=[_file_info(name, lines, report, source)])
    heading = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
        elif match := _FIVE_24.search(stripped):
            status = _clean(match.group(1)).rstrip(".")
            counts = [int(n) for n in re.findall(r"(\d+)\s*/\s*24", status)]
            cards.five_24.append(FiveTwentyFour(heading, max(counts) if counts else None, status))
    unread: List[str] = []
    for heading, header, rows in _tables(lines):
        credits, skipped = _credit_rows(name, heading, header, rows)
        cards.credits += credits
        unread += skipped
    for row in cards.credits:
        if row.cycle is None:
            report.problem(source, f"'{row.name}': no cycle (monthly, quarterly, semi-annual, annual or a "
                                   f"cardmember year) in its row; its window isn't computed")
    report.unparsed(source, unread)
    report.read(source, records=len(cards.credits) + len(cards.five_24), skipped=len(unread))
    return cards


def parse_entry(text: str) -> LogEntry:
    """One log entry: its period, and what it says about the credit."""
    clean = _clean(text)
    if match := _AUTO.match(clean):
        by = (match.group("paren") or match.group("rest") or "").strip() or None
        return LogEntry(None, AUTO, None, clean, by)
    status, remaining = UNKNOWN, None
    if match := _REMAINING.search(clean):
        written = match.group(1) or match.group(2)
        amount = Decimal(written.replace(",", ""))
        status, remaining = (OPEN, f"{written} USD") if amount > 0 else (USED, None)
    elif _MISSED.search(clean):
        status = MISSED
    elif _OPEN.search(clean):
        status = OPEN
    elif _USED.search(clean):
        status = USED
    return LogEntry(parse_period(clean), status, remaining, clean)


def _credit_rows(source: str, heading: str, header: List[str],
                 rows: List[Tuple[str, List[str]]]) -> Tuple[List[CreditRow], List[str]]:
    """(credits, lines not read) for one table. A table without a Value column holds no credits."""
    lowered = [h.lower() for h in header]
    value_col = next((i for i, h in enumerate(lowered) if "value" in h), None)
    if value_col is None:
        return [], []
    log_col = len(header) - 1
    if log_col in (0, value_col):
        return [], [raw for raw, _ in rows]  # a Value column but no log after it
    cycle_col = next((i for i, h in enumerate(lowered) if "cycle" in h), None)
    credits, unread = [], []
    for raw, cells in rows:
        cells = cells + [""] * (len(header) - len(cells))
        name = _clean(cells[0])
        if not name:
            if any(_clean(cell) for cell in cells):
                unread.append(raw)
            continue
        entries = tuple(parse_entry(piece) for piece in re.split(r"<br\s*/?>|•", cells[log_col]) if _clean(piece))
        unread += [f"{name}: {entry.text}" for entry in entries if not entry.readable]
        if any(e.period and e.period.cardmember for e in entries) or _CARDMEMBER_WORDS.search(cells[log_col]):
            cycle = CARDMEMBER
        else:
            hints = [cells[cycle_col] if cycle_col is not None else "", cells[value_col], header[value_col], heading]
            cycle = next((c for hint in hints for c, pattern in _CYCLE_WORDS if pattern.search(hint)), None)
        logged = tuple(e for e in entries if e.readable and not e.is_note)
        notes = tuple(e.text for e in entries if e.is_note)
        credits.append(CreditRow(name, dollars_as_usd(_clean(cells[value_col])), cycle, logged, source, notes))
    return credits, unread


def _tables(lines: List[str]) -> Iterator[Tuple[str, List[str], List[Tuple[str, List[str]]]]]:
    """(section heading, header cells, [(raw line, cells)]) for each Markdown table."""
    heading, i = "", 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
        if line.startswith("|") and i + 1 < len(lines) and _is_separator(lines[i + 1]):
            header, rows, i = _cells(line), [], i + 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append((lines[i].strip(), _cells(lines[i])))
                i += 1
            yield heading, header, rows
            continue
        i += 1


def _is_separator(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and "-" in stripped and not stripped.replace("|", "").replace(":", "").replace("-", "").strip()


def _cells(line: str) -> List[str]:
    inner = line.strip().strip("|")
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", inner)]


def _clean(text: str) -> str:
    """Text without Markdown emphasis or line breaks."""
    return " ".join(re.sub(r"<br\s*/?>|\*+", " ", text).split())


def _file_info(name: str, lines: List[str], report: Report, source: str) -> FileInfo:
    for line in lines:
        if match := _LAST_UPDATED.search(line):
            written = _clean(match.group(1)).rstrip(".")
            for fmt in _DATE_FORMATS:
                try:
                    return FileInfo(name, written, datetime.strptime(written, fmt).date())
                except ValueError:
                    continue
            report.problem(source, f"**Last Updated:** '{written}' isn't a date like 'August 2026' or "
                                   f"'October 9, 2026'; the file's age is unknown")
            return FileInfo(name, written, None)
    return FileInfo(name, None, None)
