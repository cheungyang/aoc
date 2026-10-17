import unittest

from core.scheduler.preconditions import build_precondition
from core.scheduler.preconditions.ha_audit import HaAudit


class TestHaAudit(unittest.TestCase):

    def test_is_declared_by_its_type(self):
        self.assertIsInstance(build_precondition({"type": "ha_audit"}), HaAudit)


if __name__ == "__main__":
    unittest.main()
