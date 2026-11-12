"""The daily journals: where they live, which files are daily journals, and when a day has an entry."""
import os
import unittest
from datetime import date
from unittest import mock

from core.tooling import Report
from agents.work_intel.lib import journals
from agents.work_intel.lib.journals import (
    daily_journals,
    has_entry,
    journal_path,
    journals_folder,
    read_journal,
    recent_journals,
)
from tests.agents.work_intel import JournalsCase, journal_text

MON, TUE, WED, THU = date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)


class TestHasEntry(unittest.TestCase):

    def test_text_under_the_front_matter_is_an_entry(self):
        self.assertTrue(has_entry(journal_text(THU, "# Daily briefing\n- Beta shipped.\n")))

    def test_the_front_matter_alone_is_not(self):
        for text in (journal_text(THU), journal_text(THU, "\n  \n\t\n"),
                     journal_text(THU).replace("\n", "\r\n"), "\ufeff" + journal_text(THU)):
            with self.subTest(text=text):
                self.assertFalse(has_entry(text))

    def test_without_front_matter_any_text_is_an_entry(self):
        self.assertTrue(has_entry("Beta shipped.\n"))
        self.assertFalse(has_entry(""))
        self.assertFalse(has_entry(" \n\n"))

    def test_front_matter_that_never_closes_is_read_as_text(self):
        self.assertTrue(has_entry("---\njournal: work\n"))


class TestJournalFiles(JournalsCase):

    def test_the_folder_and_a_days_path_are_in_the_configured_pkm(self):
        self.assertEqual(journals_folder(), self.folder)
        self.assertEqual(journal_path(THU), self.path("2026-11-12.md"))

    def test_read_journal_is_none_when_there_is_no_file(self):
        self.assertIsNone(read_journal(self.path("2026-11-12.md")))
        self.write_journal(THU)
        self.assertIn("journal-date: 2026-11-12", read_journal(self.path("2026-11-12.md")))

    def test_only_files_named_for_a_date_are_daily_journals_newest_first(self):
        for day in (TUE, THU, WED):
            self.write_journal(day)
        self.write("weekly_rollups.md", "# Week 45\n")
        self.write("2026-11-12 notes.md", "scratch\n")
        self.write("2026-13-01.md", "no such date\n")
        self.write(".2026-11-08.md", "hidden\n")
        os.makedirs(self.path("2026-11-07.md"))  # a folder, not a file
        found = daily_journals(self.folder)
        self.assertEqual([journal.day for journal in found], [THU, WED, TUE])
        self.assertEqual(found[0].path, self.path("2026-11-12.md"))
        self.assertEqual(found[0].modified, os.path.getmtime(self.path("2026-11-12.md")))

    def test_recent_journals_pass_over_front_matter_only_ones_and_stop_at_the_count(self):
        for day in (MON, TUE, WED):
            self.write_journal(day)
        self.write("2026-11-12.md", journal_text(THU))  # opened from the template, nothing pasted yet
        report = Report()
        self.assertEqual([journal.day for journal in recent_journals(self.folder, 2, report)], [WED, TUE])
        self.assertEqual([journal.day for journal in recent_journals(self.folder, 9, report)], [WED, TUE, MON])
        self.assertEqual(report.issues, [])

    def test_a_journal_that_cant_be_read_is_an_issue_and_passed_over(self):
        self.write_journal(TUE)
        locked = self.write_journal(WED)
        real_read = journals.read_journal

        def read(path):
            if path == locked:
                raise PermissionError("denied")
            return real_read(path)

        report = Report()
        with mock.patch.object(journals, "read_journal", side_effect=read):
            recent = recent_journals(self.folder, 3, report)
        self.assertEqual([journal.day for journal in recent], [TUE])
        self.assertEqual(report.issue_lines(),
                         ["pkm/vault/journals/work/2026-11-11.md: can't be read (PermissionError: denied)"])


if __name__ == "__main__":
    unittest.main()
