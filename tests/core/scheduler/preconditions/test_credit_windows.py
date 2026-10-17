import unittest

from core.scheduler.preconditions import build_precondition
from core.scheduler.preconditions.credit_windows import CreditWindows


class TestCreditWindows(unittest.TestCase):

    def test_is_declared_by_its_type(self):
        self.assertIsInstance(build_precondition({"type": "credit_windows"}), CreditWindows)


if __name__ == "__main__":
    unittest.main()
