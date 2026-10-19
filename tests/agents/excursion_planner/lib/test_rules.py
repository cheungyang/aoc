import unittest
from datetime import time, timedelta

from core.tooling import OK, PARTIAL, Report, render_rules_file
from core.tooling.rules import read_rules_text
from agents.excursion_planner.lib.rules import RULES
from agents.excursion_planner.lib.weekends import FreeTime, free_time


def read(text):
    """RULES.md text -> (FreeTime, report), the way the tool reads it."""
    report = Report()
    values, _ = read_rules_text(text, RULES, "RULES.md", report)
    return free_time(values, report), report


class TestRules(unittest.TestCase):

    def test_the_defaults_are_todays_values(self):
        self.assertEqual({rule.key: rule.default for rule in RULES},
                         {"free_from": "08:00", "free_until": "22:00", "min_gap_hours": 1.0})

    def test_a_defaults_file_reads_back_as_todays_free_time(self):
        free, report = read(render_rules_file("excursion-planner", RULES))
        self.assertEqual(free, FreeTime(time(8), time(22), timedelta(hours=1)))
        self.assertEqual(report.status, OK)

    def test_the_users_lines(self):
        free, report = read("- free_from: 09:30  # slow mornings\n- free_until: 21:00\n- min_gap_hours: 2\n")
        self.assertEqual(free.describe(), "09:30–21:00, gaps of 2 h or more")
        self.assertEqual(report.status, OK)

    def test_a_gap_that_isnt_a_number_keeps_the_default(self):
        free, report = read("- min_gap_hours: an hour\n")
        self.assertEqual(free.min_gap, timedelta(hours=1))
        self.assertEqual(report.status, PARTIAL)


if __name__ == "__main__":
    unittest.main()
