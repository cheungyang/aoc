#!/usr/bin/env python3
"""The learning loop's nightly scan: the friction detectors, then the prompt ledger.

Run at 02:00 by script-executor (agents/script-executor/agent.json), after the
23:00 dream and the 00:00 sync. It reads the last three days of memory.db
(thresholds.LOOKBACK_S: a day read twice adds nothing twice, and a missed
night is caught up), records the new friction_events and one learning_runs row
(core/learning/detectors.py), and snapshots each agent's prompt size
(core/learning/ledger.py). Nothing it does changes how an agent behaves
(core/learning/README.md).

Its stdout is the channel message, so it prints only when part of the scan failed.

    python scripts/learning_nightly.py             the scan, as scheduled
    python scripts/learning_nightly.py --days 30   a longer window: the first run, or after an outage
    python scripts/learning_nightly.py --dry-run   what it would record, per detector; records nothing
"""
import argparse
import os
import sys
import time
from collections import Counter

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.config.settings import Config
from core.learning import detectors, ledger
from core.learning import thresholds as T
from core.storage.learning import LearningStore

DAY = 86400


def has_work(ctx):
    """Scheduler gate: the scan runs every night, unless the learning loop is off."""
    if not Config().learning_observe:
        return False, "LEARNING_OBSERVE is off"
    return True, "the nightly friction scan"


def scan(store: LearningStore, *, days: float = None, now: float = None, dry_run: bool = False,
         vault: str = None) -> str:
    """Runs the detectors over the window and records what they found, the run, and the
    day's ledger. Returns what to post: "" when everything worked, else what failed.
    With `dry_run`, records nothing and returns the counts per detector."""
    now = time.time() if now is None else now
    since = now - (days * DAY if days else T.LOOKBACK_S)
    started = time.monotonic()
    result = detectors.scan(store, since, now, now=now, vault=vault)
    rows = [event.row() for event in result.events]
    if dry_run:
        counts = Counter(row["detector"] for row in rows)
        lines = [f"{len(rows)} friction events over {(now - since) / DAY:.0f} day(s) (nothing recorded):"]
        lines += [f"  {detector}: {n}" for detector, n in counts.most_common()]
        lines += [f"  muted: {', '.join(result.muted)}"] if result.muted else []
        lines += [f"  failed: {error}" for error in result.errors]
        return "\n".join(lines)
    added = store.record_friction(rows, found_at=now)
    errors = list(result.errors)
    try:
        ledger.snapshot(store, detectors.agent_ids(), day=ledger.today(now))
    except Exception as e:
        errors.append(f"ledger: {type(e).__name__}: {e}")
    store.record_run(since=since, until=now, found=len(rows), added=added,
                     took_s=round(time.monotonic() - started, 1), errors="\n".join(errors), at=now)
    if not errors:
        return ""
    return "\n".join([f"🪞 The nightly friction scan found {len(rows)} ({added} new), but part of it failed:"]
                     + [f"- {error}" for error in errors])


def main(argv=None):
    parser = argparse.ArgumentParser(description="The learning loop's nightly friction scan and prompt ledger.")
    parser.add_argument("--days", type=float, default=None,
                        help=f"days to read (default {T.LOOKBACK_S // DAY})")
    parser.add_argument("--dry-run", action="store_true", help="print the counts per detector; record nothing")
    args = parser.parse_args(argv)
    output = scan(LearningStore(), days=args.days, dry_run=args.dry_run, vault=Config().pkm_dir or None)
    if output:
        print(output)


if __name__ == "__main__":
    main()
