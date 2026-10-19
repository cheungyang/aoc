"""Thresholds in pkm/agents/<agent_id>/RULES.md override code defaults, and nothing is dropped silently."""
import os
import tempfile
import unittest

from core.tooling import Report, Rule, load_rules, render_rules_file, rules_path
from core.tooling.rules import coerce, read_rules_text, rule_keys
from core.util.config import Config

RULES = (
    Rule("move_pct", 4.0, "price move, in %, that triggers research"),
    Rule("earnings_days", 7, "days ahead to flag earnings"),
    Rule("include_etfs", False, "screen ETFs too"),
    Rule("home_types", ("house", "townhouse"), "home types to keep"),
    Rule("free_from", "08:00", "when free time starts"),
    Rule("shelf_life_days", {"草莓 (Strawberries)": 3, "牛奶 (Milk)": 7}, "days an item keeps, by name"),
)
DEFAULTS = {rule.key: rule.default for rule in RULES}


class RulesCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        Config().pkm_dir = tmp.name
        self.addCleanup(setattr, Config(), "pkm_dir", None)

    def write(self, text):
        path = rules_path("market-scout")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def load(self, text=None):
        if text is not None:
            self.write(text)
        report = Report()
        return load_rules("market-scout", RULES, report), report


class TestLoadRules(RulesCase):

    def test_the_file_is_pkm_agents_id_rules_md(self):
        self.assertEqual(rules_path("market-scout"), os.path.join(Config().pkm_dir, "agents", "market-scout", "RULES.md"))
        self.assertEqual(rules_path("x", pkm_dir="/p"), "/p/agents/x/RULES.md")

    def test_no_file_means_every_default(self):
        values, report = self.load()
        self.assertEqual(values, DEFAULTS)
        self.assertEqual((report.issues, report.reads), ([], []))
        values["shelf_life_days"]["x"] = 1
        self.assertNotIn("x", RULES[-1].default)  # callers get copies

    def test_values_override_defaults_with_comments_and_units(self):
        values, report = self.load(
            "# Rules\n\nSome prose: with a colon.\n"
            "- move_pct: 3%  # tighter\n"
            "* `earnings_days`: 1,000\n"
            "- include_etfs: yes\n"
            "- home_types: house, condo\n"
            "- free_from: 07:30  # times keep their colon\n"
        )
        self.assertEqual(values["move_pct"], 3.0)
        self.assertEqual(values["earnings_days"], 1000)
        self.assertTrue(values["include_etfs"])
        self.assertEqual(values["home_types"], ("house", "condo"))
        self.assertEqual(values["free_from"], "07:30")
        self.assertEqual(report.issues, [])
        self.assertEqual(report.reads[0].source, "pkm/agents/market-scout/RULES.md")
        self.assertEqual(report.reads[0].records, 5)

    def test_a_table_merges_its_entries_over_the_defaults(self):
        values, report = self.load(
            "- shelf_life_days:  # by name\n"
            "  - 草莓 (Strawberries): 4\n"
            "  - 豆腐 (Tofu): 5\n"
            "\n"
            "- move_pct: 5\n"
        )
        self.assertEqual(values["shelf_life_days"], {"草莓 (Strawberries)": 4, "牛奶 (Milk)": 7, "豆腐 (Tofu)": 5})
        self.assertEqual(values["move_pct"], 5.0)
        self.assertEqual(report.issues, [])

    def test_unknown_keys_and_bad_values_are_issues_and_keep_the_default(self):
        values, report = self.load(
            "- mov_pct: 3\n"
            "- earnings_days: soon\n"
            "- include_etfs: maybe\n"
            "- shelf_life_days: 3\n"
            "- move_pct: 2\n"
            "- move_pct: 1\n"
            "  - stray: 1\n"
        )
        self.assertEqual(values["earnings_days"], 7)
        self.assertFalse(values["include_etfs"])
        self.assertEqual(values["shelf_life_days"], RULES[-1].default)
        self.assertEqual(values["move_pct"], 1.0)
        source = "pkm/agents/market-scout/RULES.md"
        self.assertEqual(report.issue_lines(), [
            f"{source}: line 1: unknown rule 'mov_pct'; ignored",
            f"{source}: line 2: 'earnings_days: soon' can't be read (expected a whole number, got 'soon'); "
            "using the default 7",
            f"{source}: line 3: 'include_etfs: maybe' can't be read (expected yes or no, got 'maybe'); "
            "using the default no",
            f"{source}: line 4: 'shelf_life_days' is a table: list its entries below it, one per line; "
            "using the default",
            f"{source}: line 6: 'move_pct' is set again; this line wins",
            f"{source}: line 7: '- stray: 1' isn't under a table rule; ignored",
        ])
        self.assertEqual(report.status, "partial")

    def test_a_bad_table_entry_keeps_its_default(self):
        values, report = self.load("- shelf_life_days:\n  - 牛奶 (Milk): a week\n")
        self.assertEqual(values["shelf_life_days"]["牛奶 (Milk)"], 7)
        self.assertIn("'牛奶 (Milk): a week' can't be read", report.issue_lines()[0])

    def test_the_rendered_file_reads_back_as_the_defaults(self):
        text = render_rules_file("market-scout", RULES)
        self.assertTrue(text.startswith("# market-scout: rules\n"))
        self.assertIn("- include_etfs: no  # screen ETFs too\n", text)
        self.assertIn("- shelf_life_days:  # days an item keeps, by name\n  - 草莓 (Strawberries): 3\n", text)
        values, report = self.load(text)
        self.assertEqual(values, DEFAULTS)
        self.assertEqual(report.issues, [])
        self.assertEqual(rule_keys(text), [rule.key for rule in RULES])


class TestParsing(unittest.TestCase):

    def test_coerce_types(self):
        self.assertEqual(coerce("1,200", 0), 1200)
        self.assertEqual(coerce("2", 1.5), 2.0)
        self.assertEqual(coerce("`12%`", 1.5), 12.0)
        self.assertFalse(coerce("off", True))
        self.assertEqual(coerce(" a , b ", []), ["a", "b"])
        self.assertEqual(coerce(" a , b ", ["x"]), ["a", "b"])
        self.assertEqual(coerce("2.5, 5", (1.0,)), (2.5, 5.0))
        for raw, default in (("maybe", True), ("4.5", 4), ("x", 1.0), ("2, x", (1,))):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                coerce(raw, default)

    def test_rule_keys_lists_only_top_level_rules(self):
        self.assertEqual(rule_keys("# T\n- a: 1\n  - nested: 2\n- B: 3\n- a: 4\nprose\n"), ["a", "b"])

    def test_read_rules_text_names_its_source(self):
        report = Report()
        values, found = read_rules_text("- move_pct: 2\n- nope: 1\n", RULES, "RULES.md", report)
        self.assertEqual((values["move_pct"], found), (2.0, 1))
        self.assertEqual(report.issue_lines(), ["RULES.md: line 2: unknown rule 'nope'; ignored"])


class TestRuleDefinitions(unittest.TestCase):

    def test_a_rule_is_checked_when_declared(self):
        Rule("ok_key", {"a": 1}, "fine")
        for key, default, description in (
            ("Move", 1, "upper case"),
            ("move pct", 1, "a space"),
            ("move", 1, " "),
            ("move", None, "no default"),
            ("move", [object()], "a list of objects"),
            ("move", {"a": [1]}, "a table of lists"),
        ):
            with self.subTest(key=key, default=default), self.assertRaises((ValueError, TypeError)):
                Rule(key, default, description)


if __name__ == "__main__":
    unittest.main()
