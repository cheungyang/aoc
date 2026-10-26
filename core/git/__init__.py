"""Git and GitHub: for the coding graph, the PKM and codebase sync, and the
scripts; nothing else in core uses it. Imports only core.config and core.util
(rule G1, checked by tests/test_layers.py).

  ops.py        worktrees, commits, pushes, pull requests and their reviews
  sync.py       the PKM vault and codebase sync
  identity.py   the machine user that pushes, and its token
  privacy.py    the privacy guard: nothing personal in what is pushed

Import from the module itself: this __init__ re-exports nothing.
"""
