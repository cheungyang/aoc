"""scripts/write_rules_defaults.py: RULES.md defaults go into the PKM, never over the user's file."""
import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from core.tooling import Report
from core.tooling.rules import read_rules_text
from scripts import write_rules_defaults as script

COACH_RULES = '''from core.tooling import Rule

RULES = (
    Rule("recovery_per_hour", 2, "recovery points a group regains per hour"),
    Rule("deload_after_days", 21, "days off before loaded lifts drop 10%"),
)
'''
SCOUT_RULES = '''from core.tooling import Rule

RULES = (
    Rule("move_pct", 4.0, "price move, in %, that triggers research"),
    Rule("earnings_days", 7, "days ahead to flag earnings"),
)
'''
USERS_SCOUT_FILE = "# mine\n- move_pct: 3  # tighter\n- volume_x: 2\n"


class RulesDefaultsCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.agents = os.path.join(tmp.name, "agents")
        self.pkm = os.path.join(tmp.name, "pkm")
        self.put(self.agents, "fitness-coach/lib/rules.py", COACH_RULES)
        self.put(self.agents, "market-scout/lib/rules.py", SCOUT_RULES)
        self.put(self.agents, "main/agent.json", "{}")  # no lib/rules.py: not its business
        self.put(self.pkm, "agents/market-scout/RULES.md", USERS_SCOUT_FILE)

    @staticmethod
    def put(root, rel, text):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def pkm_file(self, agent_id):
        return os.path.join(self.pkm, "agents", agent_id, "RULES.md")

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = script.main(["--agents-dir", self.agents, "--pkm-dir", self.pkm, *args])
        return code, out.getvalue()


class TestWriteRulesDefaults(RulesDefaultsCase):

    def test_agents_are_found_by_folder(self):
        self.assertEqual([agent for agent, _ in script.rules_modules(self.agents)], ["fitness-coach", "market-scout"])

    def test_a_dry_run_shows_the_file_and_writes_nothing(self):
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.pkm_file("fitness-coach")))
        self.assertIn("fitness-coach: would write pkm/agents/fitness-coach/RULES.md:\n    # fitness-coach: rules\n", out)
        self.assertIn("    - deload_after_days: 21  # days off before loaded lifts drop 10%\n", out)
        self.assertIn("Dry run: nothing written. Run with --write to write 1 file.", out)

    def test_write_creates_the_missing_file_with_every_default(self):
        code, out = self.run_main("--write")
        self.assertEqual(code, 0)
        self.assertIn("fitness-coach: wrote pkm/agents/fitness-coach/RULES.md (2 rules)", out)
        self.assertNotIn("Dry run", out)
        with open(self.pkm_file("fitness-coach"), encoding="utf-8") as f:
            text = f.read()
        rules = script.load_rules_tuple("fitness-coach", os.path.join(self.agents, "fitness-coach", "lib", "rules.py"))
        report = Report()
        values, _ = read_rules_text(text, rules, "RULES.md", report)
        self.assertEqual(values, {"recovery_per_hour": 2, "deload_after_days": 21})
        self.assertEqual(report.issues, [])

    def test_the_users_file_is_never_touched_only_reported(self):
        code, out = self.run_main("--write")
        with open(self.pkm_file("market-scout"), encoding="utf-8") as f:
            self.assertEqual(f.read(), USERS_SCOUT_FILE)
        self.assertIn(
            "market-scout: pkm/agents/market-scout/RULES.md is there; left as it is. "
            "Not in it (their defaults apply): earnings_days. Unknown to the code: volume_x.", out)

    def test_a_complete_file_is_said_to_be_complete(self):
        self.put(self.pkm, "agents/market-scout/RULES.md", "- move_pct: 3\n- earnings_days: 5\n")
        _, out = self.run_main()
        self.assertIn("market-scout: pkm/agents/market-scout/RULES.md is there and sets every rule", out)

    def test_a_broken_rules_module_is_reported_and_the_rest_still_run(self):
        self.put(self.agents, "day-planner/lib/rules.py", "RULES = ['not a rule']\n")
        code, out = self.run_main("--write")
        self.assertEqual(code, 1)
        self.assertIn("day-planner: can't read lib/rules.py (ValueError: RULES must be a non-empty tuple of Rule)", out)
        self.assertTrue(os.path.exists(self.pkm_file("fitness-coach")))

    def test_no_agent_with_rules(self):
        code, out = self.run_main("--agents-dir", os.path.join(self.agents, "nowhere"))
        self.assertEqual((code, out.strip()), (0, "No agent declares rules yet (agents/<agent_id>/lib/rules.py)."))

    def test_a_file_that_appears_meanwhile_is_not_overwritten(self):
        real_exists = os.path.exists

        def appears_after_the_check(path):
            found = real_exists(path)
            if path == self.pkm_file("fitness-coach") and not found:
                self.put(self.pkm, "agents/fitness-coach/RULES.md", "- recovery_per_hour: 3\n")
            return found

        with mock.patch.object(script.os.path, "exists", side_effect=appears_after_the_check):
            with self.assertRaises(FileExistsError):
                script.write_defaults(self.agents, self.pkm, write=True)
        with open(self.pkm_file("fitness-coach"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "- recovery_per_hour: 3\n")


if __name__ == "__main__":
    unittest.main()
