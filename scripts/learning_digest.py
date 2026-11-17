#!/usr/bin/env python3
"""The learning loop's weekly friction digest, for #learning-lab.

Run on Monday mornings by script-executor (agents/script-executor/agent.json).
It says what the nightly scans found over the week (scripts/learning_nightly.py):
the frictions and how they compare with the week before, the patterns that
recur, a few cards for you to label (`!learn label <id>… real|not`), each
detector's precision so far, the prompt ledger's alerts, and where Phase 0's
exit criteria stand. core/learning/report.py writes it, with no model.

Its stdout is the channel message: nothing when the week has nothing to say.
Until #learning-lab exists, the scheduler prints it to the log instead.

    python scripts/learning_digest.py      the digest, as posted
"""
import os
import sys
import time

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.config.settings import Config
from core.learning import report
from core.storage.learning import LearningStore

READ_ONLY = True


def has_work(ctx):
    """Scheduler gate: a digest only when the loop is on and the week has something to say."""
    if not Config().learning_observe:
        return False, "LEARNING_OBSERVE is off"
    store = LearningStore()
    if not store.exists():
        return False, "no memory.db yet"
    if report.digest_text(store, time.time()):
        return True, "a week of friction to report"
    return False, "nothing to report this week"


def main():
    text = report.digest_text(LearningStore(), time.time())
    if text:
        print(text)


if __name__ == "__main__":
    main()
