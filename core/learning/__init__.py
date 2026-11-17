"""The self-learning loop: find friction, propose small prompt changes, prove them.

Phase 0 (this package today) only observes. Nothing here changes how an agent
behaves. core/learning/README.md has the flow:

    events.py     what the runtime tells the loop, written off the event loop
    versions.py   which prompt each model call ran on
    detectors.py  the nightly friction detectors: deterministic, no model
    thresholds.py every number the detectors use, in one place
    ledger.py     each agent's prompt size, snapshotted daily
    report.py     the texts `!learn` and the weekly digest show
"""
