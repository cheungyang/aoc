import unittest

from core.tooling import Report, render_rules_file
from core.tooling.rules import read_rules_text
from agents.fitness_coach.lib.rules import RULES, defaults

# The numbers the tools used before they moved to RULES.md: the defaults must keep them.
BEFORE_RULES_MD = {
    "recovery_per_hour": 2, "worked_recovery": 10, "secondary_hit": 30,
    "short_sleep_hours": 6, "main_lookback_days": 42, "main_run_gap_days": 21, "rotate_days": 7,
    "max_mains": 8, "accessory_budget": 24, "accessories_per_group_min": 2, "accessories_per_group_max": 4,
    "max_finishers": 6,
    "barbell_step_upper": 5, "barbell_step_lower": 10, "plate_step": 5,
    "rep_step": 2, "rep_step_small": 1, "secs_step": 10, "secs_step_small": 5,
    "deload_after_days": 21, "deload_pct": 10,
}


class TestRules(unittest.TestCase):

    def test_the_defaults_are_the_numbers_the_tools_used_before_rules_md(self):
        self.assertEqual(defaults(), BEFORE_RULES_MD)

    def test_steps_and_hours_read_as_numbers_with_decimals(self):
        """Plates come in 1.25 and 2.5 lb, so a step of 2.5 must read."""
        floats = {rule.key for rule in RULES if isinstance(rule.default, float)}
        self.assertEqual(floats, {"recovery_per_hour", "short_sleep_hours",
                                  "barbell_step_upper", "barbell_step_lower", "plate_step"})

    def test_a_written_rules_md_reads_back_as_the_defaults(self):
        report = Report()
        values, found = read_rules_text(render_rules_file("fitness-coach", RULES), RULES, "RULES.md", report)
        self.assertEqual((values, found, report.issues), (defaults(), len(RULES), []))

    def test_values_changed_by_hand(self):
        text = "- recovery_per_hour: 1.5\n- barbell_step_upper: 2.5\n- deload_after_days: 28  # a month off\n"
        report = Report()
        values, _ = read_rules_text(text, RULES, "RULES.md", report)
        self.assertEqual(report.issues, [])
        self.assertEqual((values["recovery_per_hour"], values["barbell_step_upper"], values["deload_after_days"]),
                         (1.5, 2.5, 28))
        self.assertEqual(values["deload_pct"], 10)

    def test_a_value_that_doesnt_read_keeps_its_default_and_says_so(self):
        report = Report()
        values, _ = read_rules_text("- rep_step: two\n", RULES, "RULES.md", report)
        self.assertEqual(values["rep_step"], 2)
        self.assertIn("line 1: 'rep_step: two' can't be read", report.issue_lines()[0])


if __name__ == "__main__":
    unittest.main()
