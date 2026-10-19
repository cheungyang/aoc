"""Workout history: workout_history.jsonl, one line per exercise per session, never rewritten.

    {"date":"2026-10-13","session":"2026-10-13T07:30","id":"bb_back_squat",
     "sets":[{"load":175,"reps":6,"signal":"up"},{"load":185,"reps":6,"signal":"completed"}]}

`session` is the local start time, the same on every line of a workout. Timed
sets have `secs` instead of `reps`; bodyweight sets have no `load`. A skipped
exercise has "skipped": true and no sets.
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set

from core.tooling import Report
from agents.fitness_coach.lib.exercise_files import dump_line, parse_json_line
from agents.fitness_coach.lib.progression import num

DUPLICATE_WINDOW_MINUTES = 90  # the same exercise in a session this close is already logged
SESSION_FORMAT = "%Y-%m-%dT%H:%M"


def parse_history(lines: Sequence[str], source: str, report: Report) -> List[dict]:
    """The readable lines, each with a parsed `_date`.

    A line that isn't a JSON object with an id and a date comes back raw in `report`.
    """
    entries, raw = [], []
    for line in lines:
        obj = parse_json_line(line)
        if not obj or not obj.get("id"):
            raw.append(line)
            continue
        try:
            obj["_date"] = date.fromisoformat(str(obj.get("date") or str(obj.get("session", ""))[:10]))
        except ValueError:
            raw.append(line)
            continue
        entries.append(obj)
    report.unparsed(source, raw)
    return entries


def _done(entry: dict) -> bool:
    return not entry.get("skipped") and bool(entry.get("sets"))


def recent_mains(entries: Iterable[dict], library: Dict[str, dict], today: date,
                 rules: Mapping[str, Any]) -> List[dict]:
    """Main lifts done in the last `main_lookback_days`: [{"id", "since"}], most recent first.

    `since` is when the lift's current run began: its uses, walked back from the
    latest, until a pause of more than `main_run_gap_days`.
    """
    uses: Dict[str, Set[date]] = {}
    for entry in entries:
        if _done(entry) and library.get(entry["id"], {}).get("role") == "main":
            uses.setdefault(entry["id"], set()).add(entry["_date"])
    recent = []
    for exercise_id, dates in uses.items():
        ordered = sorted(dates)
        if (today - ordered[-1]).days > rules["main_lookback_days"]:
            continue
        since = ordered[-1]
        for d in reversed(ordered[:-1]):
            if (since - d).days > rules["main_run_gap_days"]:
                break
            since = d
        recent.append({"id": exercise_id, "since": since, "last": ordered[-1]})
    recent.sort(key=lambda r: r["last"], reverse=True)
    return [{"id": r["id"], "since": r["since"].isoformat()} for r in recent]


def recently_done(entries: Iterable[dict], today: date, days: int) -> Set[str]:
    """Ids done (not skipped) within the last `days` days."""
    return {e["id"] for e in entries if _done(e) and (today - e["_date"]).days <= days}


def already_logged(entries: Iterable[dict], ids: Iterable[str], start: datetime,
                   window_minutes: int = DUPLICATE_WINDOW_MINUTES) -> List[str]:
    """Ids this session would log twice: the same exercise in a session starting within the window."""
    ids, found = set(ids), []
    for entry in entries:
        try:
            when = datetime.strptime(str(entry.get("session")), SESSION_FORMAT)
        except ValueError:
            continue
        if entry["id"] in ids and abs(when - start.replace(tzinfo=None)) <= timedelta(minutes=window_minutes):
            found.append(entry["id"])
    return sorted(set(found))


def set_record(tracking: str, s: dict) -> dict:
    """One set as the file writes it: load (loaded lifts), reps or secs, then the button."""
    record = {}
    if tracking == "weight_reps":
        record["load"] = num(s["load"])
    if tracking == "time":
        record["secs"] = int(s["secs"])
    else:
        record["reps"] = int(s["reps"])
    record["signal"] = s["signal"]
    return record


def history_line(start: datetime, exercise: dict, sets: List[dict], skipped: bool) -> str:
    line = {"date": start.date().isoformat(), "session": start.strftime(SESSION_FORMAT), "id": exercise["id"]}
    if sets:
        line["sets"] = [set_record(exercise.get("tracking", "reps"), s) for s in sets]
    if skipped:
        line["skipped"] = True
    return dump_line(line)
