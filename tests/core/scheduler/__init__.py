"""Tests for core/scheduler: one test module per scheduler module.

`tests/__init__.py` replaces `croniter` with a MagicMock for the whole suite,
which would make every cron expression "valid" and every next-run time a mock.
Scheduler tests need the real library; `use_real_croniter()` provides it for
the duration of a test without changing what the rest of the suite sees.
"""
import importlib
import sys
from contextlib import contextmanager
from unittest.mock import patch


def real_croniter_module():
    """The installed croniter module, or None if it is not installed."""
    saved = sys.modules.pop("croniter", None)
    saved_sub = sys.modules.pop("croniter.croniter", None)
    try:
        return importlib.import_module("croniter")
    except ImportError:
        return None
    finally:
        if saved is not None:
            sys.modules["croniter"] = saved
        if saved_sub is not None:
            sys.modules["croniter.croniter"] = saved_sub
        else:
            sys.modules.pop("croniter.croniter", None)


@contextmanager
def use_real_croniter():
    """Makes `import croniter` and the runner's `croniter` name the real thing."""
    module = real_croniter_module()
    if module is None:
        import unittest
        raise unittest.SkipTest("croniter is not installed")
    with patch.dict(sys.modules, {"croniter": module}), \
         patch("core.scheduler.schedule_runner.croniter", module.croniter):
        yield module
