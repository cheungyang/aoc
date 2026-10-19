"""workout_setup: the workout Setup Phase in one call, and the picks' serving details at Let's Go."""
from typing import List, Optional, Tuple

from core.tooling import Report, agent_tool, get_local_now, load_rules
from agents.fitness_coach.lib.rules import RULES
from agents.fitness_coach.lib.workout_data import load_data
from agents.fitness_coach.lib.workout_plan import build_details, build_setup, last_night_hours

GOOGLE_HEALTH = "Google Health"


def _now():
    """Local time now. A function so tests can pin it."""
    return get_local_now()


def _read_sleep() -> dict:
    """Last night's sleep via the google_health tool's own client and reader (read-only)."""
    from tools.google_health import GoogleHealthClient, read_sleep
    return read_sleep(GoogleHealthClient(), {"days": 1})


def _sleep(now, report: Report) -> Tuple[Optional[float], Optional[str]]:
    try:
        hours = last_night_hours(_read_sleep(), now.date())
    except Exception as e:  # unknown sleep never blocks a workout
        report.problem(GOOGLE_HEALTH, f"sleep couldn't be read ({str(e)[:120]})")
        return None, f"unknown (Google Health: {str(e)[:120]})"
    report.read(GOOGLE_HEALTH, records=0 if hours is None else 1)
    return hours, None if hours is not None else "unknown (no sleep logged for today yet)"


@agent_tool
def workout_setup(report: Report, ids: Optional[List[str]] = None) -> dict:
    """Everything the workout Setup Phase needs in one call; with `ids`, what serving the picks needs.

    Reads: pkm/wallet/exercise/, last night's sleep (Google Health), RULES.md.
    Computes: recovery now; ranked candidates with their targets, days since last done and break discounts; with ids, 🔼/🔽 loads and weight gaps.
    Leaves out: which exercises to pick, and the load after a break or a weight gap (coach_home_workout).

    workout_setup() returns:
    - start: local time now (YYYY-MM-DDTHH:MM).
    - sleep_h: last night's hours asleep; short_sleep: true when short. sleep_note when unknown.
    - eligible: muscle groups at 100% recovery; recovering: the others with their current %.
    - recent_mains: recent main lifts, each with the date its run began.
    - main / accessory / finisher: rows "id | name | group | station | level | days | rx" for the
      eligible groups, best first (not done lately first, advanced ones not yet started last),
      spread over stations. days: since last done ("-" never). rx per set: "5-8 @ 185" (reps @ lb),
      "9" (reps), "45s" (seconds); "/leg" or "/side" is per side; "(new)" = never done, a
      calibration start (empty bar, lightest dial, easy angle); "(-10%: 5-8 @ 165)" = the break
      discount, shown after a long break.
    When no group is recovered there are no rows, and a note says which group is next and when.

    workout_setup(ids=[...]) returns start (the session start to pass to log_workout_session)
    and, per exercise: name, rx, cue, media; loaded lifts also get loads (buildable weights one
    🔼/🔽 step apart) and, when one step up from a start can't be built, gap: [{from, below,
    above, top_reps}].

    Args:
        ids: Library ids (or exact library names) of the picked exercises. Omit for the Setup Phase.
    """
    rules = load_rules("fitness-coach", RULES, report)
    now = _now()
    data = load_data(report)
    report.raise_if_failed()  # no library: failed, the issues say why
    if ids:
        details = build_details(data, ids, now, rules, report)
        report.raise_if_failed()  # none of the ids is in the library
        return details
    return build_setup(data, now, *_sleep(now, report), rules, report)
