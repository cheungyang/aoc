"""What the deployment is configured with. Everything reads it, so it imports
nothing from the project (rule C1, checked by tests/test_layers.py).

  settings.py   Config: the settings, read from the environment and .env
  models.py     model ids, tiers and thinking levels, in one place

Import from the module itself: this __init__ re-exports nothing.
"""
