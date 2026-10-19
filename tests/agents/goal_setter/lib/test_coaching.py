import os
import unittest
from datetime import date

from core.tooling import Report
from agents.goal_setter.lib import coaching
from agents.goal_setter.lib.coaching import coaching_history, load_dossiers, note_sessions, read_dossier, read_sessions
from tests.agents.goal_setter.lib.coaching_fixture import NOTE_RUN, PROJECTS, TODAY, CoachingPkm

RUN, ALPHA, GUITAR = PROJECTS[0], PROJECTS[1], PROJECTS[2]


class TestCoachingHistory(unittest.TestCase):

    def setUp(self):
        self.pkm = CoachingPkm()
        self.addCleanup(self.pkm.cleanup)

    def history(self, project, report=None):
        report = report or Report()
        return coaching_history(project, self.pkm.pkm_dir, load_dossiers(self.pkm.pkm_dir, report), report)

    def test_dossier_found_by_its_project_name_when_the_title_changed(self):
        history = self.history(RUN)
        self.assertEqual(history.dossier_path, os.path.join("wiki", "project-coaching", "Finish a 10k by June 2026.md"))
        self.assertEqual(history.dossier.last_coached, date(2026, 9, 23))
        self.assertTrue(history.dossier.strategy.startswith("## Coaching Strategy"))
        self.assertNotIn("**Status:**", history.dossier.strategy)

    def test_last_coached_is_the_later_of_dossier_and_note(self):
        history = self.history(ALPHA)
        self.assertEqual((history.dossier.last_coached, history.session_date), (date(2026, 10, 11), date(2026, 9, 14)))
        self.assertEqual(history.last_coached, date(2026, 10, 11))

    def test_a_logged_session_counts_without_a_dossier(self):
        os.remove(os.path.join(self.pkm.pkm_dir, "wiki", "project-coaching", "Project Alpha launch.md"))
        self.assertEqual(self.history(ALPHA).last_coached, date(2026, 9, 14))

    def test_never_coached_gets_a_dossier_path_named_after_its_note(self):
        history = self.history(GUITAR)
        self.assertIsNone(history.last_coached)
        self.assertEqual(
            history.as_dict(TODAY),
            {"last_coached": None, "days_since_coached": None,
             "dossier": "pkm/wiki/project-coaching/Guitar lessons.md"},
        )

    def test_as_dict(self):
        result = self.history(RUN).as_dict(TODAY)
        self.assertEqual((result["last_coached"], result["days_since_coached"]), ("2026-09-23", 23))
        self.assertEqual(result["last_session"]["date"], "2026-09-23")

    def test_reads_are_recorded(self):
        report = Report()
        self.history(RUN, report)
        self.assertEqual([(os.path.basename(r.source), r.records) for r in report.reads], [
            ("project-coaching", 2), ("Finish a 10k by June 2026.md", None), ("Finish a 10k by June 2026.md", 2),
        ])
        self.assertEqual(report.status, "ok")

    def test_a_dossier_whose_date_doesnt_read_comes_back_raw(self):
        self.pkm.write("wiki/project-coaching/Project Alpha launch.md",
                       "---\nproject_name: Project Alpha launch\nlast_coached: 2026-1O-11\n---\nNotes.\n")
        report = Report()
        history = self.history(ALPHA, report)
        self.assertEqual(history.last_coached, date(2026, 9, 14))  # the note's session still counts
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.issues[0].lines, ["last_coached: 2026-1O-11"])
        self.assertTrue(report.issues[0].source.endswith("project-coaching/Project Alpha launch.md"))

    def test_dossiers_are_keyed_by_project_name_and_file_name(self):
        keys = load_dossiers(self.pkm.pkm_dir, Report())
        self.assertIn("finish a 10k by june 2026", keys)
        self.assertIn("project alpha launch", keys)

    def test_no_dossier_folder(self):
        report = Report()
        self.assertEqual(load_dossiers(os.path.join(self.pkm.root, "elsewhere"), report), {})
        self.assertEqual((report.issues, report.reads), ([], []))


class TestReadDossier(unittest.TestCase):

    def test_frontmatter_and_notes(self):
        dossier = read_dossier("---\nproject_name: Garden makeover\nlast_coached: 2026-10-03\n---\n"
                               "**Status:** executing\n\n## Coaching Strategy\nStart with the raised beds.\n", "d.md")
        self.assertEqual((dossier.project_name, dossier.last_coached), ("Garden makeover", date(2026, 10, 3)))
        self.assertEqual(dossier.strategy, "## Coaching Strategy\nStart with the raised beds.")
        self.assertEqual((dossier.problem, dossier.unreadable), ("", ()))

    def test_a_time_after_the_date_is_dropped(self):
        self.assertEqual(read_dossier("---\nlast_coached: 2026-10-03 09:30:00\n---\n").last_coached, date(2026, 10, 3))

    def test_what_keeps_last_coached_unknown_is_said(self):
        cases = {
            "## Coaching Strategy\nNo frontmatter.\n": ("no frontmatter, so no last_coached date", ()),
            "---\nproject_name: X\n---\nBody\n": ("no last_coached date in its frontmatter", ()),
            "---\nproject_name: X\nlast_coached: soon\n---\n": ("", ("last_coached: soon",)),
            "---\nproject_name: [X\nlast_coached: 2026-10-03\n---\n": ("", ("project_name: [X", "last_coached: 2026-10-03")),
        }
        for text, (problem, unreadable) in cases.items():
            with self.subTest(text=text):
                dossier = read_dossier(text)
                self.assertIsNone(dossier.last_coached)
                self.assertEqual((dossier.problem, dossier.unreadable), (problem, unreadable))


class TestSessions(unittest.TestCase):

    def setUp(self):
        self.pkm = CoachingPkm()
        self.addCleanup(self.pkm.cleanup)

    def test_newest_section_up_to_the_next_heading_without_comments(self):
        sessions = note_sessions(os.path.join(self.pkm.pkm_dir, NOTE_RUN), Report())
        self.assertEqual((sessions.latest, sessions.count), (date(2026, 9, 23), 2))
        self.assertTrue(sessions.notes.startswith("**Shift to the year-end plan**"))
        self.assertTrue(sessions.notes.endswith("#p/10k"))
        self.assertNotIn("aoc_id", sessions.notes)
        self.assertNotIn("Spring", sessions.notes)

    def test_newest_by_date_not_position(self):
        sessions = read_sessions("### 2026-05-01 from Grace 🚀\nold\n### 2026-08-01 from Grace\nnew\n")
        self.assertEqual((sessions.latest, sessions.notes), (date(2026, 8, 1), "new"))

    def test_a_heading_whose_date_doesnt_read_comes_back_raw(self):
        sessions = read_sessions("### 2026-08-01 from Grace\nok\n### 2026-9-30 from Grace\n### 2026/10/04 from Grace 🚀\n"
                                 "## Feedback from Grace\n")
        self.assertEqual(sessions.latest, date(2026, 8, 1))
        self.assertEqual(sessions.unreadable, ("### 2026-9-30 from Grace", "### 2026/10/04 from Grace 🚀"))

    def test_a_missing_note_or_no_sessions(self):
        report = Report()
        self.assertEqual(note_sessions(os.path.join(self.pkm.pkm_dir, "nope.md"), report).latest, None)
        self.assertEqual(report.status, "partial")
        self.assertIn("can't be read (No such file or directory)", report.issue_lines()[0])
        self.assertEqual(read_sessions("# Plain note\n"), coaching.Sessions())

    def test_long_notes_are_cut_at_a_word(self):
        notes = read_sessions("### 2026-08-01 from Grace\n" + "word " * 600).notes
        self.assertLessEqual(len(notes), coaching.NOTES_LIMIT + 2)
        self.assertTrue(notes.endswith("word …"))


if __name__ == "__main__":
    unittest.main()
