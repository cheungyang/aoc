"""What in a coding manifest needs Elephant, and how the findings say it.

Pure functions over a loaded manifest. Every case builds a made-up manifest in
memory: the repo is public, so nothing here comes from a real queue.
"""
import unittest

from agents.software_planner.lib.coding_queue import (
    HALT,
    KINDS,
    REVIEW_BUDGET,
    REVIEW_DORMANT,
    TICK_ERROR,
    ago,
    describe,
    dormant_event,
    findings,
    queue_items,
    waiting_behind,
)

HOUR = 3600
RULES = {"remind_after_hours": 24, "max_fixes_per_task": 2}


def task(task_id="feat_01_list", **fields):
    entry = {"task_id": task_id, "status": "queued", "stage": "queued", "dependencies": [], "attempts": {}}
    entry.update(fields)
    return entry


def halted(task_id="feat_01_list", kind="verification", at=1000.0, **fields):
    entry = task(task_id, status="halted", stage="provisioned", attempts={"implement": 3},
                 last_error={"kind": kind, "stage": "verify", "message": "exit 1: 2 tests failed", "at": at})
    entry.update(fields)
    return entry


def items(*queue, now=10 * HOUR, rules=RULES, **manifest_fields):
    return queue_items({"queue": list(queue), **manifest_fields}, "recipe-box", now, rules)


class TestHalts(unittest.TestCase):

    def test_a_halted_task_is_a_halt_with_its_last_error(self):
        [item] = items(halted(pr_url="https://github.com/example/recipe-box/pull/4",
                              spec_path="pkm/wiki/software/recipe-box/specs/List_v1.md"))
        self.assertEqual((item.kind, item.name, item.event_at), (HALT, "recipe-box/feat_01_list", 1000.0))
        self.assertEqual((item.stage, item.attempts, item.error), ("provisioned", "implement 3", "verification at verify"))
        self.assertEqual(item.message, "exit 1: 2 tests failed")
        self.assertEqual(item.pr_url, "https://github.com/example/recipe-box/pull/4")
        self.assertEqual(item.spec_path, "pkm/wiki/software/recipe-box/specs/List_v1.md")

    def test_pending_review_feedback_on_an_llm_halt_is_a_review_budget(self):
        [item] = items(halted(kind="llm", review_feedback=["@reviewer: sort by date"]))
        self.assertEqual(item.kind, REVIEW_BUDGET)
        self.assertEqual(item.feedback, ("@reviewer: sort by date",))

    def test_feedback_on_a_failing_test_stays_a_halt(self):
        """The failing test is the news: pending feedback alone doesn't explain it."""
        [item] = items(halted(kind="verification", review_feedback=["@reviewer: sort by date"]))
        self.assertEqual(item.kind, HALT)

    def test_a_halt_without_a_last_error_dates_from_its_last_write(self):
        [item] = items(task(status="halted", updated_at=2000.0))
        self.assertEqual(item.event_at, 2000.0)
        self.assertIn("last error: none recorded", item.details(now=3000.0))

    def test_its_fixes_count_against_the_limit(self):
        [once] = items(halted(planner_fixes={"count": 1, "log": ["..."]}))
        [twice] = items(halted(planner_fixes={"count": 2, "log": ["...", "..."]}))
        self.assertEqual((once.fixes, once.at_fix_limit), (1, False))
        self.assertEqual((twice.fixes, twice.at_fix_limit), (2, True))
        self.assertIn("fixes so far: 1 of 2", once.details(now=2000.0))
        self.assertIn("fix limit reached: 2 of 2", twice.details(now=2000.0))

    def test_a_malformed_fix_record_counts_as_none(self):
        for record in ("twice", {"count": "2"}, {"count": True}, {"count": -1}, None):
            with self.subTest(record=record):
                [item] = items(halted(planner_fixes=record))
                self.assertEqual(item.fixes, 0)

    def test_long_feedback_is_clipped_and_counted(self):
        comments = ["x" * 500] + [f"@reviewer: point {n}" for n in range(7)]
        [item] = items(halted(kind="llm", review_feedback=comments))
        self.assertEqual(len(item.feedback), 6)
        self.assertEqual(len(item.feedback[0]), 300)
        self.assertTrue(item.feedback[0].endswith("…"))
        self.assertEqual(item.feedback[-1], "... and 3 more")

    def test_done_failed_blocked_queued_and_active_tasks_need_nothing(self):
        quiet = [task("a", status="done"), task("b", status="failed"), task("c", status="blocked"),
                 task("d", status="queued"), task("e", status="active", lease_owner="nas:tick_1")]
        self.assertEqual(items(*quiet), [])


class TestTickError(unittest.TestCase):

    def test_the_manifests_tick_error_comes_first(self):
        found = items(halted(), tick_error={"stage": "repo", "message": "could not clone example/recipe-box",
                                            "at": 500.0})
        self.assertEqual([item.kind for item in found], [TICK_ERROR, HALT])
        self.assertEqual((found[0].name, found[0].stage, found[0].event_at), ("recipe-box", "repo", 500.0))

    def test_an_empty_or_malformed_tick_error_is_ignored(self):
        for error in (None, "broken", {"stage": "repo", "message": "  "}):
            with self.subTest(error=error):
                self.assertEqual(items(tick_error=error), [])


class TestDormantReviews(unittest.TestCase):

    def review(self, poll_until):
        return task("feat_02_page", status="awaiting_review", stage="awaiting_review", poll_until=poll_until,
                    pr_url="https://github.com/example/recipe-box/pull/5")

    def test_a_review_still_polled_or_recently_stopped_needs_nothing(self):
        self.assertEqual(items(self.review(None)), [])
        self.assertEqual(items(self.review(0.0), now=23 * HOUR), [])

    def test_a_review_the_tick_stopped_checking_is_raised_with_a_reopen_value(self):
        [item] = items(self.review(0.0), now=30 * HOUR)
        self.assertEqual((item.kind, item.event_at), (REVIEW_DORMANT, 24 * HOUR))
        self.assertEqual(item.reopen_until, 31 * HOUR)
        self.assertIn("reopen value: poll_until 111600 (an hour from this check)", item.details(now=30 * HOUR))

    def test_it_falls_due_again_every_remind_after_hours(self):
        self.assertEqual(dormant_event(poll_until=0.0, now=50 * HOUR, every_hours=24), 48 * HOUR)
        self.assertEqual(dormant_event(poll_until=0.0, now=71 * HOUR, every_hours=24), 48 * HOUR)
        self.assertEqual(dormant_event(poll_until=0.0, now=72 * HOUR, every_hours=24), 72 * HOUR)

    def test_zero_turns_reminders_off(self):
        self.assertEqual(items(self.review(0.0), now=500 * HOUR, rules={**RULES, "remind_after_hours": 0}), [])


class TestWaitingBehind(unittest.TestCase):

    def test_dependents_and_theirs_wait(self):
        queue = [task("a", status="halted"), task("b", dependencies=["a"]), task("c", dependencies=["b"]),
                 task("d", dependencies=["x"])]
        self.assertEqual(waiting_behind(queue, "a"), ("b", "c"))

    def test_only_queued_tasks_wait(self):
        queue = [task("a", status="halted"), task("b", status="halted", dependencies=["a"]),
                 task("c", status="pending", dependencies=["a"])]
        self.assertEqual(waiting_behind(queue, "a"), ("c",))


class TestFindings(unittest.TestCase):

    def test_new_items_in_full_then_still_open_ones_in_a_line(self):
        new = items(halted(), task("feat_02_page", dependencies=["feat_01_list"]))
        old = items(halted("feat_03_menu", kind="environment", at=2 * HOUR,
                           planner_fixes={"count": 1, "log": ["..."]}))
        self.assertEqual(findings(new, old, now=10 * HOUR).splitlines(), [
            "New since your last check (1):",
            "1. recipe-box/feat_01_list · halt",
            "   halted at stage provisioned 9 h ago · attempts: implement 3",
            "   last error (verification at verify): exit 1: 2 tests failed",
            "   waiting behind it: feat_02_page",
            "   fixes so far: 0 of 2",
            "",
            "Still open from before (1):",
            "- recipe-box/feat_03_menu · halt · halted 8 h ago (environment at verify) · fixes 1 of 2",
        ])

    def test_a_tick_error_says_where_ticks_stop(self):
        [item] = items(tick_error={"stage": "preflight", "message": "the push identity can't push", "at": 0.0})
        self.assertEqual(item.details(now=5 * HOUR), [
            "ticks stop at preflight before claiming any task, since 5 h ago:",
            "the push identity can't push",
        ])
        self.assertEqual(item.summary(now=5 * HOUR),
                         "recipe-box · tick_error · since 5 h ago (preflight): the push identity can't push")

    def test_the_reason_names_the_first_items_and_counts_the_rest(self):
        new = items(*[halted(f"feat_0{n}") for n in range(1, 6)])
        self.assertEqual(describe(new), "5 new: recipe-box/feat_01 halt; recipe-box/feat_02 halt; "
                                        "recipe-box/feat_03 halt and 2 more")

    def test_the_kinds(self):
        self.assertEqual(KINDS, (HALT, REVIEW_BUDGET, TICK_ERROR, REVIEW_DORMANT))


class TestAgo(unittest.TestCase):

    def test_minutes_then_hours_then_days(self):
        self.assertEqual([ago(s) for s in (5, 40 * 60, 5 * HOUR, 47 * HOUR, 49 * HOUR, -3)],
                         ["1 min", "40 min", "5 h", "47 h", "2 d", "1 min"])


if __name__ == "__main__":
    unittest.main()
