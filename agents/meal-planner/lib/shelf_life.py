"""When an inventory row expires.

Rows written by kitchen_inventory carry their date in the note ('冷藏保存，保鮮至
2026-10-23'); a shelf life typed by hand ('約 7 天', '2 週') counts too. Older
rows only say how they're stored ('冷藏保存', '冷凍保存'), so for those a shelf
life is estimated from the item, its section and that note, counted from the
row's 入庫日期. The days come from RULES.md (defaults in lib/rules.py). The
estimates are rough on purpose: they rank what to use first, and the snapshot
marks them.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Mapping, Optional, Tuple

from agents.meal_planner.lib.rules import RULES

EXPIRY_LABEL = "保鮮至"

_ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_DURATION = re.compile(r"(\d+)\s*(天|週|周|個月)")
_DAYS_PER = {"天": 1, "週": 7, "周": 7, "個月": 30}

# Sections whose contents keep for months whatever their note says (sauces_snacks_days).
_LONG_SECTIONS = re.compile(r"醬|醃|飲品|休閒|零食|sauce|pickle|condiment|beverage|drink|snack", re.I)

# Sections by kind, for fridge_days, room_temp_days and unstated_days; the first match wins.
_KINDS = tuple((kind, re.compile(pattern, re.I)) for kind, pattern in (
    ("seafood", r"海鮮|魚|seafood|fish"),
    ("meat", r"肉|meat"),
    ("fruit", r"水果|fruit"),
    ("veg", r"蔬|vegetable"),
    ("dairy", r"乳|蛋|dairy|egg|milk"),
))
OTHER = "other"
KINDS = tuple(kind for kind, _ in _KINDS) + (OTHER,)
KIND_TABLES = ("fridge_days", "room_temp_days", "unstated_days")

# The default shelf_life_days entries. An entry RULES.md adds is checked before them.
_DEFAULT_ITEMS = next(rule.default for rule in RULES if rule.key == "shelf_life_days")
_QUOTED = re.compile(r'^["“”](.+)["“”]$')


@dataclass(frozen=True)
class Expiry:
    """When a row expires, and how that was worked out."""
    on: Optional[date]       # None: neither a date in the note nor a 入庫日期 to count from
    estimated: bool = False  # True: 入庫日期 plus a shelf life from the rules, not a written date
    basis: str = ""          # how, in words, for explain mode

    def days_left(self, today: date) -> Optional[int]:
        return (self.on - today).days if self.on else None

    def explain(self) -> str:
        """In words, for explain mode: '入庫日期 2026-10-12 + 5 days (shelf_life_days: 莓 / …) = 2026-10-17'."""
        if self.on is None:
            return f"no expiry: {self.basis}"
        return f"{self.basis} = {self.on.isoformat()}" if self.estimated else f"{self.on.isoformat()}, {self.basis}"


def parse_date(text: str) -> Optional[date]:
    """The first YYYY-MM-DD date in `text`, or None."""
    m = _ISO_DATE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def written_expiry(note: str, added: Optional[date]) -> Optional[date]:
    """The expiry a note states: a date, or a shelf life counted from `added`."""
    stated = parse_date(note)
    if stated:
        return stated
    m = _DURATION.search(note or "")
    if m and added:
        return added + timedelta(days=int(m.group(1)) * _DAYS_PER[m.group(2)])
    return None


@lru_cache(maxsize=256)
def entry_pattern(entry: str) -> Optional["re.Pattern"]:
    """What a shelf_life_days entry matches: names split by '/', each anywhere in the
    item's name, case-blind; a name in quotes only as a whole word."""
    parts = []
    for name in entry.split("/"):
        name = name.strip()
        quoted = _QUOTED.match(name)
        if quoted:
            parts.append(rf"\b{re.escape(quoted.group(1).strip())}\b")
        elif name:
            parts.append(re.escape(name))
    return re.compile("|".join(parts), re.I) if parts else None


def section_kind(section: str) -> str:
    """The kind of a section heading, for the storage tables: seafood, meat, fruit, veg, dairy or other."""
    return next((kind for kind, pattern in _KINDS if pattern.search(section or "")), OTHER)


def estimate(section: str, name: str, note: str, rules: Mapping[str, Any]) -> Tuple[int, str]:
    """(days, the rule that gave them): a rough shelf life for a row whose note gives no date.

    In order: shelf_life_days entries RULES.md adds; sauce and snack sections;
    frozen; dried; the default shelf_life_days entries; then the storage note
    by the section's kind.
    """
    items = rules["shelf_life_days"]
    for entry, days in items.items():
        if entry not in _DEFAULT_ITEMS and _names(entry, name):
            return days, f"shelf_life_days: {entry}"
    if _LONG_SECTIONS.search(section or ""):
        return rules["sauces_snacks_days"], "sauces_snacks_days"
    lowered = f"{name} {note}".casefold()
    # Also 冷藏/冷凍: bulk meat and fish go in the freezer.
    if "冷凍" in lowered or "frozen" in lowered or "freez" in lowered:
        return rules["frozen_days"], "frozen_days"
    if "乾" in lowered or "dried" in lowered:
        return rules["dried_days"], "dried_days"
    for entry, days in items.items():
        if entry in _DEFAULT_ITEMS and _names(entry, name):
            return days, f"shelf_life_days: {entry}"
    kind = section_kind(section)
    note = note or ""
    if "陰涼" in note:
        return rules["cool_dark_days"], "cool_dark_days"
    if "冷藏" in note or "fridge" in note.casefold():
        return _by_kind(rules, "fridge_days", kind)
    if "常溫" in note:
        return _by_kind(rules, "room_temp_days", kind)
    return _by_kind(rules, "unstated_days", kind)


def expiry(section: str, name: str, note: str, added: Optional[date], rules: Mapping[str, Any]) -> Expiry:
    """When a row expires: the note's date or shelf life; else its 入庫日期 plus an
    estimate (marked estimated); else unknown."""
    written = written_expiry(note, added)
    if written:
        return Expiry(written, False, "written in the note")
    if added is None:
        return Expiry(None, False, "no 入庫日期 and no date in the note")
    days, rule = estimate(section, name, note, rules)
    return Expiry(added + timedelta(days=days), True, f"入庫日期 {added.isoformat()} + {days} days ({rule})")


def kind_table_problems(rules: Mapping[str, Any]) -> list:
    """Entries of the storage tables that name no section kind, so they never apply."""
    return [f"{key}: '{kind}' isn't a section kind ({', '.join(KINDS)}); ignored"
            for key in KIND_TABLES for kind in rules[key] if kind not in KINDS]


def with_expiry(note: str, expires: date) -> str:
    """`note` stating `expires`: its date replaced, or '保鮮至 YYYY-MM-DD' appended."""
    note = (note or "").strip()
    stamp = expires.isoformat()
    if _ISO_DATE.search(note):
        return _ISO_DATE.sub(stamp, note, count=1)
    return f"{note}，{EXPIRY_LABEL} {stamp}" if note else f"{EXPIRY_LABEL} {stamp}"


def _names(entry: str, name: str) -> bool:
    pattern = entry_pattern(entry)
    return bool(pattern and pattern.search(name or ""))


def _by_kind(rules: Mapping[str, Any], key: str, kind: str) -> Tuple[int, str]:
    table = rules[key]
    if kind in table:
        return table[kind], f"{key}: {kind}"
    return table[OTHER], f"{key}: {OTHER}"
