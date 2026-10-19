import unittest

from core.tooling import Report, render_rules_file
from core.tooling.rules import read_rules_text
from agents.meal_planner.lib.rules import RULES, defaults


class TestRules(unittest.TestCase):

    def test_the_defaults_are_the_numbers_the_tools_used_before_rules_md(self):
        values = defaults()
        self.assertEqual(len(values["shelf_life_days"]), 19)
        self.assertEqual(list(values["shelf_life_days"].values()),
                         [90, 30, 35, 7, 14, 21, 30, 10, 7, 4, 7, 60, 45, 28, 21, 14, 45, 14, 5])
        self.assertEqual({k: v for k, v in values.items() if k != "shelf_life_days"}, {
            "sauces_snacks_days": 180, "frozen_days": 90, "dried_days": 180,
            "fridge_days": {"meat": 3, "seafood": 2, "dairy": 10, "fruit": 7, "veg": 7, "other": 14},
            "cool_dark_days": 30,
            "room_temp_days": {"fruit": 7, "veg": 14, "other": 180},
            "unstated_days": {"meat": 90, "seafood": 90, "dairy": 10, "fruit": 7, "veg": 7, "other": 180},
            "expiring_soon_days": 3, "perishable_days": 30, "recent_meal_days": 14, "default_portions": 2.0,
        })

    def test_defaults_are_copies(self):
        values = defaults()
        values["shelf_life_days"]["草莓"] = 3
        self.assertNotIn("草莓", defaults()["shelf_life_days"])

    def test_a_written_rules_md_reads_back_as_the_defaults(self):
        report = Report()
        values, found = read_rules_text(render_rules_file("meal-planner", RULES), RULES, "RULES.md", report)
        self.assertEqual((values, found, report.issues), (defaults(), len(RULES), []))

    def test_entries_with_quoted_names_read_from_rules_md(self):
        text = '- shelf_life_days:\n  - 蘋果 / "apple" / "apples": 30\n  - 草莓 (Strawberries): 3\n'
        report = Report()
        values, _ = read_rules_text(text, RULES, "RULES.md", report)
        self.assertEqual(report.issues, [])
        self.assertEqual(values["shelf_life_days"]['蘋果 / "apple" / "apples"'], 30)
        self.assertEqual(values["shelf_life_days"]["草莓 (Strawberries)"], 3)


if __name__ == "__main__":
    unittest.main()
