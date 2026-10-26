import os
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from core.config.settings import Config
from tests.agents.day_planner import NEW_DAILY_NOTE, PlanningCase
from tests.helpers import envelope, execution_context
from agents.day_planner.tools.day_briefing import day_briefing

NOW = datetime(2026, 10, 16, 12, 5, tzinfo=ZoneInfo("America/Los_Angeles"))


class TestDayBriefingTool(PlanningCase):

    def setUp(self):
        super().setUp()
        patcher = mock.patch("agents.day_planner.tools.day_briefing.get_local_now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, **args):
        with execution_context(agent_id="day-planner"):
            return envelope(day_briefing.invoke(args))

    def test_needs_an_agent_run(self):
        reply = envelope(day_briefing.invoke({}))
        self.assertEqual(reply["status"], "failed")
        self.assertIn("no active execution context", reply["errors"])

    def test_returns_the_briefing_with_the_new_note_text(self):
        reply = self.call()
        self.assertEqual((reply["status"], reply["issues"], reply["errors"]), ("ok", "None", "None"))
        self.assertTrue(reply["payload"].startswith("Fri 2026-10-16 · ISO week 2026-W42"))
        self.assertIn("Today's note:\npkm/vault/journals/fleeting/2026-10-16.md: not created yet. New-note text",
                      reply["payload"])

    def test_the_new_note_is_the_template_with_the_journal_keys_and_its_templater_syntax(self):
        payload = self.call()["payload"]
        self.assertIn("~~~markdown\n---\nenergy_level:\njournal: fleeting\njournal-date: 2026-10-16\n---\n"
                      ">[!tip]+ ![[2026-W<% tp.date.now(\"w\")-1 %>#🌱 Next week's focus]]\n", payload)

    def test_a_note_still_the_template_is_one_line(self):
        self.daily(NOW.date(), NEW_DAILY_NOTE)
        reply = self.call()
        self.assertEqual(reply["status"], "ok")
        self.assertIn("\n\nToday's note: pkm/vault/journals/fleeting/2026-10-16.md: created from the template, "
                      "nothing written yet\n\n", reply["payload"])
        self.assertNotIn("New-note text", reply["payload"])

    def test_a_missing_template_is_partial(self):
        os.remove(os.path.join(self.pkm, "templates", "Fleeting Pages.md"))
        reply = self.call()
        self.assertEqual(reply["status"], "partial")
        self.assertIn("- pkm/templates/Fleeting Pages.md: not found; today's note can't be created from it",
                      reply["issues"])
        self.assertIn("not created yet; its template pkm/templates/Fleeting Pages.md is missing", reply["payload"])

    def test_a_missing_cache_costs_only_its_sections(self):
        os.remove(Config().projects_db_path)
        reply = self.call()
        self.assertEqual(reply["status"], "partial")
        self.assertIn("projects.db: not found", reply["issues"])
        self.assertIn("with their top open tasks: unavailable (", reply["payload"])
        self.assertIn("Recurring chores:\nOverdue: none", reply["payload"])

    def test_chore_lead_days_comes_from_rules_md(self):
        self.task("- [ ] Water the plants 🔁 every week 📅 2026-10-21")  # 5 days away, no history
        self.assertIn("Quiet until their usual window: 1 (next: Water the plants, from Sun 10-18)",
                      self.call()["payload"])
        self.write("agents/day-planner/RULES.md", "- chore_lead_days: 5\n")
        reply = self.call()
        self.assertEqual(reply["status"], "ok")
        self.assertIn("Due soon:\n- Water the plants: due in 5 days (Wed 10-21); no completion history, "
                      "so flagged 5 days ahead", reply["payload"])

    def test_a_rules_line_that_doesnt_read_is_reported_and_the_default_applies(self):
        self.write("agents/day-planner/RULES.md", "- chore_lead_days: soon\n")
        reply = self.call()
        self.assertEqual(reply["status"], "partial")
        self.assertIn("pkm/agents/day-planner/RULES.md: line 1: 'chore_lead_days: soon' can't be read", reply["issues"])
        self.assertIn("using the default 3", reply["issues"])

    def test_explain_lists_the_reads_and_how_numbers_were_reached(self):
        self.daily(date(2026, 10, 15))
        self.task("- [ ] Water the plants 🔁 every week 📅 2026-10-17")
        reply = self.call(explain=True)
        self.assertIn("- pkm/vault/journals/fleeting/2026-10-15.md (modified ", reply["explain"])
        self.assertIn("- pkm/templates/Fleeting Pages.md (modified ", reply["explain"])
        self.assertIn("vault week 2026-W42: weeks start on Sunday", reply["explain"])
        self.assertIn("Water the plants: due 2026-10-17; no completion history, so chore_lead_days (3) "
                      "→ opens 2026-10-14; soon on 2026-10-16", reply["explain"])
        self.assertNotIn("explain", self.call())

    def test_a_failure_is_reported(self):
        with mock.patch("agents.day_planner.tools.day_briefing.build_briefing", side_effect=RuntimeError("boom")):
            reply = self.call()
        self.assertEqual(reply["status"], "failed")
        self.assertEqual(reply["errors"], "Error in day_briefing: RuntimeError: boom")
