"""Scheduled work: loading, validating, gating and running every schedule.

Every schedule — an LLM prompt or a script — goes
through one protocol (`spec.ScheduleSpec`): the runner asks `has_work()` first,
and only a schedule that says yes costs anything.
"""
