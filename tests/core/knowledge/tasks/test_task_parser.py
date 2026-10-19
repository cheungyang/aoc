import unittest
from core.knowledge.tasks.parser import (
    is_task_line,
    parse_task_line,
    clean_title,
    extract_tags,
    extract_priority,
    extract_dates,
    extract_id,
    append_aoc_id,
)


class TestTaskParser(unittest.TestCase):

    def test_is_task_line(self):
        self.assertTrue(is_task_line("- [ ] Todo task"))
        self.assertTrue(is_task_line("- [x] Done task"))
        self.assertTrue(is_task_line("- [X] Done capital task"))
        self.assertTrue(is_task_line("- [-] Dropped task"))
        self.assertTrue(is_task_line("  * [ ] Indented asterisk task"))
        self.assertTrue(is_task_line("\t- [ ] Tab indented task"))
        self.assertFalse(is_task_line("Regular markdown line"))
        self.assertFalse(is_task_line("- Regular bullet point"))
        self.assertFalse(is_task_line("### Header"))

    def test_clean_title(self):
        # Line with wrapped doc link, tag, ticktick link, comment, and priority
        raw = "[Team Handbook - Docs](https://docs.google.com/123) #a/learn [link](https://ticktick.com/456) #ticktick %%[ticktick_id:: abc]%% 🔼"
        cleaned = clean_title(raw)
        self.assertEqual(cleaned, "Team Handbook - Docs")

        # Line with dates and comments
        raw2 = "Review quarterly roadmap ⏫ ⏳ 2026-02-20 📅 2026-02-28 %% aoc_id def123 %%"
        cleaned2 = clean_title(raw2)
        self.assertEqual(cleaned2, "Review quarterly roadmap")

        # Line with completed date and drop date
        raw3 = "Cancel old subscription #p/finance 🔽 ❌ 2026-03-01"
        cleaned3 = clean_title(raw3)
        self.assertEqual(cleaned3, "Cancel old subscription")

        # Line with nested brackets in title
        raw4 = "- [ ] [Project X Primer [go/x-primer] - Google Slides](https://docs.google.com/123) #p/aoc ⏫"
        cleaned4 = clean_title(raw4)
        self.assertEqual(cleaned4, "- [ ] Project X Primer [go/x-primer] - Google Slides")

    def test_extract_tags(self):
        raw = "Task title #a/learn #p/aoc #ticktick #urgent #ticktick/sub"
        tags = extract_tags(raw)
        self.assertEqual(tags, ["a/learn", "p/aoc", "urgent"])

    def test_extract_priority(self):
        self.assertEqual(extract_priority("Task with 🔺"), ("🔺", 1))
        self.assertEqual(extract_priority("Task with ⏫"), ("⏫", 2))
        self.assertEqual(extract_priority("Task with 🔼"), ("🔼", 3))
        self.assertEqual(extract_priority("Task with 🔽"), ("🔽", 4))
        self.assertEqual(extract_priority("Task with ⏬"), ("⏬", 5))
        self.assertEqual(extract_priority("Task with no priority"), (None, 99))

    def test_extract_dates(self):
        raw = "Task ⏳ 2026-02-15 📅 2026-02-20 ✅ 2026-02-18 ❌ 2026-02-19 ➕ 2026-02-01"
        dates = extract_dates(raw)
        self.assertEqual(dates["scheduled_date"], "2026-02-15")
        self.assertEqual(dates["due_date"], "2026-02-20")
        self.assertEqual(dates["completed_date"], "2026-02-18")
        self.assertEqual(dates["dropped_date"], "2026-02-19")
        self.assertEqual(dates["created_date"], "2026-02-01")

    def test_extract_id(self):
        # TickTick format
        tt_line = "- [ ] Task [link](https://ticktick.com/...) %%[ticktick_id:: 65f0a1b2c3d4e5f6a7b8c9d0]%%"
        tid, id_type = extract_id(tt_line)
        self.assertEqual(tid, "65f0a1b2c3d4e5f6a7b8c9d0")
        self.assertEqual(id_type, "ticktick")

        # AOC ID format
        aoc_line = "- [ ] Task title %% aoc_id 3c4d5e6f7a8b %%"
        aid, id_type2 = extract_id(aoc_line)
        self.assertEqual(aid, "3c4d5e6f7a8b")
        self.assertEqual(id_type2, "aoc")

        # No ID
        none_id, id_type3 = extract_id("- [ ] Plain task")
        self.assertIsNone(none_id)
        self.assertIsNone(id_type3)

    def test_append_aoc_id(self):
        line = "- [ ] Fix the screen door"
        updated, assigned_id = append_aoc_id(line, new_id="testid123456")
        self.assertEqual(assigned_id, "testid123456")
        self.assertEqual(updated, "- [ ] Fix the screen door %% aoc_id testid123456 %%\n")

        # Line with existing ID should not append
        line_existing = "- [ ] Fix the screen door %% aoc_id existing123 %%\n"
        updated2, id2 = append_aoc_id(line_existing)
        self.assertEqual(id2, "existing123")
        self.assertEqual(updated2, line_existing)

    def test_parse_task_line_complete(self):
        raw = "- [x] [Design PRD - Google Docs](https://docs.google.com/123) #p/aoc #a/write 🔺 📅 2026-03-01 ✅ 2026-02-28 %%[ticktick_id:: tt123]%%"
        task, updated_line = parse_task_line(raw, 10, "ticktick/Inbox.md", "ticktick")

        self.assertIsNotNone(task)
        self.assertIsNone(updated_line)
        self.assertEqual(task["id"], "tt123")
        self.assertEqual(task["title"], "Design PRD - Google Docs")
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["priority"], "🔺")
        self.assertEqual(task["priority_rank"], 1)
        self.assertEqual(task["tags"], ["p/aoc", "a/write"])
        self.assertEqual(task["due_date"], "2026-03-01")
        self.assertEqual(task["completed_date"], "2026-02-28")
        self.assertEqual(task["line_number"], 10)
        self.assertEqual(task["source"], "ticktick/Inbox.md")

    def test_in_progress_tasks_are_open(self):
        self.assertTrue(is_task_line("- [/] Draft the plan"))
        task, _ = parse_task_line("- [/] Draft the plan 📅 2026-10-20", 3, "vault/projects/x.md")
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["title"], "Draft the plan")
        self.assertEqual(task["due_date"], "2026-10-20")

    def test_trailing_urls_leave_the_title(self):
        cases = {
            "Book a checkup https://example.com/book/request-appointment/": "Book a checkup",
            "Ask sam (https://example.com/people/sam)": "Ask sam",
            "Read the newsletter: https://lists.example.com/a/x/c/VX": "Read the newsletter",
            "Pay rent <https://bank.example.com/pay>": "Pay rent",
            "recreate https://reports.example.com/weekly for the new team": "recreate https://reports.example.com/weekly for the new team",
            "https://example.com/blog/how-teams-build-products": "https://example.com/blog/how-teams-build-products",
        }
        for raw, title in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_title(raw), title)
        task, _ = parse_task_line("- [ ] Book a checkup https://example.com/book/request-appointment/", 1, "x.md")
        self.assertEqual(task["raw_title"], "Book a checkup https://example.com/book/request-appointment/")


if __name__ == "__main__":
    unittest.main()
