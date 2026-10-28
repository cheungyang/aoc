"""log_workout_session: a finished workout's history, recovery, next targets and Google Health session, in one call."""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.tooling import (
    Report, ToolError, agent_tool, current_agent_id, display_path, get_local_now, get_local_timezone,
    has_permission, load_rules,
)
from agents.fitness_coach.lib.rules import RULES
from agents.fitness_coach.lib.workout_data import load_data
from agents.fitness_coach.lib.workout_log import apply_session, plan_session
from agents.fitness_coach.lib.workout_history import SESSION_FORMAT

# Every action writes, so the read-only voice replay refuses them all.
READ_ACTIONS = ()


class SetResult(BaseModel):
    """One set, as done."""
    model_config = ConfigDict(extra="forbid")

    load: Optional[float] = Field(None, description=(
        "lb used, loaded lifts only: barbell total incl. the bar, dumbbell per hand, "
        "landmine sleeve plates, lever-arm plates per arm."))
    reps: Optional[int] = Field(None, description="Reps done (per side for /leg, /side). Not for timed holds.")
    secs: Optional[int] = Field(None, description="Seconds held, timed holds only.")
    signal: Literal["completed", "up", "down"] = Field(
        description="The button tapped after the set: ✅ completed, 🔼 up, 🔽 down.")


class ExerciseResult(BaseModel):
    """One exercise of the session."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Library id from workout_setup (or the exact library name).")
    sets: List[SetResult] = Field(default_factory=list, description="Sets in the order done.")
    skipped: bool = Field(False, description="True if ⏭️ skipped (with no sets if skipped before the first set).")
    next_load: Optional[float] = Field(None, description=(
        "Loaded lifts with a workout_setup `gap`: next session's load, picked by your gap rule. "
        "Snapped down to a buildable weight."))


def _now():
    """Local time now. A function so tests can pin it."""
    return get_local_now()


def _parse_start(value: str, now: datetime) -> datetime:
    """ISO date-time (naive = local) or HH:MM today."""
    text = (value or "").strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.combine(now.date(), datetime.strptime(text, "%H:%M").time())
    return parsed.astimezone(get_local_timezone()) if parsed.tzinfo else parsed.replace(tzinfo=get_local_timezone())


def _log_google_health(writes) -> str:
    """One STRENGTH_TRAINING session through google_health's guarded write path."""
    if not has_permission("log_workout"):
        return "not logged: no permission"
    # The one call outside core.tooling: google_health's write path takes the
    # ExecutionContext and a ToolsLoader.
    from core.loaders.tools_loader import ToolsLoader
    from core.runtime.foundation.context import try_context
    from tools.google_health import (
        GoogleHealthClient, GoogleHealthError, GuardRejection, InstructionError, log_workout,
    )
    instruction = {
        "action": "log_workout",
        "exercise_type": "STRENGTH_TRAINING",
        "start_time": writes.start.isoformat(timespec="minutes"),
        "end_time": writes.end.isoformat(timespec="minutes"),
        "notes": writes.notes,
    }
    clients = {}

    def get_client():
        clients.setdefault("client", GoogleHealthClient())
        return clients["client"]

    try:
        log_workout(instruction, get_client, try_context(), ToolsLoader())
    except (GuardRejection, InstructionError, GoogleHealthError) as e:
        return f"not logged: {e}"
    except Exception as e:  # noqa: BLE001 - reported, the files are already saved
        return f"not logged: {type(e).__name__}: {e}"
    return f"logged {writes.start.strftime('%H:%M')}-{writes.end.strftime('%H:%M')}"


@agent_tool
def log_workout_session(report: Report, exercises: List[ExerciseResult], start: str, duration_minutes: int) -> dict:
    """Logs a finished workout in one call: muscle recovery, workout history, the next targets
    (progression) and a Google Health strength-training session.

    Reads: pkm/wallet/exercise/, whose history, progression and recovery files it writes; RULES.md.
    Computes: recovery after the session; next targets (one step up when every set was ✅, else the last set held); your next_load, snapped down to a buildable weight.
    Leaves out: the load after a weight gap: pass next_load (coach_home_workout).

    Pass every exercise of the session in the order done, each with all its sets and the button
    tapped after each set. Per set: load and reps for loaded lifts, reps only for bodyweight,
    secs only for timed holds. An exercise skipped before its first set: skipped=true, no sets.

    Returns per exercise the sets done and the next target, plus what was saved and whether
    Google Health logged it. Nothing is written when an exercise is unknown, a set misses a field,
    a gap needs your next_load, or the session is already logged; the errors say which. If Google
    Health fails, the files are still saved and the failure is reported.

    Args:
        exercises: The session's exercises, in order.
        start: The session start workout_setup returned (YYYY-MM-DDTHH:MM, local).
        duration_minutes: Minutes from start to the last set.
    """
    rules = load_rules("fitness-coach", RULES, report)
    now = _now()
    try:
        session_start = _parse_start(start, now)
    except ValueError:
        raise ToolError(f"start must be YYYY-MM-DDTHH:MM (local), got {start!r}.") from None
    results = [
        (e if isinstance(e, ExerciseResult) else ExerciseResult.model_validate(e)).model_dump(exclude_none=True)
        for e in exercises
    ]
    data = load_data(report)
    report.raise_if_failed()  # no library: failed, the issues say why
    writes, errors = plan_session(data, results, session_start, int(duration_minutes), now, rules, report)
    if errors:
        raise ToolError("Nothing was written.\n" + "\n".join(errors))
    for path in writes.paths():
        if not has_permission("write", path):
            raise ToolError(f"Agent '{current_agent_id()}' may not write {display_path(path)}; nothing was written.")

    written: List[str] = []
    try:
        apply_session(writes, written)
    except Exception as e:
        done = ", ".join(written) or "nothing"
        raise ToolError(f"Writing the workout files failed after saving {done}: {type(e).__name__}: {e}") from None

    did_something = any("done" in row for row in writes.summary)
    google = _log_google_health(writes) if did_something else "not logged: every exercise was skipped"
    payload = {
        "session": f"{writes.start.strftime(SESSION_FORMAT)}, {round((writes.end - writes.start).total_seconds() / 60)} min",
        "exercises": writes.summary,
        "saved": ", ".join(written),
        "google_health": google,
    }
    if writes.remarks:
        payload["remarks"] = writes.remarks
    return payload
