import os
from datetime import date

from agents.day_planner.lib.journal_notes import (
    clip,
    daily_note,
    focus_section,
    is_template_focus,
    is_untouched,
    read_note,
    read_weekly_focus,
    weekly_note,
    with_journal_keys,
)
from core.tooling import Report
from tests.agents.day_planner import (
    DAILY_NOTE,
    FLEETING_TEMPLATE,
    NEW_DAILY_NOTE,
    TODAY,
    WEEKLY_FOCUS_NOTE,
    PlanningCase,
)

YESTERDAY = date(2026, 10, 15)


class TestDailyNote(PlanningCase):

    def test_path_and_missing_note(self):
        self.assertEqual(daily_note(YESTERDAY), os.path.join("vault", "journals", "fleeting", "2026-10-15.md"))
        self.assertIsNone(read_note(os.path.join(self.pkm, daily_note(YESTERDAY))))

    def test_a_note_comes_back_as_written(self):
        self.daily(YESTERDAY)
        text = read_note(os.path.join(self.pkm, daily_note(YESTERDAY)))
        self.assertEqual(text, DAILY_NOTE.replace("{day}", "2026-10-15"))

    def test_two_frontmatter_blocks_come_back_as_written(self):
        """The plugin appended its template under a note the agent had written (real: 2026-10-03)."""
        text = ("---\ndate: 2026-10-03\nenergy_level: 7\n---\n# 2026-10-03\n\n#### Agent's recommendation\n"
                "- **Energy Level**: 7/10 (rested)\n" + FLEETING_TEMPLATE)
        self.daily(YESTERDAY, text)
        self.assertEqual(read_note(os.path.join(self.pkm, daily_note(YESTERDAY))), text)


class TestClip(PlanningCase):

    def test_a_short_note_is_whole(self):
        self.assertEqual(clip("a\nb\n"), ("a\nb", 0))

    def test_a_long_note_is_cut_at_a_line_with_a_count(self):
        body = "\n".join(f"line {n} " + "x" * 60 for n in range(60))
        shown, cut = clip(body)
        self.assertLessEqual(len(shown), 3000)
        self.assertTrue(shown.endswith("x"))
        self.assertEqual(len(shown.splitlines()) + cut, 60)
        self.assertGreater(cut, 0)

    def test_a_single_overlong_line_is_kept(self):
        self.assertEqual(clip("y" * 50, limit=10), ("y" * 50, 0))


class TestFocusSection(PlanningCase):

    def test_lines_under_the_heading_as_written(self):
        text = WEEKLY_FOCUS_NOTE.replace("{day}", "2026-10-04")
        self.assertEqual(focus_section(text), [
            "* **🚀 Project A push**: Ship the Project A roadmap %% aoc_id 0f1e2d3c4b5a %%",
            "* **Garden makeover**:",
            "\t* plants - talk to Pat for a quote",
            "* Plan the next quarter",
        ])

    def test_deeper_headings_stay_in_the_section(self):
        text = "## 🌱 Next week's focus\n1. **Errand**: Book the dentist.\n### Details\n* call\n## Book things\n* x"
        self.assertEqual(focus_section(text), ["1. **Errand**: Book the dentist.", "### Details", "* call"])

    def test_no_heading_and_template_placeholders(self):
        self.assertIsNone(focus_section("## ✅ Wins\n* shipped"))
        self.assertTrue(is_template_focus(["* A", "* B", "* C"]))
        self.assertFalse(is_template_focus(["* A", "* B", "* Ship it"]))


class TestWeeklyFocus(PlanningCase):

    def focus(self, today=TODAY, report=None):
        return read_weekly_focus(self.pkm, today, report or Report())

    def test_last_weeks_next_week_focus_is_this_weeks(self):
        self.weekly("2026-W41", WEEKLY_FOCUS_NOTE)
        self.weekly("2026-W42", day="2026-10-11")
        focus = self.focus()
        self.assertEqual(focus.source, "2026-W41")
        self.assertEqual(focus.items[0], "* **🚀 Project A push**: Ship the Project A roadmap "
                                         "%% aoc_id 0f1e2d3c4b5a %%")
        self.assertEqual(len(focus.items), 4)

    def test_falls_back_to_this_weeks_note(self):
        """Earlier weekly reviews wrote the coming week's focus into that week's own note."""
        self.weekly("2026-W41")
        self.weekly("2026-W42", WEEKLY_FOCUS_NOTE, day="2026-10-11")
        focus = self.focus()
        self.assertEqual(focus.source, "2026-W42")
        self.assertEqual([(s.name, s.state) for s in focus.looked], [("2026-W41", "template"), ("2026-W42", "set")])

    def test_agent_written_note_with_a_level_two_heading(self):
        self.weekly("2026-W41", "# Weekly Review 2026-W41\n\n## 🌱 Next week's focus\n1. **Errand**: Book the dentist.\n"
                                "2. **Project A**: Ship the roadmap.\n")
        self.assertEqual(self.focus().items,
                         ["1. **Errand**: Book the dentist.", "2. **Project A**: Ship the roadmap."])

    def test_zero_padded_names_are_found(self):
        self.weekly("2026-W09", WEEKLY_FOCUS_NOTE)
        self.assertEqual(self.focus(date(2026, 3, 4)).source, "2026-W09")

    def test_placeholders_and_missing_notes_say_why(self):
        self.weekly("2026-W41")
        report = Report()
        focus = self.focus(report=report)
        self.assertEqual(focus.items, [])
        self.assertEqual([(s.name, s.state) for s in focus.looked], [("2026-W41", "template"), ("2026-W42", "no note")])
        self.assertEqual(report.status, "ok")  # a note that doesn't exist yet isn't a problem

    def test_a_note_without_the_heading_is_a_problem(self):
        self.weekly("2026-W41", "## ✅ Wins\n* shipped\n")
        report = Report()
        focus = self.focus(report=report)
        self.assertEqual(focus.looked[0].state, "no heading")
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.issue_lines(), ["pkm/vault/journals/weekly/2026-W41.md: no \"Next week's focus\" heading"])

    def test_sunday_reads_the_week_that_just_ended(self):
        self.weekly("2026-W42", WEEKLY_FOCUS_NOTE, day="2026-10-11")
        self.assertEqual(self.focus(date(2026, 10, 18)).source, "2026-W42")


class TestNewNotes(PlanningCase):

    def test_the_template_as_written_with_the_journal_keys(self):
        text = with_journal_keys(FLEETING_TEMPLATE, "fleeting", TODAY)
        lines = text.splitlines()
        self.assertEqual(lines[:5], ["---", "energy_level:", "journal: fleeting", "journal-date: 2026-10-16", "---"])
        self.assertEqual(lines[5], ">[!tip]+ ![[2026-W<% tp.date.now(\"w\")-1 %>#🌱 Next week's focus]]")
        self.assertEqual(text, FLEETING_TEMPLATE.replace("energy_level:\n",
                                                         "energy_level:\njournal: fleeting\njournal-date: 2026-10-16\n"))

    def test_keys_already_there_are_kept(self):
        self.assertEqual(with_journal_keys("---\njournal: fleeting\n---\nbody\n", "fleeting", TODAY),
                         "---\njournal: fleeting\njournal-date: 2026-10-16\n---\nbody\n")

    def test_template_without_frontmatter(self):
        self.assertEqual(with_journal_keys("### Brain dump\n", "fleeting", TODAY),
                         "---\njournal: fleeting\njournal-date: 2026-10-16\n---\n### Brain dump\n")

    def test_a_weekly_note_gets_the_weeks_sunday_and_keeps_every_other_byte(self):
        """As the plugin wrote 2026-W43: the template's own keys first, its trailing blank line kept."""
        template = "---\ncssclass: hide-embedded-header \n---\n<< [[<% tp.date.now(\"gggg-[W]ww\", -1) %>]] >>\n\n"
        self.assertEqual(with_journal_keys(template, "weekly", date(2026, 10, 18)),
                         "---\ncssclass: hide-embedded-header \njournal: weekly\njournal-date: 2026-10-18\n---\n"
                         "<< [[<% tp.date.now(\"gggg-[W]ww\", -1) %>]] >>\n\n")

    def test_the_weekly_note_has_the_plugins_name(self):
        self.assertEqual(weekly_note(date(2026, 10, 23)), os.path.join("vault", "journals", "weekly", "2026-W43.md"))
        self.assertEqual(weekly_note(date(2026, 2, 26)), os.path.join("vault", "journals", "weekly", "2026-W9.md"))


class TestUntouched(PlanningCase):
    """A daily note still exactly what the plugin created: the template with its keys, nothing written."""

    def new_note(self, day=TODAY):
        return NEW_DAILY_NOTE.replace("{day}", day.isoformat())

    def test_the_note_the_plugin_creates_is_untouched_whatever_its_date(self):
        self.assertEqual(self.new_note(), with_journal_keys(FLEETING_TEMPLATE, "fleeting", TODAY))
        self.assertTrue(is_untouched(self.new_note(), FLEETING_TEMPLATE))
        self.assertTrue(is_untouched(self.new_note(YESTERDAY), FLEETING_TEMPLATE))

    def test_line_endings_and_trailing_whitespace_dont_count(self):
        note = self.new_note()
        for same in (
            note.replace("\n", "\r\n"),
            note.replace("\n", "\r"),
            note.replace(" \n", "\n"),  # an editor trimmed the template's trailing spaces
            note.replace("energy_level:", "energy_level:  \t"),
            note + "\n  \n\n",
            note.rstrip("\n"),
        ):
            with self.subTest(note=repr(same[-40:])):
                self.assertTrue(is_untouched(same, FLEETING_TEMPLATE))
        self.assertTrue(is_untouched(note, FLEETING_TEMPLATE.replace("\n", "\r\n")))

    def test_the_plugins_keys_dont_count_wherever_they_sit(self):
        note = self.new_note()
        keys = "journal: fleeting\njournal-date: 2026-10-16\n"
        for same in (
            note.replace(keys, ""),  # a note without them
            note.replace("energy_level:\n" + keys, keys + "energy_level:\n"),
            note.replace("journal-date: 2026-10-16", "journal-date: \"2026-10-01\""),
        ):
            with self.subTest(note=same[:80]):
                self.assertTrue(is_untouched(same, FLEETING_TEMPLATE))

    def test_a_frontmatter_value_is_writing(self):
        self.assertFalse(is_untouched(self.new_note().replace("energy_level:", "energy_level: 6"), FLEETING_TEMPLATE))

    def test_any_other_change_is_writing(self):
        note = self.new_note()
        for written in (
            note.replace("### Brain dump 🧠🗑️\n", "### Brain dump 🧠🗑️\nCall Pat\n"),
            note + "\n#### Agent's recommendation\n- [ ] Ship the roadmap draft\n",
            note.replace("### Sparks & Ideas 🌟", "### Sparks"),
            note.replace("\n\n### Brain dump", "\n\n\n### Brain dump"),  # a blank line added mid-note
            note.replace("energy_level:\n", "energy_level:\nmood:\n"),  # a key added
            note.replace("energy_level:\n", ""),  # a key of the template's removed
            "---\ndate: 2026-10-03\n---\n# 2026-10-03\n" + note,  # the plugin's template under a written note
        ):
            with self.subTest(note=written[:80]):
                self.assertFalse(is_untouched(written, FLEETING_TEMPLATE))

    def test_a_template_without_frontmatter(self):
        template = "### Brain dump\n\n### Sparks\n"
        self.assertTrue(is_untouched(with_journal_keys(template, "fleeting", TODAY), template))
        self.assertTrue(is_untouched(template, template))
        self.assertFalse(is_untouched("---\nmood: calm\n---\n" + template, template))
