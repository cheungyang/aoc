"""Muscle recovery: muscle_recovery.json holds a % per group and when it was written.

A group recovers `recovery_per_hour` points an hour up to 100; at 100 it is
eligible for a main or accessory lift. A session sets the primaries of its main
and accessory lifts to `worked_recovery` and takes `secondary_hit` off (floor 0)
every group it hit only as a secondary or with a finisher. The numbers come from
RULES.md (lib/rules.py).
"""
import math
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

from core.tooling import Report
from agents.fitness_coach.lib.exercise_library import GROUPS

FULL = 100


def _parse_time(value, tz) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


def check_record(record: Any, source: str, report: Report) -> Optional[dict]:
    """The file's object, or None when it isn't one. What can't be read goes in `report`.

    A missing group counts as recovered, as on a first run. A group whose value
    isn't a number counts as recovered too, and is named; so is a missing or
    unreadable `updated`, which leaves the values as stored. Other keys come back
    raw (the tool keeps them in the file).
    """
    if record is None:
        return None
    if not isinstance(record, dict):
        report.problem(source, "not a JSON object: every group counts as recovered")
        return None
    if _parse_time(record.get("updated"), None) is None:
        report.problem(source, f"'updated' ({record.get('updated')!r}) isn't a time: "
                               f"the values are taken as stored, without recovery since")
    for group in GROUPS:
        value = record.get(group, FULL)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            report.problem(source, f"{group}: {value!r} isn't a number: counted as recovered")
    extra = [f'"{key}": {value!r}' for key, value in record.items() if key != "updated" and key not in GROUPS]
    report.unparsed(source, extra)
    return record


def _stored(record: dict, group: str) -> float:
    value = record.get(group, FULL)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else FULL


def current_recovery(record: Optional[dict], now: datetime, rules: Mapping[str, Any],
                     report: Optional[Report] = None) -> Dict[str, int]:
    """Each group's % now: min(100, stored + `recovery_per_hour` × hours since `updated`), rounded down.

    A group missing from the file counts as recovered; without a readable
    `updated` the stored values are taken as they are.
    """
    record = record or {}
    per_hour = rules["recovery_per_hour"]
    updated = _parse_time(record.get("updated"), now.tzinfo)
    hours = max(0.0, (now - updated).total_seconds() / 3600) if updated else 0.0
    values = {}
    for group in GROUPS:
        stored = _stored(record, group)
        values[group] = int(math.floor(min(FULL, stored + per_hour * hours)))
        if report is not None and stored < FULL:
            report.trace(f"{group} recovery {values[group]} = min(100, {stored:g} + {per_hour:g}/h × {hours:.1f} h)")
    return values


def eligible_groups(values: Dict[str, int]) -> List[str]:
    return [g for g in GROUPS if values.get(g, FULL) >= FULL]


def next_ready(values: Dict[str, int], now: datetime, rules: Mapping[str, Any]) -> Optional[tuple]:
    """(group, time) of the group that reaches 100 first, or None if one already has."""
    if eligible_groups(values):
        return None
    group = max(GROUPS, key=lambda g: values[g])
    return group, now + timedelta(hours=(FULL - values[group]) / rules["recovery_per_hour"])


def after_session(values: Dict[str, int], done: Iterable[dict], rules: Mapping[str, Any],
                  report: Optional[Report] = None) -> Dict[str, int]:
    """Recovery after the exercises in `done` (library entries actually performed)."""
    done = list(done)
    worked = {e["primary_muscle"] for e in done if e.get("role") in ("main", "accessory")}
    hit = set()
    for exercise in done:
        hit.update(exercise.get("secondary_muscles") or [])
        if exercise.get("role") == "finisher":
            hit.add(exercise["primary_muscle"])
    result = dict(values)
    for group in worked & set(result):
        result[group] = min(result[group], rules["worked_recovery"])
    for group in (hit - worked) & set(result):
        result[group] = max(0, result[group] - rules["secondary_hit"])
    if report is not None:
        for group in GROUPS:
            if group in result and result[group] != values.get(group):
                how = "worked as a primary" if group in worked else "hit as a secondary or by a finisher"
                report.trace(f"{group} recovery {values.get(group)} → {result[group]} ({how})")
    return result


def recovery_record(old: Optional[dict], values: Dict[str, int], now: datetime) -> dict:
    """The file's new content: `updated` first, groups in the file's order, other keys kept."""
    old = old or {}
    record = {"updated": now.isoformat(timespec="seconds")}
    for key, value in old.items():
        if key != "updated":
            record[key] = values[key] if key in values else value
    for group in GROUPS:
        record.setdefault(group, values[group])
    return record
