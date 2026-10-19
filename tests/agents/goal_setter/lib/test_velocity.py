import unittest

from agents.goal_setter.lib.velocity import (
    alias_tags,
    open_task_lines,
    project_tasks,
    recent_completion_lines,
    task_stats,
    unreadable_dates,
)
from tests.agents.goal_setter.lib.coaching_fixture import NOTE_ALPHA, PROJECTS, TASKS, TODAY

RUN, ALPHA, GUITAR = PROJECTS[0], PROJECTS[1], PROJECTS[2]


def done(day, title="t"):
    return {"title": title, "status": "completed", "completed_date": day}


class TestMembership(unittest.TestCase):

    def test_alias_tags_skip_plain_names(self):
        self.assertEqual(alias_tags({"aliases": ["#p/Alpha", "Alpha launch", "#"]}), ["p/alpha"])

    def test_by_tag_subtag_or_note(self):
        self.assertEqual({t["id"] for t in project_tasks(ALPHA, TASKS)}, {"a1", "a2", "a3", "a4"})

    def test_a_longer_tag_is_another_project(self):
        self.assertNotIn("r6", {t["id"] for t in project_tasks(RUN, TASKS)})

    def test_a_note_task_counts_without_the_tag(self):
        untagged = {"id": "x", "status": "todo", "tags": [], "source": NOTE_ALPHA}
        self.assertEqual(project_tasks(ALPHA, [untagged]), [untagged])


class TestTaskStats(unittest.TestCase):

    def test_windows(self):
        tasks = [done("2026-10-15"), done("2026-10-01"), done("2026-09-10"), done("2026-07-20"), done("2026-01-02")]
        stats = task_stats(tasks, TODAY)
        self.assertEqual(
            (stats.done_7d, stats.done_30d, stats.done_prev_30d, stats.done_90d, stats.completed),
            (1, 2, 1, 4, 5),
        )
        velocity = stats.as_dict(TODAY)["velocity"]
        self.assertEqual((velocity["per_week_90d"], velocity["trend"]), (0.3, "up"))
        self.assertEqual(stats.as_dict(TODAY)["last_completed"]["days_ago"], 1)

    def test_trends(self):
        cases = {
            "stalled": [done("2026-07-01")],
            "steady": [done("2026-10-10"), done("2026-09-10")],
            "down": [done("2026-09-10"), done("2026-09-01")],
        }
        for trend, tasks in cases.items():
            with self.subTest(trend=trend):
                self.assertEqual(task_stats(tasks, TODAY).trend, trend)

    def test_open_overdue_due_soon_and_undated_completions(self):
        stats = task_stats(project_tasks(GUITAR, TASKS) + [
            {"title": "a", "status": "todo", "due_date": "2026-10-10"},
            {"title": "b", "status": "todo", "due_date": "2026-10-30"},
        ], TODAY)
        self.assertEqual((stats.open, stats.overdue, stats.due_soon, stats.completed), (4, 1, 1, 1))
        self.assertIsNone(stats.last_done)
        self.assertEqual(stats.undated, 1)
        self.assertNotIn("last_completed", stats.as_dict(TODAY))
        self.assertEqual(stats.as_dict(TODAY)["completed_without_date"], 1)

    def test_future_completion_dates_are_ignored(self):
        self.assertEqual(task_stats([done("2026-10-20")], TODAY).done_7d, 0)

    def test_dates_that_dont_read(self):
        tasks = [
            {"id": "a", "status": "todo", "due_date": "2026-02-30"},
            {"id": "b", "status": "completed", "completed_date": "someday"},
            {"id": "c", "status": "todo", "due_date": "2026-10-20"},
            {"id": "d", "status": "dropped", "due_date": "nope"},  # dropped: its dates count for nothing
        ]
        self.assertEqual([t["id"] for t in unreadable_dates(tasks)], ["a", "b"])
        self.assertEqual(task_stats(tasks, TODAY).undated, 1)


class TestTaskLines(unittest.TestCase):

    def test_open_tasks_most_urgent_first(self):
        tasks = [
            {"title": "later", "status": "todo", "priority": "🔼", "priority_rank": 3},
            {"title": "urgent", "status": "todo", "priority": "🔺", "priority_rank": 1, "due_date": "2026-10-10"},
            {"title": "no priority", "status": "todo", "priority_rank": 99},
            {"title": "done", "status": "completed"},
        ]
        lines, hidden = open_task_lines(tasks, TODAY)
        self.assertEqual(lines, ["🔺 urgent (due 2026-10-10, overdue)", "🔼 later", "no priority"])
        self.assertEqual(hidden, 0)
        self.assertEqual(open_task_lines(tasks, TODAY, limit=1), (["🔺 urgent (due 2026-10-10, overdue)"], 2))

    def test_recent_completions_newest_first(self):
        tasks = [done("2026-10-01", "older"), done("2026-10-09", "newer"), done("2026-08-01", "too old")]
        self.assertEqual(recent_completion_lines(tasks, TODAY), ["2026-10-09 newer", "2026-10-01 older"])


if __name__ == "__main__":
    unittest.main()
