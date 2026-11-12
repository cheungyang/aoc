"""journal_updates: the scan that gates work-intel's 16:30 sparring session."""
import json
import os
import unittest
from datetime import date

from core.scheduler.preconditions import PreconditionError, build_precondition
from core.scheduler.spec import Findings, Problems, PromptSchedule, ScheduleContext
from tests.agents.work_intel import JournalsCase, at, journal_text
from agents.work_intel.preconditions.journal_updates import JournalUpdates

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
MON, TUE, WED, THU, FRI = (date(2026, 11, day) for day in (9, 10, 11, 12, 13))
LAST = at(WED, 16, 30)      # Wednesday's sparring session
SPARRING = at(THU, 16, 30)  # Thursday's run
SINCE = "since the last sparring session (Wed 2026-11-11 16:30)"


class TestDeclaration(unittest.TestCase):

    def test_the_16_30_sparring_declares_it(self):
        from core.scheduler.registry import load_agent_schedules

        with open(os.path.join(ROOT, "agents", "work-intel", "agent.json"), encoding="utf-8") as f:
            config = json.load(f)
        result = load_agent_schedules("work-intel", config)
        self.assertEqual(result.rejections, [])
        spec = {s.schedule_id: s for s in result.specs}["work-intel:journal-sparring"]
        self.assertEqual(spec.cron, "30 16 * * 1-5")
        self.assertIsInstance(spec.precondition, JournalUpdates)

    def test_other_agents_cannot(self):
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "journal_updates"}, agent_id="day-planner")


class TestJournalUpdates(JournalsCase):

    def evaluate(self, last_success_at=LAST, now=SPARRING):
        ctx = ScheduleContext(schedule_id="work-intel:journal-sparring", agent_id="work-intel",
                              now=now, last_success_at=last_success_at)
        return JournalUpdates({"type": "journal_updates"}).evaluate(ctx)

    def test_todays_journal_wakes_the_sparring_with_the_last_three(self):
        for day in (MON, TUE, WED, THU):
            self.write_journal(day)
        decision = self.evaluate()
        self.assertTrue(decision)
        self.assertEqual(decision.reason, f"1 recent journal(s) new {SINCE}: 2026-11-12")
        self.assertEqual(decision.detail, Findings("journal_updates", "\n".join([
            f"The 3 most recent daily journals (lookback_days: 3), newest first. (new) marks one new or updated {SINCE}:",
            "- pkm/vault/journals/work/2026-11-12.md (new)",
            "- pkm/vault/journals/work/2026-11-11.md",
            "- pkm/vault/journals/work/2026-11-10.md",
        ])))

    def test_nothing_new_since_the_last_session_skips(self):
        for day in (TUE, WED):
            self.write_journal(day)
        decision = self.evaluate()
        self.assertFalse(decision)
        self.assertEqual(decision.reason, f"no recent journal new {SINCE}")
        self.assertIsNone(decision.detail)

    def test_an_update_pasted_after_the_last_session_counts(self):
        # Wednesday's update went in at 18:00, after Wednesday's session; nothing for Thursday yet.
        self.write_journal(TUE)
        self.write_journal(WED, mtime=at(WED, 18))
        decision = self.evaluate()
        self.assertTrue(decision)
        self.assertIn("- pkm/vault/journals/work/2026-11-11.md (new)", decision.detail.text)

    def test_an_edit_to_a_journal_older_than_the_lookback_does_not_count(self):
        for day in (TUE, WED, THU):
            self.write_journal(day)
        self.write_journal(MON, mtime=at(FRI, 9))  # a typo fixed in Monday's, outside the last three
        self.assertFalse(self.evaluate(last_success_at=at(THU, 16, 30), now=at(FRI, 16, 30)))

    def test_a_journal_with_only_its_front_matter_neither_counts_nor_crowds_out(self):
        for day in (MON, TUE, WED):
            self.write_journal(day)
        self.write("2026-11-12.md", journal_text(THU), mtime=at(THU, 9))  # opened from the template
        self.assertFalse(self.evaluate())
        self.write_journal(THU, mtime=at(THU, 10))  # the update is pasted
        decision = self.evaluate()
        self.assertEqual(decision.detail.text.splitlines()[1:], [
            "- pkm/vault/journals/work/2026-11-12.md (new)",
            "- pkm/vault/journals/work/2026-11-11.md",
            "- pkm/vault/journals/work/2026-11-10.md",
        ])

    def test_weekly_rollups_and_other_files_never_fire_it(self):
        self.write_journal(WED)
        self.write("weekly_rollups.md", "# Week 45\n- Beta shipped.\n", mtime=at(THU, 9))
        self.write("2026-11-12 notes.md", "scratch\n", mtime=at(THU, 9))
        self.assertFalse(self.evaluate())

    def test_before_the_first_session_every_recent_journal_is_new(self):
        for day in (TUE, WED):
            self.write_journal(day)
        decision = self.evaluate(last_success_at=None)
        self.assertTrue(decision)
        self.assertEqual(decision.reason,
                         "2 recent journal(s) new since the last sparring session (none yet): 2026-11-11, 2026-11-10")
        self.assertEqual(decision.detail.text.splitlines(), [
            "The 2 most recent daily journals (lookback_days: 3), newest first. "
            "(new) marks one new or updated since the last sparring session (none yet):",
            "- pkm/vault/journals/work/2026-11-11.md (new)",
            "- pkm/vault/journals/work/2026-11-10.md (new)",
        ])

    def test_no_journal_yet_skips_quietly(self):
        decision = self.evaluate(last_success_at=None)
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "no daily journal with an entry in pkm/vault/journals/work")
        self.assertIsNone(decision.detail)

    def test_lookback_days_comes_from_rules_md(self):
        for day in (TUE, WED, THU):
            self.write_journal(day)
        self.write_rules("- lookback_days: 1\n")
        decision = self.evaluate()
        self.assertTrue(decision)
        self.assertIn("(lookback_days: 1)", decision.detail.text)
        self.assertEqual(decision.detail.text.splitlines()[1:], ["- pkm/vault/journals/work/2026-11-12.md (new)"])

    def test_a_lookback_under_one_is_an_issue_and_the_default_applies(self):
        for day in (TUE, WED, THU):
            self.write_journal(day)
        self.write_rules("- lookback_days: 0\n")
        decision = self.evaluate()
        self.assertTrue(decision)
        self.assertIn("(lookback_days: 3)", decision.detail.text)
        self.assertEqual(decision.detail.issues, (
            "pkm/agents/work-intel/RULES.md: lookback_days is 0, but at least 1 journal must be read; "
            "using the default 3",))

    def test_a_missing_journals_folder_is_posted(self):
        os.rmdir(self.folder)
        decision = self.evaluate()
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "no journals folder to check (with issues)")
        self.assertEqual(decision.detail, Problems(
            "journal_updates", ("pkm/vault/journals/work: folder not found, so new journal entries can't be checked",)))

    def test_the_sparring_starts_from_the_findings(self):
        self.write_journal(THU)
        spec = PromptSchedule(schedule_id="work-intel:journal-sparring", agent_id="work-intel",
                              cron="30 16 * * 1-5", prompt="Spar on what's new.",
                              precondition=JournalUpdates({"type": "journal_updates"}))
        ctx = ScheduleContext(schedule_id=spec.schedule_id, agent_id="work-intel", now=SPARRING,
                              last_success_at=LAST)
        prompt = spec.build_prompt(ctx, spec.has_work(ctx))
        self.assertTrue(prompt.startswith("Spar on what's new.\n\n<findings source=\"journal_updates\">"))
        self.assertIn("- pkm/vault/journals/work/2026-11-12.md (new)", prompt)


if __name__ == "__main__":
    unittest.main()
