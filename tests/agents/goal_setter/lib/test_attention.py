import unittest
from datetime import date

from core.tooling import Report
from tests.agents.goal_setter.lib.coaching_fixture import PROJECTS, RULES, TODAY, CoachingPkm
from agents.goal_setter.lib.attention import (
    ProjectNotFound,
    assess,
    find_project,
    is_resting,
    project_context,
    projects_needing_attention,
    rank_projects,
)
from agents.goal_setter.lib.snapshot import Snapshot
from agents.goal_setter.lib.velocity import task_stats

GUITAR = "Guitar lessons"
RUN = "Finish a 10k by EoY 2026"
SPANISH = "Spanish lessons"
ALPHA = "Project Alpha launch"


class TestRanking(unittest.TestCase):

    def setUp(self):
        self.pkm = CoachingPkm()
        self.addCleanup(self.pkm.cleanup)
        self.snapshot = self.pkm.snapshot()

    def rank(self, report=None, **rules):
        return rank_projects(self.snapshot, TODAY, dict(RULES, **rules), report or Report())

    def scores(self, **rules):
        return [(a.project["name"], a.score) for a in self.rank(**rules)[0]]

    def test_order_reasons_and_resting(self):
        report = Report()
        ranked, resting = self.rank(report)
        self.assertEqual(
            [(a.project["name"], a.score, a.rank) for a in ranked],
            [
                (GUITAR, 7, 2),
                (RUN, 6, 2),
                (SPANISH, 6, 99),  # lapsed: ties lose to this year's priorities
            ],
        )
        self.assertEqual(ranked[1].reasons, ("last coached 23 days ago", "stalled: nothing done in 105 days, 2 open"))
        self.assertEqual(ranked[1].points, ("due_again 2", "stalled 2", "⏫ 2"))
        self.assertEqual(resting, [ALPHA])
        self.assertEqual(report.status, "ok")

    def test_reasons_are_facts_and_a_lapsed_commitment_adds_no_points(self):
        spanish = self.rank()[0][2]
        self.assertEqual(spanish.reasons, (
            "never coached", "stalled: nothing done in 39 days, 1 open", "1 overdue task", "committed for 2025, not 2026",
        ))
        self.assertEqual(spanish.points, ("never_coached 3", "stalled 2", "overdue 1"))

    def test_public_shape(self):
        result = projects_needing_attention(TODAY, RULES, self.snapshot, Report(), limit=2)
        self.assertEqual(result["executing"], 4)
        self.assertEqual([p["name"] for p in result["projects"]], [GUITAR, RUN])
        self.assertEqual(result["more"], [SPANISH])
        self.assertEqual(result["coached_in_last_7d"], [ALPHA])
        self.assertEqual(result["projects"][1], {
            "name": RUN, "priority": "⏫", "score": 6,
            "reasons": ["last coached 23 days ago", "stalled: nothing done in 105 days, 2 open"],
            "last_coached": "2026-09-23", "open_tasks": 2, "done_last_30d": 0,
        })

    def test_weigh_stalled_projects_more(self):
        self.assertEqual(self.scores(stalled=3), [(GUITAR, 8), (RUN, 7), (SPANISH, 7)])

    def test_the_day_thresholds(self):
        self.assertEqual(self.scores(coach_every_days=23)[1], (RUN, 6))  # 23 days: due again
        self.assertEqual(self.scores(coach_every_days=24)[1:], [(SPANISH, 6), (RUN, 4)])  # not yet
        self.assertEqual(self.scores(long_gap_days=23)[0:2], [(GUITAR, 7), (RUN, 7)])  # a long gap: 3, not 2
        self.assertEqual(self.scores(stall_days=39)[2], (SPANISH, 6))  # 39 days idle: stalled
        self.assertEqual(self.scores(stall_days=40), [(GUITAR, 7), (RUN, 6), (SPANISH, 4)])  # not yet

    def test_rest_days(self):
        _, resting = self.rank(rest_days=3)
        self.assertEqual(resting, [])  # Alpha, coached 5 days ago, no longer rests, and nothing else calls for it
        self.assertEqual([a.project["name"] for a in self.rank(rest_days=24)[0]], [GUITAR, SPANISH])  # the 10k rests too

    def test_priority_points(self):
        self.assertEqual(self.scores(priority_points={"🔺": 3, "⏫": 5, "🔼": 1})[0], (GUITAR, 10))
        # A variation selector after the sign (U+FE0F, from an emoji keyboard) names the same sign.
        self.assertEqual(self.scores(priority_points={"🔺": 3, "⏫\ufe0f": 5})[0], (GUITAR, 10))

    def test_a_priority_points_entry_that_names_no_priority_is_reported(self):
        self.pkm.use_as_config()
        report = Report()
        self.rank(report, priority_points={"🔺": 3, "⏫": 2, "high": 9})
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.issue_lines(), [
            "pkm/agents/goal-setter/RULES.md: priority_points: high is not a priority (🔺 ⏫ 🔼 🔽 ⏬); ignored",
        ])

    def test_explain_accounts_for_every_executing_project(self):
        report = Report(explain=True)
        self.rank(report)
        self.assertEqual(report.traces, [  # in the store's order: this year's commitments, by priority, then name
            "Project Alpha launch: rests, last coached 2026-10-11, 5 days ago (rest_days 7)",
            "Finish a 10k by EoY 2026: 6 = due_again 2 + stalled 2 + ⏫ 2 "
            "(last coached 2026-09-23; last dated completion 2026-07-03)",
            "Guitar lessons: 7 = never_coached 3 + stalled 2 + ⏫ 2 "
            "(last coached never; last dated completion none)",
            "Spanish lessons: 6 = never_coached 3 + stalled 2 + overdue 1 "
            "(last coached never; last dated completion 2026-09-07)",
        ])

    def test_a_task_date_that_cant_be_read_comes_back_raw(self):
        bad = {"id": "g9", "title": "Book the recital", "status": "todo", "tags": ["p/music"], "due_date": "2026-02-30",
               "source": "ticktick/Inbox.md", "raw_line": "- [ ] Book the recital 📅 2026-02-30 #p/music"}
        snapshot = Snapshot(self.snapshot.projects, self.snapshot.tasks + [bad], self.snapshot.pkm_dir)
        report = Report()
        rank_projects(snapshot, TODAY, RULES, report)
        self.assertEqual(report.status, "partial")
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].lines, ["- [ ] Book the recital 📅 2026-02-30 #p/music"])
        self.assertTrue(report.issues[0].source.endswith("ticktick/Inbox.md"))

    def test_no_open_tasks(self):
        project = {"name": "Empty", "status": "executing", "priority_rank": 1, "commitment_year": 2026}
        attention = assess(project, task_stats([], TODAY), None, TODAY, RULES)
        self.assertEqual(attention.reasons, ("never coached", "no open tasks"))
        self.assertEqual(attention.score, 3 + 2 + 3)

    def test_a_busy_recently_coached_project_needs_nothing(self):
        project = {"name": "Busy", "status": "executing", "priority_rank": 1, "commitment_year": 2026}
        tasks = [{"title": "next", "status": "todo"}, {"title": "done", "status": "completed", "completed_date": "2026-10-10"}]
        self.assertIsNone(assess(project, task_stats(tasks, TODAY), date(2026, 10, 6), TODAY, RULES))

    def test_resting_is_fewer_than_rest_days(self):
        self.assertTrue(is_resting(date(2026, 10, 10), TODAY, RULES))  # 6 days
        self.assertFalse(is_resting(date(2026, 10, 9), TODAY, RULES))  # 7 days
        self.assertFalse(is_resting(None, TODAY, RULES))


class TestProjectContext(unittest.TestCase):

    def setUp(self):
        self.pkm = CoachingPkm()
        self.addCleanup(self.pkm.cleanup)
        self.snapshot = self.pkm.snapshot()

    def context(self, name, report=None):
        return project_context(name, TODAY, self.snapshot, report or Report())

    def test_everything_in_one_read(self):
        report = Report()
        context = self.context("10k", report)
        self.assertEqual(context["project"], {
            "name": "Finish a 10k by EoY 2026", "status": "executing", "priority": "⏫", "commitment_year": 2026,
            "category": "🏃 Health", "alias": "#p/10k", "note": "pkm/vault/projects/Finish a 10k by June 2026.md",
            "started": "2025-03-01", "last_reviewed": "2026-09-23",
        })
        self.assertEqual(context["coaching"]["last_coached"], "2026-09-23")
        self.assertEqual(context["coaching"]["dossier"], "pkm/wiki/project-coaching/Finish a 10k by June 2026.md")
        tasks = context["tasks"]
        self.assertEqual((tasks["open"], tasks["completed"], tasks["dropped"]), (2, 2, 1))
        self.assertEqual(tasks["last_completed"]["days_ago"], 105)
        self.assertNotIn("completed_without_date", tasks)
        self.assertEqual(context["open_tasks"], ["🔼 Run a 5k time trial", "Easy runs: 3x weekly"])
        self.assertNotIn("recently_completed", context)
        self.assertEqual(report.status, "ok")

    def test_velocity_and_recent_wins(self):
        context = self.context("Project Alpha launch")
        self.assertEqual(context["tasks"]["velocity"]["done_last_30d"], 2)
        self.assertEqual(context["tasks"]["velocity"]["done_prior_30d"], 1)
        self.assertEqual(context["tasks"]["velocity"]["trend"], "up")
        self.assertEqual(context["recently_completed"], ["2026-10-08 Follow up with Pat on the spec", "2026-10-02 Draft the rollout plan"])
        self.assertEqual(context["open_tasks"], ["🔺 Write the launch checklist (due 2026-10-22)"])

    def test_a_completion_without_a_date_is_counted_and_named(self):
        tasks = self.context("guitar")["tasks"]
        self.assertEqual((tasks["completed"], tasks["completed_without_date"]), (1, 1))
        self.assertEqual(tasks["velocity"]["done_last_90d"], 0)

    def test_explain_gives_the_inputs_behind_the_numbers(self):
        report = Report(explain=True)
        self.context("10k", report)
        self.assertEqual(report.traces, [
            "last coached 2026-09-23: the later of the dossier's last_coached (2026-09-23) and the note's latest "
            "session (2026-09-23); 23 days before 2026-10-16",
            "velocity: 2 of 2 completed tasks have a date; done in the last 7/30/90 days 0/0/0, the 30 days before 0; "
            "per week 0 x 7 / 90 = 0.0",
            "overdue: 0 of 2 open tasks were due before 2026-10-16; 0 are due by 2026-10-30",
        ])
        self.assertEqual([read.source.rsplit("/", 1)[-1] for read in report.reads[-3:]],
                         ["project-coaching", "Finish a 10k by June 2026.md", "Finish a 10k by June 2026.md"])

    def test_a_note_that_is_gone_is_partial(self):
        import os

        os.remove(os.path.join(self.pkm.pkm_dir, "vault/projects/Guitar lessons.md"))
        report = Report()
        context = self.context("guitar", report)
        self.assertEqual(report.status, "partial")
        self.assertIn("can't be read (No such file or directory); its coaching sessions are unknown",
                      report.issue_lines()[0])
        self.assertIsNone(context["coaching"]["last_coached"])


class TestFindProject(unittest.TestCase):

    def test_ways_to_name_a_project(self):
        for name in ("finish a 10K by eoy 2026", "4c36ea3c2444", "Finish a 10k by June 2026", "#p/10k", "p/10k", "10k"):
            with self.subTest(name=name):
                self.assertEqual(find_project(name, PROJECTS)["id"], "4c36ea3c2444")

    def test_a_unique_part_or_any_status(self):
        self.assertEqual(find_project("guitar", PROJECTS)["name"], "Guitar lessons")
        self.assertEqual(find_project("podcast", PROJECTS)["status"], "paused")

    def test_spoken_names_ignore_spaces_and_punctuation(self):
        self.assertEqual(find_project("10 k", PROJECTS)["id"], "4c36ea3c2444")
        self.assertEqual(find_project("Alpha-launch", PROJECTS)["id"], "8150fa0422fa")

    def test_ambiguous_and_unknown_names_list_the_candidates(self):
        with self.assertRaisesRegex(ProjectNotFound, "matches several projects: Guitar lessons, Spanish lessons"):
            find_project("lessons", PROJECTS)
        with self.assertRaisesRegex(ProjectNotFound, "No project matches 'running goal'. Executing projects: Finish a 10k"):
            find_project("running goal", PROJECTS)
        with self.assertRaises(ProjectNotFound):
            find_project("  ", PROJECTS)


if __name__ == "__main__":
    unittest.main()
