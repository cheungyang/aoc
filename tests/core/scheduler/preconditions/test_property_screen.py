import unittest

from core.scheduler.preconditions import build_precondition
from core.scheduler.preconditions.property_screen import PropertyScreen


class TestPropertyScreen(unittest.TestCase):

    def test_is_declared_by_its_type(self):
        self.assertIsInstance(build_precondition({"type": "property_screen"}), PropertyScreen)


if __name__ == "__main__":
    unittest.main()
