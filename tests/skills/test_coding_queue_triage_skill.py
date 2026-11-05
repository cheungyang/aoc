"""The coding_queue_triage skill: its text matches its grants and covers every kind the scan reports."""
import json
import os
import re
import unittest

from agents.software_planner.lib.coding_queue import KINDS

SKILL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "skills", "coding_queue_triage"))


def read(name):
    with open(os.path.join(SKILL_DIR, name), encoding="utf-8") as f:
        return f.read()


class TestCodingQueueTriageSkill(unittest.TestCase):

    def setUp(self):
        self.text = read("SKILL.md")
        self.config = json.loads(read("skill.json"))

    def test_frontmatter_matches_skill_json(self):
        self.assertEqual(re.search(r"^name: (\S+)$", self.text, re.MULTILINE).group(1), "coding_queue_triage")
        self.assertEqual(self.config["skill_id"], "coding_queue_triage")
        self.assertEqual(re.search(r"^description: (.+)$", self.text, re.MULTILINE).group(1),
                         self.config["description"])

    def test_required_tools_match_the_grants(self):
        section = re.split(r"^## Required Tools\s*$", self.text, flags=re.MULTILINE)[-1]
        self.assertEqual(set(re.findall(r"^- `(\w+)`", section, re.MULTILINE)), set(self.config["tools"]))

    def test_every_kind_the_scan_reports_has_a_row(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.assertRegex(self.text, rf"(?m)^\| `{kind}`")

    def test_a_fix_is_one_replace_block_never_an_overwrite(self):
        self.assertIn("`filesystem` `replace_block`", self.text)
        self.assertIn("Never `overwrite` `build_request.json`", self.text)

    def test_it_writes_nowhere_but_the_software_folder(self):
        self.assertEqual(self.config["tools"]["filesystem"], {"pkm/wiki/software": ["@write"]})


if __name__ == "__main__":
    unittest.main()
