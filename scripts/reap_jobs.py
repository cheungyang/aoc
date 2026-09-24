#!/usr/bin/env python3
"""Marks jobs stuck in `running` / `queued` as `timeout`.

A job row is written as `running` before the work starts and only reaches a
terminal state afterwards, so a process killed in between -- a container
restart, a NAS reboot -- leaves the row `running` forever. The system runs
continuously, so a startup-only sweep would go weeks without firing and would
never catch a job that hung *during* an uptime. Instead this runs on a short
cron like any other script schedule, and `has_work()` makes the idle case free.

The sweep itself belongs to `JobManager` (`has_stale` / `reap_stale`); this is
only the schedulable entry point. Its stdout becomes the channel message, so it
prints only when it actually reaped something.
"""
import argparse
import os
import sys

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.runtime.job_manager import DEFAULT_STALE_SECONDS, JobManager


def has_work(ctx):
    """Scheduler gate: only run when some job has been active for too long."""
    if JobManager().has_stale(max_age_seconds=DEFAULT_STALE_SECONDS):
        return True, f"jobs running/queued for over {DEFAULT_STALE_SECONDS}s"
    return False, "no stale jobs"


def reap(max_age_seconds: int = DEFAULT_STALE_SECONDS) -> str:
    """Reaps stale jobs; returns the user-facing summary, or "" if none."""
    reaped = JobManager().reap_stale(max_age_seconds=max_age_seconds) or []
    if not reaped:
        return ""
    return f"Marked {len(reaped)} stale job(s) as timeout: {', '.join(map(str, reaped))}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mark stale running/queued jobs as timeout.")
    parser.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_STALE_SECONDS,
        help=f"Seconds a job may stay running/queued before it is reaped (default {DEFAULT_STALE_SECONDS}).",
    )
    args = parser.parse_args(argv)
    summary = reap(args.max_age)
    if summary:
        print(summary)


if __name__ == "__main__":
    main()
