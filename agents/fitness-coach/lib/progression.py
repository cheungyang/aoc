"""Weight progression: progression.jsonl holds one line per exercise done.

    {"id":"bb_back_squat","last":"2026-10-13","load":135,"reps":6,"next_load":145}
    {"id":"bb_bench_press","last":"2026-10-13","load":100,"reps":6,"next_load":102.5,"next_reps":8}
    {"id":"pull_ups","last":"2026-10-13","reps":7,"next_reps":8}
    {"id":"plank","last":"2026-10-13","secs":45,"next_secs":50}

A weight_reps line also carries next_reps when the next load is lighter than
one full step up: the weight below a gap, the heaviest weight, or a load that
had to be snapped down.

The code computes; the coach decides (skills/coach_home_workout): which load
to take after a weight gap (`next_load`, snapped to a buildable weight here),
and whether to take the break discount `prescribe` shows.
"""
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence

from core.tooling import Report
from agents.fitness_coach.lib.equipment import (
    Equipment, implement_for, is_loadable, snap_down, snap_up, step_up,
)
from agents.fitness_coach.lib.exercise_files import dump_line, parse_json_line
from agents.fitness_coach.lib.exercise_library import is_lower_body, parse_rep_range

SECS_ROUNDING = 5  # timed holds are prescribed in whole 5 s
_EPS = 1e-6


def num(x: float):
    """185.0 -> 185, 82.5 -> 82.5, as the files write them."""
    x = round(float(x), 2)
    return int(x) if x.is_integer() else x


def parse_progression(lines: Sequence[str], source: str, report: Report) -> Dict[str, dict]:
    """id -> its line. The first line wins if an id appears twice (updates replace that one).

    A line that isn't a JSON object with an id comes back raw in `report`; a
    repeated id and an unreadable `last` date are named there too.
    """
    found: Dict[str, dict] = {}
    raw: List[str] = []
    repeated: List[str] = []
    for line in lines:
        obj = parse_json_line(line)
        if not obj or not isinstance(obj.get("id"), str) or not obj["id"]:
            raw.append(line)
            continue
        if obj["id"] in found:
            repeated.append(obj["id"])
            continue
        found[obj["id"]] = obj
        if _date(obj.get("last")) is None:
            report.problem(source, f"{obj['id']}: 'last' ({obj.get('last')!r}) isn't a date: "
                                   f"days since unknown, no break discount")
    report.unparsed(source, raw)
    for exercise_id in dict.fromkeys(repeated):
        report.problem(source, f"{exercise_id} has more than one line; the first one counts")
    return found


def _date(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


@dataclass
class Rx:
    """What to do per set: the load and a rep (or seconds) target, else the library range."""
    tracking: str
    rep_range: str
    load: Optional[float] = None
    reps: Optional[int] = None
    secs: Optional[int] = None
    new: bool = False
    days: Optional[int] = None          # since last done; None: never done, or no readable date
    deload: Optional["Rx"] = None       # after the break discount, once `days` passes deload_after_days
    deload_pct: Optional[float] = None

    def text(self, flags: bool = True) -> str:
        side = parse_rep_range(self.rep_range).side
        if self.tracking == "time":
            base = f"{self.secs}s{side}" if self.secs else self.rep_range
        else:
            base = f"{self.reps}{side}" if self.reps else self.rep_range
            if self.tracking == "weight_reps" and self.load is not None:
                base += f" @ {num(self.load)}"
        if flags and self.new:
            base += " (new)"
        elif flags and self.deload is not None:
            base += f" (-{self.deload_pct:g}%: {self.deload.text(flags=False)})"
        return base


def _weights(exercise: dict, equipment: Equipment) -> Sequence[float]:
    return equipment.loads.get(implement_for(exercise.get("equipment") or []), ())


def prescribe(exercise: dict, line: Optional[dict], today: date, equipment: Equipment,
              rules: Mapping[str, Any]) -> Rx:
    """Today's prescription from the exercise's progression line.

    No line: a calibration start (empty bar, lightest dial, empty sleeve or arms;
    the library range at an easy angle for bodyweight). Otherwise the line's next
    target, a load snapped down to a buildable weight. Last done more than
    `deload_after_days` ago: also the break discount, `deload_pct` % lighter
    (snapped down to a buildable weight) or fewer reps or seconds; the coach
    decides whether to take it.
    """
    tracking = exercise.get("tracking", "reps")
    rx = Rx(tracking=tracking, rep_range=exercise.get("rep_range", ""))
    weights = _weights(exercise, equipment)
    value = {"weight_reps": "load", "reps": "reps", "time": "secs"}.get(tracking, "reps")
    target = (line or {}).get(f"next_{value}", (line or {}).get(value))
    if not line or not isinstance(target, (int, float)):
        rx.new = True
        if tracking == "weight_reps" and weights:
            rx.load = weights[0]
        return rx

    last = _date(line.get("last"))
    rx.days = (today - last).days if last else None
    if tracking == "weight_reps":
        rx.load = (snap_down(weights, target) or weights[0]) if weights else target
        nxt_reps = line.get("next_reps")
        rx.reps = int(nxt_reps) if isinstance(nxt_reps, (int, float)) else None
    elif tracking == "time":
        rx.secs = int(target)
    else:
        rx.reps = int(target)
    if rx.days is not None and rx.days > rules["deload_after_days"]:
        rx.deload, rx.deload_pct = _discounted(rx, target, weights, rules["deload_pct"]), rules["deload_pct"]
    return rx


def _discounted(rx: Rx, target: float, weights: Sequence[float], pct: float) -> Rx:
    """`rx` after the break discount: `pct` % off the target; the reps go back to the range."""
    factor = (100 - pct) / 100
    after = Rx(tracking=rx.tracking, rep_range=rx.rep_range)
    if rx.tracking == "weight_reps":
        load = target * factor
        after.load = (snap_down(weights, load) or weights[0]) if weights else load
    elif rx.tracking == "time":
        after.secs = max(SECS_ROUNDING, int(int(target) * factor) // SECS_ROUNDING * SECS_ROUNDING)
    else:
        after.reps = max(1, math.floor(int(target) * factor))
    return after


def more_reps(reps: int, top: int, rules: Mapping[str, Any]) -> int:
    """More reps: + `rep_step` while that stays within the rep range (top), else + `rep_step_small`."""
    return reps + (rules["rep_step"] if reps + rules["rep_step"] <= top else rules["rep_step_small"])


@dataclass(frozen=True)
class Gap:
    """One step up from `start` can't be built: no weight loads between `below` and `above`."""
    start: float
    step_up: float
    below: float
    above: float
    top_reps: int

    def as_dict(self) -> dict:
        return {"from": num(self.start), "below": num(self.below), "above": num(self.above), "top_reps": self.top_reps}


class NeedsPick(ValueError):
    """Every set was completed and one step up falls in a gap: the coach picks the next load."""

    def __init__(self, gap: Gap):
        self.gap = gap
        super().__init__(f"one step up from {num(gap.start)} is {num(gap.step_up)}, which can't be built "
                         f"(nothing loads between {num(gap.below)} and {num(gap.above)})")


def gap_after(exercise: dict, load: float, equipment: Equipment, rules: Mapping[str, Any]) -> Optional[Gap]:
    """The gap one step up from `load` falls in, or None when that step can be built.

    Past the heaviest weight there is nothing to jump to, so that is no gap: the
    heaviest weight is taken, with more reps.
    """
    weights = _weights(exercise, equipment)
    if not weights:
        return None
    held = snap_down(weights, load) or weights[0]
    target = step_up(implement_for(exercise.get("equipment") or []), weights, held, is_lower_body(exercise), rules)
    if target is None or is_loadable(weights, target):
        return None
    above = snap_up(weights, target)
    if above is None:
        return None
    top = parse_rep_range(exercise.get("rep_range", "")).high
    return Gap(start=held, step_up=target, below=snap_down(weights, target), above=above, top_reps=top)


def next_load(exercise: dict, equipment: Equipment, load: float, reps: int, rules: Mapping[str, Any],
              pick: Optional[float] = None) -> tuple:
    """(next_load, next_reps or None) after every set was completed as planned.

    One step up (`step_up`), when it can be built. In a gap the coach picks the
    load (`pick`), snapped down to a buildable weight; without a pick, NeedsPick.
    At the heaviest weight (or past it), the heaviest one. Whenever the next load
    is lighter than one full step up, the reps go up (`more_reps`).
    """
    weights = _weights(exercise, equipment)
    top = parse_rep_range(exercise.get("rep_range", "")).high
    if not weights:
        return (pick if pick is not None else load + rules["plate_step"]), None
    held = snap_down(weights, load) or weights[0]
    target = step_up(implement_for(exercise.get("equipment") or []), weights, held, is_lower_body(exercise), rules)
    if pick is not None:
        nxt = snap_down(weights, pick) or weights[0]
    elif target is None:  # the heaviest dial
        nxt = held
    elif is_loadable(weights, target):
        nxt = target
    elif snap_up(weights, target) is None:  # past the heaviest weight: nothing to jump to
        nxt = snap_down(weights, target)
    else:
        raise NeedsPick(gap_after(exercise, held, equipment, rules))
    lighter = target is None or nxt < target - _EPS
    return nxt, (more_reps(reps, top, rules) if lighter else None)


def next_line(exercise: dict, sets: List[dict], today: date, equipment: Equipment, rules: Mapping[str, Any],
              cut_short: bool = False, pick: Optional[float] = None, report: Optional[Report] = None) -> dict:
    """The exercise's new progression line from the sets done.

    Every set ✅: one step up (`next_load`, or the coach's `pick` after a gap).
    Any 🔼/🔽, or cut short: the last set's load (reps, seconds) is both the
    result and the next target, unless the coach picked another load.
    """
    tracking = exercise.get("tracking", "reps")
    last = sets[-1]
    all_done = not cut_short and all(s.get("signal") == "completed" for s in sets)
    line = {"id": exercise["id"], "last": today.isoformat()}
    rep_range = parse_rep_range(exercise.get("rep_range", ""))
    if tracking == "weight_reps":
        load, reps = float(last["load"]), int(last["reps"])
        line.update(load=num(load), reps=reps)
        if all_done:
            nxt, nxt_reps = next_load(exercise, equipment, load, reps, rules, pick)
        else:
            weights = _weights(exercise, equipment)
            wanted = pick if pick is not None else load
            nxt = (snap_down(weights, wanted) or weights[0]) if weights else wanted
            nxt_reps = more_reps(reps, rep_range.high, rules) if nxt < load - _EPS else None
        line["next_load"] = num(nxt)
        if nxt_reps:
            line["next_reps"] = nxt_reps
        if report is not None:
            how = "every set ✅" if all_done else "held: a 🔼/🔽 or cut short"
            picked = f", your pick {num(pick)}" if pick is not None else ""
            more = f", reps {reps} → {nxt_reps}" if nxt_reps else ""
            report.trace(f"{exercise['id']} next load {num(nxt)} from {num(load)} × {reps} ({how}{picked}{more})")
    elif tracking == "time":
        secs = int(last["secs"])
        step = rules["secs_step"] if secs + rules["secs_step"] <= rep_range.high else rules["secs_step_small"]
        line.update(secs=secs, next_secs=secs + step if all_done else secs)
        if report is not None:
            report.trace(f"{exercise['id']} next {line['next_secs']}s from {secs}s "
                         f"({f'every set ✅: + {step}' if all_done else 'held'}; range tops at {rep_range.high}s)")
    else:
        reps = int(last["reps"])
        line.update(reps=reps, next_reps=more_reps(reps, rep_range.high, rules) if all_done else reps)
        if report is not None:
            report.trace(f"{exercise['id']} next {line['next_reps']} reps from {reps} "
                         f"({'every set ✅' if all_done else 'held'}; range tops at {rep_range.high})")
    return line


def merge_lines(lines: Sequence[str], updates: Dict[str, dict]) -> List[str]:
    """The file with each updated id's line replaced in place and new ids appended.

    Every other line is kept as it was, unparseable ones included.
    """
    out, replaced = [], set()
    for raw in lines:
        obj = parse_json_line(raw)
        exercise_id = obj.get("id") if obj else None
        if exercise_id in updates and exercise_id not in replaced:
            out.append(dump_line(updates[exercise_id]))
            replaced.add(exercise_id)
        else:
            out.append(raw)
    out.extend(dump_line(line) for exercise_id, line in updates.items() if exercise_id not in replaced)
    return out
