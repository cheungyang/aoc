import os
import sqlite3
import tempfile
import unittest
from datetime import date

from agents.day_planner.lib.task_cache import (
    cache_path,
    display_title,
    open_cache,
    read_day,
    recurrence_rule,
)
from core.storage.tasks import TasksStore
from core.tooling import Report


class TestDisplayTitle(unittest.TestCase):

    def test_link_with_parentheses_in_its_url_keeps_only_its_label(self):
        raw = ("Book [Haircut](https://example.com/salon/(booking/calendar//drawer:"
               "slot/1700000000000)?loc=main&rooms=A-1&durations=60) monthly🔽 ⏳ 2026-10-25 "
               "🔁 every month")
        self.assertEqual(display_title(raw), "Book Haircut monthly")

    def test_metadata_tags_comments_and_dates_go(self):
        raw = ("Pay the water bill every 5th of the month ⏫ 🔁 every month 🛫 2025-10-06 ✅ 2025-12-17 "
               "%% aoc_id 0a1b2c3d4e5f %%")
        self.assertEqual(display_title(raw), "Pay the water bill every 5th of the month")
        raw = "Compare insurance quotes #p/budget #a/quick ⏫ 📅 2026-12-01 [link](https://ticktick.com/webapp/#p/x)"
        self.assertEqual(display_title(raw), "Compare insurance quotes")

    def test_unclosed_link_and_wiki_links(self):
        raw = "$10 movie credit monthly, see the [offers page](https://rewards.example.com/offers #p/budget ⏫"
        self.assertEqual(display_title(raw), "$10 movie credit monthly, see the offers page")
        self.assertEqual(display_title("Chat with [[Mentoring notes Sam|Sam]] about [[2025-12-08]]"),
                         "Chat with Sam about 2025-12-08")

    def test_nested_brackets_in_a_label(self):
        raw = "Read [Project Title [go/proj] - Docs](https://docs.google.com/x_(y)) today"
        self.assertEqual(display_title(raw), "Read Project Title [go/proj] - Docs today")

    def test_long_titles_are_cut(self):
        self.assertEqual(display_title("word " * 40, limit=20), "word word word word…")


class TestRecurrenceRule(unittest.TestCase):

    def test_reads_the_rule_up_to_the_next_marker(self):
        self.assertEqual(recurrence_rule("Pay the water bill ⏫ 🔁 every month  📅 2026-10-15"), "every month")
        self.assertEqual(recurrence_rule("Check designs 🔁 every 2 weeks on Wednesday ⏳ 2024-05-01"),
                         "every 2 weeks on wednesday")
        self.assertEqual(recurrence_rule("Plain task 📅 2026-10-15"), "")


class TestReadOnly(unittest.TestCase):
    """The caches open through the stores' read_only()."""

    def test_missing_cache_raises_instead_of_being_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tasks.db")
            with self.assertRaises(FileNotFoundError):
                with TasksStore(path).read_only():
                    pass
            self.assertFalse(os.path.exists(path))

    def test_cannot_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a b.db")  # a space, to exercise the URI quoting
            setup = sqlite3.connect(path)
            setup.execute("CREATE TABLE t (x)")
            setup.commit()
            setup.close()
            with TasksStore(path).read_only() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM t").fetchone()[0], 0)
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("INSERT INTO t VALUES (1)")
                self.assertEqual(cache_path(conn), os.path.realpath(path))


class TestOpenCache(unittest.TestCase):

    def test_a_missing_cache_is_an_issue_and_isnt_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tasks.db")
            report = Report()
            with open_cache(TasksStore(path), report) as conn:
                self.assertIsNone(conn)
            self.assertEqual((report.status, report.issue_lines()), ("partial", [f"{path}: not found"]))
            report = Report()
            with open_cache(TasksStore(path), report, required=True) as conn:
                self.assertIsNone(conn)
            self.assertEqual(report.status, "failed")
            self.assertFalse(os.path.exists(path))

    def test_a_cache_is_open_for_the_block_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TasksStore(os.path.join(tmp, "tasks.db"))
            store.init()
            report = Report()
            with open_cache(store, report) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
            self.assertEqual(report.issues, [])
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")


class TestReadDay(unittest.TestCase):

    def test_real_dates_only(self):
        self.assertEqual(read_day("2026-10-16"), date(2026, 10, 16))
        self.assertIsNone(read_day("2026-02-30"))
        self.assertIsNone(read_day(""))
        self.assertIsNone(read_day(None))


if __name__ == "__main__":
    unittest.main()
