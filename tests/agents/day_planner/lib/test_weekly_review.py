import os
from datetime import date
from unittest import mock

from agents.day_planner.lib.note_sections import SectionError
from agents.day_planner.lib.weekly_review import describe, review_week, save_review
from core.tooling import DataProblem, Report
from tests.agents.day_planner import WEEKLY_TEMPLATE, WEEKLY_TEMPLATE_NOTE, PlanningCase

WEEK_43 = date(2026, 10, 18)  # Sunday
FRIDAY = date(2026, 10, 23)
REVIEW = [("🏆 Works wins", "* Shipped Project A"), ("🌱 Next week's focus", "* Project A\n* Project B\n* Rest")]


class TestReviewWeek(PlanningCase):

    def test_friday_and_saturday_review_the_current_week(self):
        self.assertEqual([review_week(date(2026, 10, d)) for d in (23, 24)], [WEEK_43, WEEK_43])

    def test_sunday_to_thursday_review_the_week_before(self):
        self.assertEqual([review_week(date(2026, 10, d)) for d in (25, 26, 29)], [WEEK_43] * 3)
        self.assertEqual(review_week(date(2026, 10, 22)), date(2026, 10, 11))

    def test_across_new_year(self):
        """Friday, January 1, 2027 is in the week from Sunday, December 27."""
        self.assertEqual(review_week(date(2027, 1, 2)), date(2026, 12, 27))


class TestSaveReview(PlanningCase):

    def setUp(self):
        super().setUp()
        self.template = self.write("templates/Weekly Review.md", WEEKLY_TEMPLATE)
        self.note = os.path.join(self.pkm, "vault", "journals", "weekly", "2026-W43.md")

    def save(self, sections=REVIEW, replace=False, report=None):
        return save_review(self.pkm, WEEK_43, sections, replace, report or Report())

    def read(self):
        with open(self.note, encoding="utf-8") as f:
            return f.read()

    def test_a_missing_note_is_the_template_with_the_plugins_keys_and_the_review(self):
        saved = self.save()
        expected = (WEEKLY_TEMPLATE
                    .replace("hide-embedded-header \n---", "hide-embedded-header \njournal: weekly\njournal-date: 2026-10-18\n---")
                    .replace("### 🏆 Works wins\n", "### 🏆 Works wins\n* Shipped Project A\n")
                    .replace("* A\n* B\n* C\n", "* Project A\n* Project B\n* Rest\n"))
        self.assertEqual(self.read(), expected)
        self.assertIn('<% tp.date.now("gggg-[W]ww", -1, tp.file.title, "gggg-[W]ww") %>', self.read())
        self.assertEqual((saved.created, saved.keys_added), (True, False))
        self.assertEqual(oct(os.stat(self.note).st_mode & 0o777), "0o644")

    def test_the_plugins_note_is_filled_and_keeps_the_rest(self):
        written = self.weekly("2026-W43", WEEKLY_TEMPLATE_NOTE, day="2026-10-18")
        os.chmod(written, 0o600)
        saved = self.save([("🌱 Next week's focus", "* Project A")])
        self.assertEqual(self.read(), WEEKLY_TEMPLATE_NOTE.replace("{day}", "2026-10-18").replace("* A\n* B\n* C\n",
                                                                                               "* Project A\n"))
        self.assertEqual((saved.created, saved.keys_added), (False, False))
        self.assertEqual(oct(os.stat(self.note).st_mode & 0o777), "0o600")

    def test_a_note_without_the_plugins_keys_gets_them(self):
        """Without them the plugin would add its template under the note: the double frontmatter."""
        self.weekly("2026-W43", "# Weekly Review\n\n### 🌱 Next week's focus\n")
        saved = self.save([("Next week's focus", "* Project A")])
        self.assertEqual(self.read(), "---\njournal: weekly\njournal-date: 2026-10-18\n---\n# Weekly Review\n\n"
                                      "### 🌱 Next week's focus\n* Project A\n")
        self.assertTrue(saved.keys_added)

    def test_a_refused_section_writes_nothing(self):
        self.weekly("2026-W43", WEEKLY_TEMPLATE_NOTE.replace("* A\n* B\n* C\n", "* Mine\n"), day="2026-10-18")
        before = self.read()
        with self.assertRaisesRegex(SectionError, "already has text"):
            self.save([("🌱 Next week's focus", "* Project A")])
        self.assertEqual(self.read(), before)

    def test_a_missing_template_fails_and_writes_nothing(self):
        os.remove(self.template)
        report = Report()
        with self.assertRaises(DataProblem):
            self.save(report=report)
        self.assertEqual(report.issue_lines(), ["pkm/templates/Weekly Review.md: not found; the week's note can't be "
                                                "created from it"])
        self.assertFalse(os.path.exists(self.note))

    def test_a_note_that_isnt_utf8_is_left_alone(self):
        os.makedirs(os.path.dirname(self.note), exist_ok=True)
        with open(self.note, "wb") as f:
            f.write(b"### \xff Next week's focus\n")
        report = Report()
        with self.assertRaises(DataProblem):
            self.save(report=report)
        self.assertEqual(report.issue_lines(), ["pkm/vault/journals/weekly/2026-W43.md: isn't UTF-8 text, so it's "
                                                "left as it is"])
        with open(self.note, "rb") as f:
            self.assertEqual(f.read(), b"### \xff Next week's focus\n")

    def test_a_failed_write_leaves_the_note_whole(self):
        self.weekly("2026-W43", WEEKLY_TEMPLATE_NOTE, day="2026-10-18")
        before = self.read()
        with mock.patch("agents.day_planner.lib.weekly_review.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.save([("🌱 Next week's focus", "* Project A")])
        self.assertEqual(self.read(), before)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.note))), ["2026-W43.md"])

    def test_explain_lists_what_was_read(self):
        report = Report(explain=True)
        self.save(report=report)
        self.assertEqual([read.source for read in report.reads], ["pkm/templates/Weekly Review.md"])


class TestDescribe(PlanningCase):

    def setUp(self):
        super().setUp()
        self.write("templates/Weekly Review.md", WEEKLY_TEMPLATE)

    def test_the_note_the_week_and_each_section(self):
        saved = save_review(self.pkm, WEEK_43, REVIEW + [("🏆 Family wins", "")], False, Report())
        self.assertEqual(describe(saved, FRIDAY),
                         "Saved in pkm/vault/journals/weekly/2026-W43.md (the week of Sun 10-18 to Sat 10-24), "
                         "a new note from the weekly template:\n"
                         "- 🏆 Works wins: 1 line\n"
                         "- 🌱 Next week's focus: 3 lines\n"
                         "- 🏆 Family wins: nothing to write")

    def test_replaced_text_comes_back(self):
        save_review(self.pkm, WEEK_43, REVIEW, False, Report())
        saved = save_review(self.pkm, WEEK_43, [("Works wins", "* Shipped Project B")], True, Report())
        self.assertEqual(describe(saved, FRIDAY).splitlines()[1:], ["- 🏆 Works wins: 1 line, in place of:",
                                                                    "    * Shipped Project A"])
        self.assertIn("), the existing note:", describe(saved, FRIDAY))
