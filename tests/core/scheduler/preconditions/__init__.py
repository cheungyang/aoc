"""Tests for core/scheduler/preconditions, one module per precondition module."""
import os
import tempfile
import time
import unittest

from core.scheduler.spec import ScheduleContext


def ctx(last=None):
    return ScheduleContext(schedule_id="a:x", agent_id="a", now=time.time(), last_success_at=last)


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, content="x", mtime=None):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def abs(self, rel):
        return os.path.join(self.root, rel)
