import os
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from core.loaders.tools_loader import ToolsLoader
from tests.agents.day_planner import WEEKLY_TEMPLATE, WEEKLY_TEMPLATE_NOTE, PlanningCase
from tests.helpers import envelope, execution_context
from agents.day_planner.tools import write_weekly_review as module
from agents.day_planner.tools.write_weekly_review import write_weekly_review

FRIDAY = datetime(2026, 10, 23, 15, 20, tzinfo=ZoneInfo("America/Los_Angeles"))
SUNDAY = datetime(2026, 10, 25, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
SECTIONS = [
    {"heading": "🏆 Works wins", "text": "* Shipped Project A"},
    {"heading": "🌱 Next week's focus", "text": "* Project A\n* Project B\n* Rest"},
]


class TestWriteWeeklyReview(PlanningCase):

    def setUp(self):
        super().setUp()
        self.write("templates/Weekly Review.md", WEEKLY_TEMPLATE)
        self.now = FRIDAY

    def call(self, agent_id="day-planner", **args):
        args.setdefault("sections", SECTIONS)
        with mock.patch.object(module, "get_local_now", return_value=self.now):
            with execution_context(agent_id=agent_id):
                return envelope(write_weekly_review.invoke(args))

    def note(self, name="2026-W43"):
        path = os.path.join(self.pkm, "vault", "journals", "weekly", f"{name}.md")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_needs_an_agent_run(self):
        reply = envelope(write_weekly_review.invoke({"sections": SECTIONS}))
        self.assertEqual(reply["status"], "failed")
        self.assertIn("no active execution context", reply["errors"])

    def test_friday_creates_this_weeks_note_from_the_template(self):
        reply = self.call()
        self.assertEqual((reply["status"], reply["issues"], reply["errors"]), ("ok", "None", "None"))
        self.assertEqual(reply["payload"],
                         "Saved in pkm/vault/journals/weekly/2026-W43.md (the week of Sun 10-18 to Sat 10-24), "
                         "a new note from the weekly template:\n"
                         "- 🏆 Works wins: 1 line\n"
                         "- 🌱 Next week's focus: 3 lines")
        text = self.note()
        self.assertTrue(text.startswith("---\ncssclass: hide-embedded-header \njournal: weekly\njournal-date: 2026-10-18\n"
                                        "---\n<< [[<% tp.date.now("))
        self.assertIn("### 🌱 Next week's focus\n* Project A\n* Project B\n* Rest\n\n\n### 🗓️", text)

    def test_sunday_writes_the_week_that_just_ended(self):
        self.weekly("2026-W43", WEEKLY_TEMPLATE_NOTE, day="2026-10-18")
        self.now = SUNDAY
        reply = self.call(sections=[SECTIONS[1]])
        self.assertIn("2026-W43.md (the week of Sun 10-18 to Sat 10-24), the existing note:", reply["payload"])
        self.assertIn("### 🌱 Next week's focus\n* Project A\n", self.note())
        self.assertIsNone(self.note("2026-W44"))

    def test_text_already_in_a_section_fails_and_shows_it(self):
        mine = WEEKLY_TEMPLATE_NOTE.replace("{day}", "2026-10-18").replace("* A\n* B\n* C\n", "* My own focus\n")
        self.weekly("2026-W43", mine)
        reply = self.call(sections=[SECTIONS[1]])
        self.assertEqual(reply["status"], "failed")
        self.assertEqual(reply["errors"],
                         "Error: Nothing written to pkm/vault/journals/weekly/2026-W43.md:\n"
                         "- '🌱 Next week's focus' already has text:\n    * My own focus\n"
                         "Show the user that text, then call again with the text to keep and replace=true.")
        self.assertEqual(self.note(), mine)

    def test_replace_overwrites_and_returns_the_old_text(self):
        self.weekly("2026-W43", WEEKLY_TEMPLATE_NOTE.replace("* A\n* B\n* C\n", "* My own focus\n"), day="2026-10-18")
        reply = self.call(sections=[SECTIONS[1]], replace=True)
        self.assertEqual(reply["status"], "ok")
        self.assertIn("- 🌱 Next week's focus: 3 lines, in place of:\n    * My own focus", reply["payload"])
        self.assertNotIn("My own focus", self.note())

    def test_a_heading_the_note_lacks_fails_and_lists_its_headings(self):
        reply = self.call(sections=[{"heading": "Work wins", "text": "* x"}])
        self.assertEqual(reply["status"], "failed")
        self.assertIn("- no heading 'Work wins'\n- the note's headings: 🔍 Weekly Review: Insights & Actions; ",
                      reply["errors"])
        self.assertIsNone(self.note())

    def test_nothing_to_write_is_refused(self):
        reply = self.call(sections=[{"heading": "🏆 Works wins", "text": " "}])
        self.assertEqual(reply["errors"], "Error: Nothing to write: give the sections their text.")
        self.assertIsNone(self.note())

    def test_a_missing_template_fails(self):
        os.remove(os.path.join(self.pkm, "templates", "Weekly Review.md"))
        reply = self.call()
        self.assertEqual(reply["status"], "failed")
        self.assertIn("pkm/templates/Weekly Review.md: not found; the week's note can't be created from it",
                      reply["issues"])
        self.assertIsNone(self.note())

    def test_explain_says_which_week_and_what_was_read(self):
        reply = self.call(explain=True)
        self.assertIn("- pkm/templates/Weekly Review.md (modified ", reply["explain"])
        self.assertIn("week under review: the vault week holding the latest Friday on or before 2026-10-23, from "
                      "2026-10-18; its note is pkm/vault/journals/weekly/2026-W43.md", reply["explain"])

    def test_every_call_writes_so_read_only_runs_refuse_it(self):
        self.assertEqual(tuple(module.READ_ACTIONS), ())
        checked = []

        def read_only(loader, ctx, tool_id, action_name=None, path=None, **kwargs):
            checked.append((tool_id, action_name))
            return action_name is None or action_name in module.READ_ACTIONS  # as scripts/voice_dryrun.py's guard

        with mock.patch.object(ToolsLoader, "check_permission", read_only):
            reply = self.call()
        self.assertEqual(reply["status"], "failed")
        self.assertEqual(reply["errors"], "Error: Agent 'day-planner' does not have permission to 'write' "
                                          "pkm/vault/journals/weekly/2026-W43.md.")
        self.assertEqual(checked, [("write_weekly_review", None), ("write_weekly_review", "write")])
        self.assertIsNone(self.note())

    def test_day_planner_has_it_through_the_weekly_review_skill(self):
        with execution_context(agent_id="day-planner") as ctx:
            loader = ToolsLoader()
            self.assertTrue(loader.check_permission(ctx, "write_weekly_review", "write",
                                                    os.path.join(self.pkm, "vault", "journals", "weekly", "x.md")))
        self.assertIn("does not have permission to use write_weekly_review", self.call(agent_id="meal-planner")["errors"])
