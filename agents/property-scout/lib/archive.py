"""Listings already evaluated: the real_estate_scout skill's archive.

    TOUR  a report per listing in pkm/wallet/real-estate/properties/<Street_Address_City>.md,
          with "- **Address**: <street>, <city>, <ST> <zip>" and "- **Link**: <Zillow URL>"
    PASS  one line per listing in pkm/wallet/real-estate/passed.md:
          "- 2026-10-16 | 123 Main St, Shelbyville, IL 62702 | <Zillow URL> | busy road"

A listing counts as evaluated when its zpid (from a Zillow link) or its street
address and city appear in either. Reports written before they carried a link
still match by address or file name. A passed.md line that is neither one of
these PASS lines, a heading nor blank comes back raw (partial): the listing it
names isn't counted as evaluated.
"""
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.tooling import Report, pkm_path

REPORTS_DIR = "properties"
PASS_LOG = "passed.md"

_ZPID = re.compile(r"(\d+)_zpid")
_ADDRESS_LINE = re.compile(r"\*\*Address\*\*\s*:?\s*(.+)", re.I)
_TITLE = re.compile(r"^#\s*Property Evaluation Report\s*:\s*(.+)$", re.I | re.M)
_RULE = re.compile(r"[-*_](?:\s*[-*_]){2,}")  # a markdown rule: ---, ***, ___
_ABBREVIATIONS = {
    "street": "st", "avenue": "ave", "drive": "dr", "road": "rd", "boulevard": "blvd",
    "court": "ct", "lane": "ln", "place": "pl", "terrace": "ter", "circle": "cir",
    "highway": "hwy", "parkway": "pkwy", "north": "n", "south": "s", "east": "e", "west": "w",
}


def real_estate_dir() -> str:
    return pkm_path("wallet", "real-estate")


def address_key(address: str) -> str:
    """Street and city of "street, city, ST zip", normalized.

    "120 Maple Drive, Springfield, IL 62701" -> "120mapledrspringfield", the
    same key the report file 120_Maple_Dr_Springfield.md gives.
    """
    return _normalize(" ".join(part for part in address.split(",")[:2]))


@dataclass
class Archive:
    """What was evaluated, each zpid and address key with where it's recorded."""
    zpids: Dict[str, str] = field(default_factory=dict)
    keys: Dict[str, str] = field(default_factory=dict)

    def add_report(self, name: str, text: str) -> None:
        """A TOUR report: its file name, its Address lines and title, and the zpids in its links."""
        source = f"{REPORTS_DIR}/{name}"
        for zpid in _ZPID.findall(text):
            self.zpids.setdefault(zpid, source)
        addresses = [m.group(1) for m in _ADDRESS_LINE.finditer(text)] + _TITLE.findall(text)
        stem = name[:-3] if name.endswith(".md") else name
        self._add_keys([_normalize(stem)] + [address_key(a) for a in addresses], source)

    def add_pass_log(self, text: str) -> Tuple[int, List[str]]:
        """passed.md's PASS lines: (lines read, lines that are none of PASS line, heading or blank)."""
        read, unread = 0, []
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or _RULE.fullmatch(stripped):
                continue
            fields = [part.strip() for part in stripped.lstrip("-* ").split("|")]
            zpids = _ZPID.findall(line)
            key = address_key(fields[1]) if len(fields) >= 2 else ""
            if not stripped.startswith(("-", "*")) or not (zpids or key):
                unread.append(line)
                continue
            read += 1
            source = f"{PASS_LOG} line {number}"
            for zpid in zpids:
                self.zpids.setdefault(zpid, source)
            self._add_keys([key], source)
        return read, unread

    def find(self, row: Dict[str, Any]) -> Optional[str]:
        """Where a listing row (address, url, zpid) was already evaluated; None when it's new."""
        zpids = set(_ZPID.findall(row.get("url") or ""))
        if row.get("zpid") is not None:
            zpids.add(str(row["zpid"]))
        for zpid in sorted(zpids):
            if zpid in self.zpids:
                return self.zpids[zpid]
        key = address_key(row.get("address") or "")
        if not key:
            return None
        # A key that runs on (a file name with the state and zip) still matches.
        return next((source for known, source in self.keys.items() if known.startswith(key)), None)

    def _add_keys(self, keys: List[str], source: str) -> None:
        for key in keys:
            if key:
                self.keys.setdefault(key, source)


def load_archive(report: Report) -> Archive:
    """The TOUR reports and passed.md. Lines passed.md can't account for go in `report`."""
    archive = Archive()
    base = real_estate_dir()
    reports = os.path.join(base, REPORTS_DIR)
    if os.path.isdir(reports):
        names = [name for name in sorted(os.listdir(reports)) if name.endswith(".md") and not name.startswith(".")]
        for name in names:
            with open(os.path.join(reports, name), encoding="utf-8", errors="replace") as f:
                archive.add_report(name, f.read())
        report.read(reports, records=len(names))
    log = os.path.join(base, PASS_LOG)
    if os.path.isfile(log):
        with open(log, encoding="utf-8", errors="replace") as f:
            read, unread = archive.add_pass_log(f.read())
        report.unparsed(log, unread)
        report.read(log, records=read, skipped=len(unread))
    return archive


def _normalize(text: str) -> str:
    return "".join(_ABBREVIATIONS.get(word, word) for word in re.findall(r"[a-z0-9]+", text.lower()))
