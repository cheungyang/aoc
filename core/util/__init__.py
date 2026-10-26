"""Small helpers with no domain. A util imports only core.config (rule U1,
checked by tests/test_layers.py): a helper that needs more belongs with its
consumer, not here.

  local_time.py   the user's zone and "now"; DST-safe conversions
  errors.py       readable text for an exception; whether a service raised it
  fx.py           money amounts and exchange rates

Import from the module itself: this __init__ re-exports nothing.
"""
