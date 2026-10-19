import unittest

from core.tooling import Report, render_rules_file
from core.tooling.rules import read_rules_text
from agents.goal_setter.lib.rules import RULES

# The ranking before its numbers moved to RULES.md: the defaults must keep it.
TODAYS_RANKING = {
    "rest_days": 7, "coach_every_days": 14, "long_gap_days": 28, "stall_days": 30,
    "never_coached": 3, "due_again": 2, "long_gap": 3, "stalled": 2, "no_open_tasks": 2, "overdue": 1,
    "priority_points": {"🔺": 3, "⏫": 2, "🔼": 1, "🔽": 0, "⏬": 0},
}


class TestRules(unittest.TestCase):

    def test_the_defaults_are_todays_ranking(self):
        self.assertEqual({rule.key: rule.default for rule in RULES}, TODAYS_RANKING)

    def test_the_written_defaults_read_back_unchanged(self):
        report = Report()
        values, found = read_rules_text(render_rules_file("goal-setter", RULES), RULES, "RULES.md", report)
        self.assertEqual(values, TODAYS_RANKING)
        self.assertEqual((found, report.issues), (len(RULES), []))

    def test_a_weight_and_one_priority_changed_by_hand(self):
        text = "- stalled: 3\n- priority_points:\n  - ⏫: 4\n"
        values, _ = read_rules_text(text, RULES, "RULES.md", Report())
        self.assertEqual(values["stalled"], 3)
        self.assertEqual(values["priority_points"], {"🔺": 3, "⏫": 4, "🔼": 1, "🔽": 0, "⏬": 0})


if __name__ == "__main__":
    unittest.main()
