import unittest

from core.tooling import Report, render_rules_file
from core.tooling.rules import read_rules_text
from agents.work_intel.lib.rules import RULES

# The work_sparring skill's lookback: the last three daily journals.
DEFAULTS = {"lookback_days": 3}


class TestRules(unittest.TestCase):

    def test_the_defaults(self):
        self.assertEqual({rule.key: rule.default for rule in RULES}, DEFAULTS)

    def test_the_written_defaults_read_back_unchanged(self):
        report = Report()
        values, found = read_rules_text(render_rules_file("work-intel", RULES), RULES, "RULES.md", report)
        self.assertEqual(values, DEFAULTS)
        self.assertEqual((found, report.issues), (len(RULES), []))

    def test_a_value_changed_by_hand_keeps_its_comment_out(self):
        values, _ = read_rules_text("- lookback_days: 5  # a longer look back\n", RULES, "RULES.md", Report())
        self.assertEqual(values, {"lookback_days": 5})


if __name__ == "__main__":
    unittest.main()
