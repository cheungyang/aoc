"""Memory v2 entry format: one fact per line, parsed strictly."""
import datetime
import unittest

from core.knowledge.memory.entries import (
    Entry, MemoryFile, TagsError, clean_text, format_file, new_entry, normalise, parse_file,
    parse_line, parse_tags,
)

D = datetime.date


class TestEntryLine(unittest.TestCase):

    def test_round_trip(self):
        entry = Entry("family", "Parents visiting (Bay Area).", "main", D(2026, 9, 10), D(2026, 9, 18), 3,
                      D(2026, 12, 5))
        line = entry.format()
        self.assertEqual(
            line,
            "- [family] Parents visiting (Bay Area). "
            "(src: main · first 2026-09-10 · seen 2026-09-18 · x3 · until 2026-12-05)",
        )
        self.assertEqual(parse_line(line), entry)

    def test_render_hides_metadata(self):
        entry = new_entry("food", "Oat milk OK.", "meal-planner", D(2026, 9, 1))
        self.assertEqual(entry.render(), "- Oat milk OK.")

    def test_malformed_lines_do_not_parse(self):
        for line in (
            "- plain bullet",
            "- [food] no meta",
            "- [food] bad date (src: a · first 2026-13-01 · seen 2026-09-01 · x1)",
            "- [food] no src (first 2026-09-01 · seen 2026-09-01 · x1)",
            "- [food] bad until (src: a · first 2026-09-01 · seen 2026-09-01 · x1 · until soon)",
            "- [food] stray field (src: a · first 2026-09-01 · seen 2026-09-01 · x1 · mood happy)",
        ):
            self.assertIsNone(parse_line(line), line)

    def test_confirm_expire_and_age(self):
        entry = new_entry("food", "x", "a", D(2026, 1, 1), until=D(2026, 2, 1))
        confirmed = entry.confirmed(D(2026, 1, 20))
        self.assertEqual((confirmed.count, confirmed.seen), (2, D(2026, 1, 20)))
        self.assertFalse(entry.expired(D(2026, 2, 1)))
        self.assertTrue(entry.expired(D(2026, 2, 2)))
        self.assertEqual(entry.days_unseen(D(2026, 1, 31)), 30)

    def test_clean_text_is_one_line_and_cannot_fake_metadata(self):
        self.assertEqual(clean_text("a\n  b"), "a b")
        self.assertNotIn("(src:", clean_text("x (src: evil)"))

    def test_normalise_ignores_case_and_punctuation(self):
        self.assertEqual(normalise("Likes  Tea!"), normalise("likes tea"))


class TestFile(unittest.TestCase):

    def test_hand_edits_are_kept_not_dropped(self):
        text = ("# Memory\n\n"
                "- [private] ok (src: a · first 2026-09-01 · seen 2026-09-01 · x1)\n"
                "- typed in Obsidian\n")
        memory = parse_file(text)
        self.assertEqual(len(memory.entries), 1)
        self.assertEqual(memory.unparsed, ["- typed in Obsidian"])
        self.assertEqual(format_file("Memory", memory), text)

    def test_size_counts_the_rendered_form(self):
        memory = MemoryFile([new_entry("food", "abc", "a", D(2026, 1, 1))])
        self.assertEqual(memory.size(), len("- abc"))


class TestTags(unittest.TestCase):

    def test_parses_sections(self):
        text = "# Memory tags\n\nIntro.\n\n## food\nDiet and\nkitchen.\n\n## health\nExercise.\n"
        self.assertEqual(parse_tags(text), [("food", "Diet and kitchen."), ("health", "Exercise.")])

    def test_rejects_bad_tags(self):
        for text in ("## Food\nx", "## profile\nx", "## a\nx\n## a\ny", "## food\n"):
            with self.assertRaises(TagsError, msg=text):
                parse_tags(text)


if __name__ == "__main__":
    unittest.main()
