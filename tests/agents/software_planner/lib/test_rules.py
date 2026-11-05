import unittest

from core.tooling import Report, render_rules_file
from core.tooling.rules import read_rules_text
from agents.software_planner.lib.rules import RULES

# The defaults the approved proposal settled on (§9): a daily reminder, two fixes.
DEFAULTS = {"remind_after_hours": 24, "max_fixes_per_task": 2}


class TestRules(unittest.TestCase):

    def test_the_defaults_are_the_approved_ones(self):
        self.assertEqual({rule.key: rule.default for rule in RULES}, DEFAULTS)

    def test_the_written_defaults_read_back_unchanged(self):
        report = Report()
        values, found = read_rules_text(render_rules_file("software-planner", RULES), RULES, "RULES.md", report)
        self.assertEqual(values, DEFAULTS)
        self.assertEqual((found, report.issues), (len(RULES), []))

    def test_values_changed_by_hand(self):
        values, _ = read_rules_text("- remind_after_hours: 12\n- max_fixes_per_task: 1\n", RULES, "RULES.md", Report())
        self.assertEqual(values, {"remind_after_hours": 12, "max_fixes_per_task": 1})


if __name__ == "__main__":
    unittest.main()
