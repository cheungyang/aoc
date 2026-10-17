import unittest

from core.scheduler.preconditions import build_precondition
from core.scheduler.preconditions.market_screen import MarketScreen


class TestMarketScreen(unittest.TestCase):

    def test_is_declared_by_its_type(self):
        self.assertIsInstance(build_precondition({"type": "market_screen"}), MarketScreen)


if __name__ == "__main__":
    unittest.main()
