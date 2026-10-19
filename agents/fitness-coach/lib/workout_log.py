"""What log_workout_session writes: recovery, history and progression for one finished workout.

`plan_session` validates everything and computes every write without touching
a file, so a bad call writes nothing; `apply_session` then writes them.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.tooling import Report
from agents.fitness_coach.lib.exercise_files import (
    HISTORY_FILE, PROGRESSION_FILE, RECOVERY_FILE, append_lines, data_path, write_atomic,
)
from agents.fitness_coach.lib.exercise_library import parse_rep_range, resolve, suggest
from agents.fitness_coach.lib.muscle_recovery import after_session, current_recovery, recovery_record
from agents.fitness_coach.lib.progression import Gap, NeedsPick, merge_lines, next_line, num, prescribe
from agents.fitness_coach.lib.workout_data import WorkoutData
from agents.fitness_coach.lib.workout_history import SESSION_FORMAT, already_logged, history_line

FIELDS = {"weight_reps": ("load", "reps"), "reps": ("reps",), "time": ("secs",)}
# Safety limits on `start`: a session is logged the day it happens.
FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_AGE = timedelta(hours=24)
MAX_NOTES = 1000  # Google Health's limit for a session's notes


@dataclass
class SessionWrites:
    start: datetime
    end: datetime
    history_lines: List[str]
    progression_text: Optional[str]   # None: no line changes
    recovery: Optional[dict]          # None: nothing was done
    summary: List[dict]
    notes: str
    remarks: List[str] = field(default_factory=list)  # what the call changed: a trimmed end, a snapped pick

    def paths(self) -> List[str]:
        """The files `apply_session` writes, in order."""
        paths = [data_path(HISTORY_FILE)]
        if self.progression_text is not None:
            paths.append(data_path(PROGRESSION_FILE))
        if self.recovery is not None:
            paths.append(data_path(RECOVERY_FILE))
        return paths


def _sets_text(tracking: str, side: str, sets: List[dict]) -> str:
    """3×6 @ 185 · 6, 6, 5 @ 185 · 6 @ 175, 6 @ 185 · 3×10/leg · 45s, 40s."""
    if tracking == "time":
        values = [f"{int(s['secs'])}s{side}" for s in sets]
    else:
        values = [f"{int(s['reps'])}{side}" for s in sets]
    loads = [num(s["load"]) for s in sets] if tracking == "weight_reps" else []
    if loads and len(set(loads)) > 1:
        return ", ".join(f"{v} @ {w}" for v, w in zip(values, loads))
    reps = f"{len(values)}×{values[0]}" if len(set(values)) == 1 and len(values) > 1 else ", ".join(values)
    return f"{reps} @ {loads[0]}" if loads else reps


def _check_sets(exercise: dict, sets: List[dict]) -> Optional[str]:
    tracking = exercise.get("tracking", "reps")
    needed = FIELDS.get(tracking, ("reps",))
    for i, s in enumerate(sets, 1):
        missing = [f for f in needed if s.get(f) is None]
        if missing:
            return (f"{exercise['id']} set {i}: needs {' and '.join(needed)} "
                    f"({tracking.replace('_', ' + ')} tracking); missing {', '.join(missing)}.")
    return None


def pick_needed(exercise: dict, gap: Gap) -> str:
    """The error for a gap without the coach's next_load: the facts to pick from."""
    return (f"{exercise['id']}: every set was ✅ at {num(gap.start)}, and one step up ({num(gap.step_up)}) "
            f"can't be built. Pass next_load for it: {num(gap.below)}, the weight below the gap (reps go up), "
            f"or {num(gap.above)}, the weight above it; the rep range tops at {gap.top_reps}.")


def plan_session(data: WorkoutData, results: List[dict], start: datetime, duration_minutes: int,
                 now: datetime, rules: Mapping[str, Any], report: Report) -> Tuple[Optional[SessionWrites], List[str]]:
    """Every write for the session, or the errors that stop all of them."""
    errors: List[str] = []
    if start > now + FUTURE_TOLERANCE:
        errors.append(f"start {start.strftime(SESSION_FORMAT)} is in the future.")
    elif now - start > MAX_AGE:
        errors.append(f"start {start.strftime(SESSION_FORMAT)} is more than 24 hours ago; pass the session's start.")
    if duration_minutes < 1:
        errors.append("duration_minutes must be at least 1.")

    entries, seen = [], set()
    for result in results:
        exercise = resolve(data.library, result.get("id", ""))
        if exercise is None:
            hint = suggest(data.library, result.get("id", ""))
            errors.append(f"Unknown exercise '{result.get('id')}'." + (f" Did you mean: {', '.join(hint)}?" if hint else ""))
            continue
        if exercise["id"] in seen:
            errors.append(f"{exercise['id']} is listed twice; list each exercise once with all its sets.")
            continue
        seen.add(exercise["id"])
        sets = result.get("sets") or []
        problem = _check_sets(exercise, sets)
        if problem:
            errors.append(problem)
        pick = result.get("next_load")
        if pick is not None and exercise.get("tracking") != "weight_reps":
            errors.append(f"{exercise['id']}: next_load is only for loaded lifts.")
        entries.append((exercise, sets, bool(result.get("skipped")) or not sets, pick))
    if not results:
        errors.append("exercises is empty.")
    twice = already_logged(data.history, seen, start)
    if twice:
        errors.append(f"Already logged for this session: {', '.join(twice)}. Nothing was written again.")
    if errors:
        return None, errors

    remarks = []
    end = start + timedelta(minutes=duration_minutes)
    if end > now:
        end = max(now, start + timedelta(minutes=1))
        remarks.append(f"end trimmed to now ({end.strftime('%H:%M')}): start + duration was in the future.")

    today = start.date()
    updates: Dict[str, dict] = {}
    done, summary, notes = [], [], []
    for exercise, sets, skipped, pick in entries:
        side = parse_rep_range(exercise.get("rep_range", "")).side
        name = exercise.get("name", exercise["id"])
        if not sets:
            summary.append({"name": name, "skipped": True})
            continue
        try:
            line = next_line(exercise, sets, today, data.equipment, rules, cut_short=skipped, pick=pick, report=report)
        except NeedsPick as gap:
            errors.append(pick_needed(exercise, gap.gap))
            continue
        if pick is not None and abs(float(line["next_load"]) - float(pick)) > 1e-6:
            which = "the weight below it" if line["next_load"] < pick else "the lightest weight"
            remarks.append(f"{exercise['id']}: next_load {num(pick)} can't be built; {line['next_load']}, {which}.")
        updates[exercise["id"]] = line
        done.append(exercise)
        did = _sets_text(exercise.get("tracking", "reps"), side, sets)
        nxt = prescribe(exercise, line, today, data.equipment, rules).text(flags=False)
        summary.append({"name": name, "done": did + (" (cut short)" if skipped else ""), "next": nxt})
        notes.append(f"{name} {did}")
    if errors:
        return None, errors

    skipped_names = [s["name"] for s in summary if s.get("skipped")]
    note_text = "; ".join(notes) + (f"; skipped: {', '.join(skipped_names)}" if skipped_names else "")
    recovery = None
    if done:
        values = after_session(current_recovery(data.recovery, now, rules, report), done, rules, report)
        recovery = recovery_record(data.recovery, values, now)
    progression = "\n".join(merge_lines(data.progression_lines, updates)) + "\n" if updates else None
    return SessionWrites(
        start=start,
        end=end,
        history_lines=[history_line(start, e, sets, skipped) for e, sets, skipped, _ in entries],
        progression_text=progression,
        recovery=recovery,
        summary=summary,
        notes=f"Home workout: {note_text}"[:MAX_NOTES],
        remarks=remarks,
    ), []


def apply_session(writes: SessionWrites, written: List[str]) -> None:
    """Writes history, then progression, then recovery, naming each in `written` once done."""
    append_lines(data_path(HISTORY_FILE), writes.history_lines)
    written.append(f"history (+{len(writes.history_lines)} lines)")
    if writes.progression_text is not None:
        write_atomic(data_path(PROGRESSION_FILE), writes.progression_text)
        written.append("progression")
    if writes.recovery is not None:
        write_atomic(data_path(RECOVERY_FILE), json.dumps(writes.recovery, indent=2) + "\n")
        written.append("recovery")
