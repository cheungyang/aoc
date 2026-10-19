import unittest

from agents.day_planner.lib.note_sections import SectionError, fill_sections
from tests.agents.day_planner import WEEKLY_TEMPLATE

WINS = [
    ("🏆 Works wins", "* Shipped Project A's draft\n* Hired for Project B"),
    ("🏆 Family wins", "* A weekend away"),
    ("🏆 Personal development wins", "* Ran three times"),
    ("🌱 Next week's focus", "* **Project A**: ship it\n    - book the review\n* **Project B**: plan\n* Rest"),
]


class TestFillSections(unittest.TestCase):

    def test_the_template_takes_the_review_and_keeps_every_other_line(self):
        text, filled = fill_sections(WEEKLY_TEMPLATE, WINS)
        expected = (WEEKLY_TEMPLATE
                    .replace("### 🏆 Works wins\n", "### 🏆 Works wins\n* Shipped Project A's draft\n* Hired for Project B\n")
                    .replace("### 🏆 Family wins\n", "### 🏆 Family wins\n* A weekend away\n")
                    .replace("### 🏆 Personal development wins\n", "### 🏆 Personal development wins\n* Ran three times\n")
                    .replace("* A\n* B\n* C\n", "* **Project A**: ship it\n    - book the review\n* **Project B**: plan\n* Rest\n"))
        self.assertEqual(text, expected)
        self.assertEqual([(f.heading, f.lines, f.replaced) for f in filled], [
            ("🏆 Works wins", 2, []), ("🏆 Family wins", 1, []), ("🏆 Personal development wins", 1, []),
            ("🌱 Next week's focus", 4, []),
        ])

    def test_headings_are_named_whatever_their_case_emoji_and_punctuation(self):
        text, filled = fill_sections(WEEKLY_TEMPLATE, [("works WINS", "* x"), ("Next week’s focus", "* y")])
        self.assertEqual([f.heading for f in filled], ["🏆 Works wins", "🌱 Next week's focus"])
        self.assertIn("### 🌱 Next week's focus\n* y\n\n\n### 🗓️", text)

    def test_a_section_with_other_text_is_refused_and_nothing_changes(self):
        text, _ = fill_sections(WEEKLY_TEMPLATE, WINS)
        with self.assertRaises(SectionError) as caught:
            fill_sections(text, [("🏆 Family wins", "* Another"), ("🏆 Works wins", "* New")])
        self.assertEqual(str(caught.exception),
                         "- '🏆 Family wins' already has text:\n    * A weekend away\n"
                         "- '🏆 Works wins' already has text:\n    * Shipped Project A's draft\n    * Hired for Project B")

    def test_replace_overwrites_and_says_what_it_replaced(self):
        text, _ = fill_sections(WEEKLY_TEMPLATE, WINS)
        again, filled = fill_sections(text, [("🌱 Next week's focus", "* Project C")], replace=True)
        self.assertIn("### 🌱 Next week's focus\n* Project C\n\n\n### 🗓️", again)
        self.assertEqual(filled[0].replaced, ["* **Project A**: ship it", "    - book the review", "* **Project B**: plan",
                                              "* Rest"])
        self.assertEqual(again.count("Project A"), 1)  # the wins keep theirs

    def test_the_templates_placeholder_isnt_text_to_keep(self):
        _, filled = fill_sections("## 🌱 Next week's focus\n1. A\n2. B\n3. C\n", [("Next week's focus", "1. Ship")])
        self.assertEqual(filled[0].replaced, [])

    def test_a_missing_heading_lists_the_notes_headings(self):
        with self.assertRaises(SectionError) as caught:
            fill_sections("## Wins\n\n## Focus\n", [("Work wins", "* x")])
        self.assertEqual(str(caught.exception), "- no heading 'Work wins'\n- the note's headings: Wins; Focus")

    def test_a_heading_twice_in_the_note_or_in_the_call_is_refused(self):
        with self.assertRaises(SectionError) as caught:
            fill_sections("## Wins\n\n## Wins\n", [("Wins", "* x"), ("wins", "* y")])
        self.assertEqual(str(caught.exception), "- 'Wins' matches 2 headings\n- 'wins' is given twice")

    def test_headings_in_code_blocks_and_frontmatter_dont_count(self):
        note = "---\n# not a heading: a YAML comment\n---\n```\n# Wins\n```\n## Wins\n"
        text, _ = fill_sections(note, [("Wins", "* x")])
        self.assertEqual(text, note + "* x\n")

    def test_a_level_two_heading_keeps_its_subsections(self):
        text, _ = fill_sections("## Wins\n### Work\n* old\n", [("Wins", "* all")])
        self.assertEqual(text, "## Wins\n* all\n\n### Work\n* old\n")

    def test_empty_text_writes_nothing(self):
        text, filled = fill_sections(WEEKLY_TEMPLATE, [("🏆 Family wins", "\n  \n")])
        self.assertEqual((text, filled[0].lines), (WEEKLY_TEMPLATE, 0))

    def test_outer_blank_lines_and_trailing_spaces_are_trimmed(self):
        text, _ = fill_sections("## Focus\n", [("Focus", "\n* a  \n\n* b\n\n")])
        self.assertEqual(text, "## Focus\n* a\n\n* b\n")

    def test_a_note_without_a_final_newline(self):
        self.assertEqual(fill_sections("## Focus", [("Focus", "* a")])[0], "## Focus\n* a")


if __name__ == "__main__":
    unittest.main()
