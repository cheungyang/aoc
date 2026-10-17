import unittest

from core.scheduler.preconditions import build_precondition
from core.scheduler.preconditions.morning_briefing import MorningBriefing


class TestMorningBriefing(unittest.TestCase):

    def test_is_declared_by_its_type(self):
        self.assertIsInstance(build_precondition({"type": "morning_briefing"}), MorningBriefing)


if __name__ == "__main__":
    unittest.main()
