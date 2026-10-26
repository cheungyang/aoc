import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from agents.day_planner.lib.briefing import build_briefing
from core.tooling import Report
from core.config.settings import Config
from tests.agents.day_planner import DAILY_NOTE, NEW_DAILY_NOTE, WEEKLY_FOCUS_NOTE, PlanningCase

ZONE = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 16, 6, 0, tzinfo=ZONE)
RULES = {"chore_lead_days": 3}
TITLES = [
    "Fri 2026-10-16 · ISO week 2026-W42",
    "Daily notes in the last 7 days:",
    "This week's focus",
    "Yesterday",
    "Today's note",
    "Recurring chores:",
    "Open tasks due by Fri 10-23 or scheduled on or before today (chores excluded):",
    "Active 2026 projects (executing or planning) by priority, with their top open tasks:",
]


class TestBriefing(PlanningCase):

    def setUp(self):
        super().setUp()
        self.daily(date(2026, 10, 15))
        self.daily(date(2026, 10, 12))
        self.weekly("2026-W41", WEEKLY_FOCUS_NOTE)
        self.project("Project A", priority="🔺", rank=1, aliases=["#p/alpha"], category="💼 Work")
        self.task("- [ ] Talk to Pat about the launch plan ⏫ #p/alpha", "vault/journals/fleeting/2026-08-18.md")
        self.task("- [ ] Pay the water bill every 15th of the month ⏫ 🔁 every month  📅 2026-10-15")
        self.task("- [ ] check and pay the internet bill 📅 2026-10-21", "ticktick/Inbox.md")

    def build(self, report=None, include_new_note=False):
        return build_briefing(NOW, report or Report(), RULES, include_new_note=include_new_note)

    def section(self, text, title):
        """The section starting with `title`, up to the next section's title."""
        start = text.index(f"\n\n{title}") + 2
        later = [text.index(f"\n\n{t}", start) for t in TITLES if f"\n\n{t}" in text[start:]]
        return text[start:min(later)] if later else text[start:]

    def test_every_section_in_order(self):
        text = self.build()
        self.assertTrue(text.startswith(TITLES[0]))
        positions = [text.index(f"\n\n{title}") for title in TITLES[1:]]
        self.assertEqual(positions, sorted(positions))

    def test_dates_and_weeks_are_computed(self):
        text = self.build()
        self.assertTrue(text.startswith(
            "Fri 2026-10-16 · ISO week 2026-W42 · vault week 2026-W42 (Sun 10-11 to Sat 10-17)\n"))
        self.assertIn("Daily notes in the last 7 days: Mon 10-12, Thu 10-15; none on Fri 10-09, Sat 10-10, Sun 10-11, "
                      "Tue 10-13, Wed 10-14", text)

    def test_notes_come_back_as_written(self):
        text = self.build()
        self.assertIn("Yesterday:\nThu 2026-10-15, pkm/vault/journals/fleeting/2026-10-15.md, as written:\n~~~markdown\n"
                      + DAILY_NOTE.replace("{day}", "2026-10-15").rstrip("\n") + "\n~~~", text)
        self.assertIn("This week's focus:\n(from 2026-W41, \"Next week's focus\", as written)\n"
                      "* **🚀 Project A push**: Ship the Project A roadmap %% aoc_id 0f1e2d3c4b5a %%\n"
                      "* **Garden makeover**:\n\t* plants - talk to Pat for a quote\n* Plan the next quarter", text)

    def test_today_exists_or_not(self):
        self.assertIn("\n\nToday's note: pkm/vault/journals/fleeting/2026-10-16.md: not created yet\n\n", self.build())
        self.daily(NOW.date())
        self.assertIn("Today's note:\npkm/vault/journals/fleeting/2026-10-16.md, as written:\n~~~markdown\n---\n"
                      "energy_level: 6\njournal: fleeting\njournal-date: 2026-10-16\n", self.build())

    def test_chores_and_tasks_are_computed(self):
        text = self.build()
        self.assertIn("1 day overdue (due Thu 10-15)", self.section(text, "Recurring chores:"))
        self.assertIn("- check and pay the internet bill: due in 5 days (Wed 10-21)", text)
        self.assertIn("  - ⏫ Talk to Pat about the launch plan", text)

    def test_new_note_text_only_when_asked_for_and_missing(self):
        text = self.build(include_new_note=True)
        self.assertIn("not created yet. New-note text (the template as the Journals plugin writes it):\n~~~markdown\n"
                      "---\nenergy_level:\njournal: fleeting\njournal-date: 2026-10-16\n---\n"
                      ">[!tip]+ ![[2026-W<% tp.date.now(\"w\")-1 %>#🌱 Next week's focus]]", text)
        self.assertIn("### Sparks & Ideas 🌟\n~~~", text)
        self.daily(NOW.date())
        self.assertNotIn("New-note text", self.build(include_new_note=True))

    def test_a_note_nothing_is_written_in_yet_is_one_line(self):
        self.daily(date(2026, 10, 15), NEW_DAILY_NOTE)
        self.daily(NOW.date(), NEW_DAILY_NOTE.replace("\n", "\r\n"))
        report = Report(explain=True)
        text = self.build(report, include_new_note=True)
        self.assertIn("\n\nYesterday: Thu 2026-10-15, pkm/vault/journals/fleeting/2026-10-15.md: "
                      "created from the template, nothing written yet\n\n", text)
        self.assertIn("\n\nToday's note: pkm/vault/journals/fleeting/2026-10-16.md: "
                      "created from the template, nothing written yet\n\n", text)
        self.assertNotIn("Brain dump", text)
        self.assertEqual((report.status, report.issues), ("ok", []))
        self.assertEqual([read.source for read in report.reads].count("pkm/templates/Fleeting Pages.md"), 1)
        self.assertIn("pkm/vault/journals/fleeting/2026-10-15.md: the same as pkm/templates/Fleeting Pages.md once "
                      "line endings, trailing whitespace and the Journals plugin's keys are set aside", report.traces)

    def test_a_note_with_only_a_frontmatter_value_comes_back_in_full(self):
        note = NEW_DAILY_NOTE.replace("{day}", "2026-10-15").replace("energy_level:", "energy_level: 6")
        self.daily(date(2026, 10, 15), note)
        self.assertIn("Yesterday:\nThu 2026-10-15, pkm/vault/journals/fleeting/2026-10-15.md, as written:\n~~~markdown\n"
                      + note.rstrip("\n") + "\n~~~", self.build())

    def test_without_the_template_a_note_comes_back_in_full(self):
        os.remove(os.path.join(self.pkm, "templates", "Fleeting Pages.md"))
        self.daily(NOW.date(), NEW_DAILY_NOTE)
        report = Report()
        text = self.build(report, include_new_note=True)
        self.assertIn("Today's note:\npkm/vault/journals/fleeting/2026-10-16.md, as written:\n~~~markdown\n---\n"
                      "energy_level:\njournal: fleeting\n", text)
        self.assertEqual(report.status, "ok")  # the note exists, so no new-note text needs the template

    def test_a_long_note_is_cut_with_a_count(self):
        body = "\n".join(f"line {n} " + "x" * 60 for n in range(80))
        self.daily(date(2026, 10, 15), "### Brain dump\n" + body + "\n")
        yesterday = self.section(self.build(), "Yesterday")
        self.assertRegex(yesterday, r"\n~~~\n\[\d+ more lines in the note\]$")
        self.assertIn("line 0 ", yesterday)
        self.assertNotIn("line 79 ", yesterday)

    def test_a_fence_inside_a_note_gets_a_longer_one(self):
        self.daily(date(2026, 10, 15), "~~~\ncode\n~~~\n")
        self.assertIn("~~~~markdown\n~~~\ncode\n~~~\n~~~~", self.build())

    def test_everything_read_is_ok(self):
        report = Report()
        self.build(report)
        self.assertEqual((report.status, report.issues), ("ok", []))

    def test_an_unreadable_source_costs_only_its_section(self):
        os.remove(Config().projects_db_path)
        report = Report()
        text = self.build(report)
        self.assertIn("Active 2026 projects (executing or planning) by priority, with their top open tasks: "
                      "unavailable (", text)
        self.assertIn("1 day overdue (due Thu 10-15)", text)
        self.assertEqual(report.status, "partial")
        self.assertTrue(report.issue_lines()[0].endswith("projects.db: not found"))

    def test_an_error_in_one_section_is_reported_and_costs_only_that_section(self):
        from unittest import mock

        with mock.patch("agents.day_planner.lib.project_slate.due_tasks", side_effect=RuntimeError("bad row")):
            report = Report()
            text = self.build(report)
        self.assertIn("(chores excluded): unavailable (RuntimeError: bad row)", text)
        self.assertIn("1 day overdue (due Thu 10-15)", text)
        self.assertEqual(report.issue_lines(), [
            "Open tasks due by Fri 10-23 or scheduled on or before today (chores excluded): "
            "couldn't be read (RuntimeError: bad row)"])

    def test_nothing_is_written(self):
        before = sorted(os.walk(self.root))
        self.build(include_new_note=True)
        self.assertEqual(sorted(os.walk(self.root)), before)
