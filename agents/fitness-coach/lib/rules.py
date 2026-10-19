"""fitness-coach's thresholds. The user's values live in pkm/agents/fitness-coach/RULES.md.

workout_setup and log_workout_session read them with `load_rules`; WORKFLOW.md
says how each one is applied, with worked examples. Weights are lb.
"""
from core.tooling import Rule

RULES = (
    # Muscle recovery
    Rule("recovery_per_hour", 2.0, "recovery points a muscle group regains per hour, up to 100"),
    Rule("worked_recovery", 10, "recovery a group drops to when a main or accessory lift works it as its primary"),
    Rule("secondary_hit", 30, "recovery points a group loses when a lift works it only as a secondary, or a finisher works it"),
    # The Setup Phase snapshot
    Rule("short_sleep_hours", 6.0, "hours asleep under which last night counts as short"),
    Rule("main_lookback_days", 42, "days back within which a main lift counts as recent"),
    Rule("main_run_gap_days", 21, "days without a main lift after which its next use starts a new run"),
    Rule("rotate_days", 7, "days within which an exercise done ranks last among the candidates"),
    Rule("max_mains", 8, "main lift candidates shown, at most"),
    Rule("accessory_budget", 24, "accessory candidates shown, split across the recovered groups"),
    Rule("accessories_per_group_min", 2, "accessory candidates per recovered group, at least"),
    Rule("accessories_per_group_max", 4, "accessory candidates per recovered group, at most"),
    Rule("max_finishers", 6, "finisher candidates shown, at most"),
    # One step: progression and the 🔼/🔽 loads
    Rule("barbell_step_upper", 5.0, "lb one step adds to an upper-body barbell lift"),
    Rule("barbell_step_lower", 10.0, "lb one step adds to a lower-body barbell lift, deadlifts included"),
    Rule("plate_step", 5.0, "lb one step adds on the landmine, the lever arms or plates held"),
    Rule("rep_step", 2, "reps added when they go up, while that stays within the rep range"),
    Rule("rep_step_small", 1, "reps added instead when rep_step would pass the top of the range"),
    Rule("secs_step", 10, "seconds added to a timed hold after every set was completed, while that stays within the range"),
    Rule("secs_step_small", 5, "seconds added instead when secs_step would pass the top of the range"),
    # The break discount
    Rule("deload_after_days", 21, "days since an exercise was last done after which its break discount is shown"),
    Rule("deload_pct", 10, "the break discount, in %, off the load, the reps or the seconds"),
)


def defaults() -> dict:
    """Every rule at its default: what applies when RULES.md doesn't set it."""
    return {rule.key: rule.default for rule in RULES}
