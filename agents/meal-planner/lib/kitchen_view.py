"""The planning view of the kitchen: what to use first, what else is there, and
what the family has eaten lately. kitchen_snapshot returns it in one read.
"""
import json
from dataclasses import dataclass, field
from datetime import date
from operator import itemgetter
from typing import Any, Dict, List, Mapping

from agents.meal_planner.lib.inventory_doc import InventoryDoc
from agents.meal_planner.lib.meal_log import last_ideas, recent_meals
from agents.meal_planner.lib.shelf_life import expiry, parse_date


@dataclass
class Snapshot:
    data: dict                                        # what kitchen_snapshot returns
    undated: List[str] = field(default_factory=list)  # rows with no 入庫日期 and no date in the note, as written
    traces: List[str] = field(default_factory=list)   # how each row's days left was reached, for explain mode


def bucket(days_left: int, rules: Mapping[str, Any]) -> str:
    """The list a row with `days_left` goes in: expired, expiring_soon, perishables or long_life."""
    if days_left > rules["perishable_days"]:
        return "long_life"
    if days_left < 0:
        return "expired"
    return "expiring_soon" if days_left <= rules["expiring_soon_days"] else "perishables"


def build_snapshot(doc: InventoryDoc, meal_logs: str, staples: str, today: date, rules: Mapping[str, Any]) -> Snapshot:
    """The snapshot from the inventory and the other two files' text. `rules` are the RULES.md values."""
    lists: Dict[str, list] = {"expired": [], "expiring_soon": [], "perishables": []}
    long_life: Dict[str, List[str]] = {}
    undated, traces = [], []
    for row in doc.rows():
        found = expiry(row.section, row.name, row.note, parse_date(row.added), rules)
        if found.on is None:
            undated.append((row.original or "").rstrip("\r\n"))
            continue
        left = found.days_left(today)
        traces.append(f"{row.name}: {found.explain()}; days_left {left}")
        kind = bucket(left, rules)
        if kind == "long_life":
            long_life.setdefault(row.section or "-", []).append(" ".join(f"{row.name} {row.qty}".split()))
            continue
        entry = {"item": row.name, "qty": row.qty, "days_left": left}
        if found.estimated:
            entry["est"] = True
        lists[kind].append(entry)
    by_days = itemgetter("days_left")  # sorted() is stable: ties keep file order
    days = rules["recent_meal_days"]
    data = {
        "today": today.isoformat(),
        "expired": sorted(lists["expired"], key=by_days),
        "expiring_soon": sorted(lists["expiring_soon"], key=by_days),
        "perishables": sorted(lists["perishables"], key=by_days),
        "long_life": long_life,
        "staples": staples_list(staples),
        "recent_meals": recent_meals(meal_logs, today, days),
    }
    ideas = last_ideas(meal_logs, today, days)
    if ideas:
        data["last_ideas"] = ideas
    return Snapshot(data, undated, traces)


def staples_list(text: str) -> List[str]:
    """The '- item' lines of STAPLES.md."""
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped[:1] in ("-", "*") and stripped[1:].strip():
            items.append(stripped[1:].strip())
    return items


def staples_unrecognized(text: str) -> List[str]:
    """STAPLES.md lines that are neither an item, a heading, a note nor blank, as written."""
    return [line for line in text.splitlines()
            if line.strip() and line.strip()[:1] not in ("-", "*", "#", ">")]


def to_json(snapshot: dict) -> str:
    """JSON with one line per row: compact for the model, still readable in logs."""
    def dump(value):
        return json.dumps(value, ensure_ascii=False)

    parts = []
    for key, value in snapshot.items():
        if isinstance(value, list) and value:
            parts.append(f" {dump(key)}: [\n  " + ",\n  ".join(dump(v) for v in value) + "]")
        elif isinstance(value, dict) and value and all(isinstance(v, list) for v in value.values()):
            parts.append(f" {dump(key)}: {{\n  " + ",\n  ".join(f"{dump(k)}: {dump(v)}" for k, v in value.items()) + "}")
        else:
            parts.append(f" {dump(key)}: {dump(value)}")
    return "{\n" + ",\n".join(parts) + "\n}"
