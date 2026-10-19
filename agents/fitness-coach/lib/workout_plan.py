"""What workout_setup returns: the Setup Phase snapshot, and serving details for the picks.

The snapshot is sized for a voice turn: candidates are capped and ranked so the
model picks from a short, station-diverse list instead of the whole library.
The caps and windows come from RULES.md (lib/rules.py).
"""
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from core.tooling import Report
from agents.fitness_coach.lib.equipment import implement_for, ladder, station_for
from agents.fitness_coach.lib.exercise_library import GROUPS, is_lower_body, resolve, suggest
from agents.fitness_coach.lib.muscle_recovery import current_recovery, eligible_groups, next_ready
from agents.fitness_coach.lib.progression import Rx, gap_after, num, prescribe
from agents.fitness_coach.lib.workout_data import WorkoutData
from agents.fitness_coach.lib.workout_history import SESSION_FORMAT, recent_mains, recently_done

ROW_COLUMNS = "id | name | group | station | level | days | rx"


def last_night_hours(sleep: Optional[dict], today: date) -> Optional[float]:
    """Hours asleep in the sessions that ended today (naps flagged as such left out)."""
    sessions = [
        s for s in (sleep or {}).get("sessions") or []
        if s.get("wake_date") == today.isoformat() and s.get("main_sleep") is not False
    ]
    if not sessions:
        return None
    return round(sum(s.get("minutes_asleep") or 0 for s in sessions) / 60, 1)


def _station(data: WorkoutData, exercise: dict) -> str:
    return station_for(exercise.get("equipment") or [], data.equipment.stations)


def _prescribe(data: WorkoutData, exercise: dict, today: date, rules: Mapping[str, Any], report: Report) -> Rx:
    line = data.progression.get(exercise["id"])
    rx = prescribe(exercise, line, today, data.equipment, rules)
    if report.explain:
        if rx.new:
            start = f", at the lightest buildable weight, {num(rx.load)}" if rx.load is not None else ""
            report.trace(f"{exercise['id']} rx {rx.text()}: never done, a calibration start{start}")
        else:
            since = f"{rx.days} d ago" if rx.days is not None else "date unknown"
            discount = (f"; over {rules['deload_after_days']} d, -{rules['deload_pct']:g}% gives {rx.deload.text(flags=False)}"
                        if rx.deload is not None else "")
            report.trace(f"{exercise['id']} rx {rx.text(flags=False)}: progression.jsonl's next target "
                         f"(last done {line.get('last')}, {since}){discount}")
    return rx


def _row(data: WorkoutData, exercise: dict, today: date, rules: Mapping[str, Any], report: Report) -> str:
    rx = _prescribe(data, exercise, today, rules, report)
    return " | ".join([
        exercise["id"], exercise.get("name", ""), exercise.get("primary_muscle", ""),
        _station(data, exercise), exercise.get("level", ""), "-" if rx.days is None else str(rx.days), rx.text(),
    ])


def _new_advanced(data: WorkoutData, exercise: dict) -> bool:
    """An advanced exercise not yet in the user's progression: last in rank, never a station pick."""
    return exercise.get("level") == "advanced" and exercise["id"] not in data.progression


def _diverse(data: WorkoutData, pool: Sequence[dict], k: int, stations: Sequence[str]) -> List[dict]:
    """Up to k from a ranked pool: the best per station in preference order, then by rank."""
    chosen: List[dict] = []
    for station in stations:
        if len(chosen) >= k:
            break
        best = next((e for e in pool if e not in chosen and _station(data, e) == station
                     and not _new_advanced(data, e)), None)
        if best:
            chosen.append(best)
    chosen += [e for e in pool if e not in chosen][: max(0, k - len(chosen))]
    return sorted(chosen, key=list(pool).index)


def _mains(data: WorkoutData, eligible: Sequence[str], recent: Sequence[str], rules: Mapping[str, Any]) -> List[dict]:
    """Eligible mains: the recent ones first, then the best of each group in turn."""
    pool = [e for e in data.library.values() if e.get("role") == "main" and e.get("primary_muscle") in eligible]
    rank = lambda e: (_new_advanced(data, e), e["_order"])  # noqa: E731
    first = [data.library[i] for i in recent if data.library.get(i) in pool]
    by_group = {g: sorted((e for e in pool if e["primary_muscle"] == g and e not in first), key=rank) for g in GROUPS}
    rest = []
    while any(by_group.values()):
        for group in GROUPS:
            if by_group[group]:
                rest.append(by_group[group].pop(0))
    return (first + rest)[:rules["max_mains"]]


def candidates(data: WorkoutData, eligible: Sequence[str], recent: Sequence[str], today: date,
               rules: Mapping[str, Any]) -> Dict[str, List[dict]]:
    """main / accessory / finisher candidates for the eligible groups, best first.

    Rank: not done in the last `rotate_days`, then advanced ones not yet started
    last, then library order (finishers: Core first). Accessories (per group)
    and finishers spread over stations, the main lifts' stations and Floor
    first; exercises that need no equipment fit any station and compete on rank
    alone. At most `max_mains` mains and `max_finishers` finishers; the
    `accessory_budget` is split across the groups, between
    `accessories_per_group_min` and `accessories_per_group_max` each.
    """
    rotated = recently_done(data.history, today, rules["rotate_days"])
    rank = lambda e: (e["id"] in rotated, _new_advanced(data, e), e["_order"])  # noqa: E731
    mains = _mains(data, eligible, recent, rules)
    preferred = list(dict.fromkeys([_station(data, e) for e in mains] + ["Floor"] + list(data.equipment.stations)))

    pools = {g: sorted((e for e in data.library.values()
                        if e.get("role") == "accessory" and e.get("primary_muscle") == g), key=rank) for g in eligible}
    pools = {g: pool for g, pool in pools.items() if pool}
    k = accessories_per_group(len(pools), rules)
    accessories = [e for pool in pools.values() for e in _diverse(data, pool, k, preferred)]
    finishers = sorted((e for e in data.library.values()
                        if e.get("role") == "finisher" and e.get("primary_muscle") in eligible),
                       key=lambda e: (rank(e)[:2], e.get("primary_muscle") != "Core", e["_order"]))
    return {"main": mains, "accessory": accessories,
            "finisher": _diverse(data, finishers, rules["max_finishers"], preferred)}


def accessories_per_group(groups: int, rules: Mapping[str, Any]) -> int:
    """Accessory candidates per group: the budget split evenly, within the per-group bounds."""
    split = rules["accessory_budget"] // max(1, groups)
    return min(rules["accessories_per_group_max"], max(rules["accessories_per_group_min"], split))


def build_setup(data: WorkoutData, now: datetime, sleep_hours: Optional[float], sleep_note: Optional[str],
                rules: Mapping[str, Any], report: Report) -> dict:
    today = now.date()
    values = current_recovery(data.recovery, now, rules, report)
    eligible = eligible_groups(values)
    recent = recent_mains(data.history, data.library, today, rules)

    payload: dict = {"start": now.strftime(SESSION_FORMAT), "sleep_h": sleep_hours}
    if sleep_hours is not None:
        payload["short_sleep"] = sleep_hours < rules["short_sleep_hours"]
    else:
        payload["sleep_note"] = sleep_note or "unknown"
    payload["eligible"] = eligible
    recovering = {g: v for g, v in values.items() if g not in eligible}
    if recovering:
        payload["recovering"] = recovering
    if recent:
        payload["recent_mains"] = [f"{r['id']} since {r['since']}" for r in recent]
    ready = next_ready(values, now, rules)
    if ready:
        payload["note"] = f"No group is fully recovered; {ready[0]} is next, at {ready[1].strftime('%a %H:%M')}."
        return payload

    payload["rows"] = ROW_COLUMNS
    for role, exercises in candidates(data, eligible, [r["id"] for r in recent], today, rules).items():
        payload[role] = [_row(data, e, today, rules, report) for e in exercises]
    return payload


def build_details(data: WorkoutData, refs: Sequence[str], now: datetime, rules: Mapping[str, Any],
                  report: Report) -> dict:
    """Serving details for the picked exercises: rx, cue, media, the 🔼/🔽 loads and any gap.

    `loads` runs one step apart from two steps below the start to two above;
    with a break discount, from two below the discounted load to two above the
    target, so either start works. `gap` names each start whose next step up
    can't be built, for the coach's pick at wrap-up.
    """
    today, rows = now.date(), []
    for ref in refs:
        exercise = resolve(data.library, ref)
        if exercise is None:
            hint = suggest(data.library, ref)
            report.problem("ids", f"unknown exercise '{ref}'" + (f"; did you mean: {', '.join(hint)}?" if hint else ""))
            continue
        rx = _prescribe(data, exercise, today, rules, report)
        row = {"id": exercise["id"], "name": exercise.get("name"), "rx": rx.text(),
               "cue": exercise.get("cue"), "media": exercise.get("media")}
        implement = implement_for(exercise.get("equipment") or [])
        weights = data.equipment.loads.get(implement)
        if exercise.get("tracking") == "weight_reps" and rx.load is not None and weights:
            starts = sorted({rx.load} | ({rx.deload.load} if rx.deload is not None and rx.deload.load is not None else set()))
            rungs = ladder(implement, weights, starts[0], is_lower_body(exercise), rules,
                           down=0 if rx.new else 2, through=starts[-1] if len(starts) > 1 else None)
            row["loads"] = [num(w) for w in rungs]
            gaps = [gap.as_dict() for gap in (gap_after(exercise, s, data.equipment, rules) for s in starts) if gap]
            if gaps:
                row["gap"] = gaps
            if report.explain:
                body = "lower body" if is_lower_body(exercise) else "upper body"
                report.trace(f"{exercise['id']} loads: {implement}, {body}, one step apart from "
                             f"{num(starts[0])}" + (f" through {num(starts[-1])}" if len(starts) > 1 else ""))
        rows.append({k: v for k, v in row.items() if v not in (None, "")})
    if refs and not rows:
        report.missing("ids", "none of the ids is in the library")
    return {"start": now.strftime(SESSION_FORMAT), "exercises": rows}
