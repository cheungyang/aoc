from agents.day_planner.lib.project_slate import active_projects, due_tasks, format_projects
from core.storage.projects import ProjectsStore
from core.storage.tasks import TasksStore
from core.tooling import Report
from tests.agents.day_planner import TODAY, PlanningCase

PROJECT_A_FILE = "vault/projects/Project A.md"


class TestActiveProjects(PlanningCase):

    def setUp(self):
        super().setUp()
        self.project("Project A", priority="🔺", rank=1, aliases=["#p/alpha"], category="💼 Work")
        # The sync keeps some tag prefixes in the status (real: 's/✊executing').
        self.project("Project B", status="s/✊executing", aliases=["#p/beta"])
        # Committed to 2026 through the commitments table only; its file name differs from its name.
        self.project("Learn Spanish by EoY 2026", year=2025, aliases=["#p/spanish"], category="🦄 Personal",
                     file_name="Learn Spanish by EoQ3 2026.md", commitments=[2025, 2026])
        self.project("Garden makeover", status="planning", priority="🔽", rank=4,
                     aliases=["#p/garden"], category="🏠 Family")
        # Not active this year.
        self.project("Project C", year=2025, aliases=["#p/gamma"])
        self.project("Lake trip", status="paused", aliases=["#p/lake"])
        self.project("Bike tour", status="considering", aliases=["#p/bike"])

        self.task("- [ ] Draft the spec 🔺 #p/alpha", "vault/journals/fleeting/2026-08-21.md")
        self.task("- [ ] Talk to Pat about the launch plan ⏫ #p/alpha", "vault/journals/fleeting/2026-08-18.md")
        self.task("- [ ] Draft the 2027 roadmap 🔼 📅 2026-10-19", PROJECT_A_FILE)  # in the file, no tag
        self.task("- [ ] Update the Project A tracker 🔼", PROJECT_A_FILE)
        self.task("- [x] Done already ⏫ #p/alpha ✅ 2026-10-01", PROJECT_A_FILE)
        self.task("- [ ] Review Project A metrics 🔁 every week 📅 2026-10-19 #p/alpha", PROJECT_A_FILE)  # a chore
        # The same task kept in two files counts once.
        self.task("- [ ] Get a quote for the raised beds ⏫ #p/garden", "vault/journals/personal/2024-06-10.md")
        self.task("- [ ] Get a quote for the raised beds ⏫ #p/garden", "vault/projects/Garden makeover.md")

    def projects(self, report=None):
        with ProjectsStore().read_only() as p, TasksStore().read_only() as t:
            return active_projects(p, t, TODAY, report or Report())

    def test_this_years_executing_and_planning_projects_by_priority(self):
        self.assertEqual([p.name for p in self.projects()], [
            "Project A",
            "Learn Spanish by EoY 2026",
            "Project B",
            "Garden makeover",
        ])

    def test_top_open_tasks_by_tag_or_file_best_first(self):
        project_a = self.projects()[0]
        self.assertEqual(project_a.open_count, 4)
        self.assertEqual(project_a.top_tasks, [
            "🔺 Draft the spec",
            "⏫ Talk to Pat about the launch plan",
            "🔼 Draft the 2027 roadmap: due in 3 days (Mon 10-19)",
        ])
        garden = self.projects()[-1]
        self.assertEqual((garden.open_count, garden.top_tasks), (1, ["⏫ Get a quote for the raised beds"]))

    def test_format(self):
        lines = format_projects(self.projects()).splitlines()
        self.assertEqual(lines[0], "🔺 Project A · 💼 Work · executing · #p/alpha · 4 open")
        self.assertIn("⏫ Learn Spanish by EoY 2026 · 🦄 Personal · executing · #p/spanish · 0 open · "
                      "file: Learn Spanish by EoQ3 2026.md", lines)
        self.assertEqual(lines[-1], "Project files are pkm/vault/projects/<project name>.md unless a file is shown.")

    def test_reads_are_recorded(self):
        report = Report()
        self.projects(report)
        self.assertEqual(report.status, "ok")
        self.assertEqual([read.records for read in report.reads], [6, 5])  # 6 projects this year; 5 open tasks
        self.assertIn("Projects committed to 2026: 6; executing or planning: 4", report.traces)

    def test_a_top_task_whose_date_isnt_real_is_shown_without_it_and_returned_raw(self):
        self.task("- [ ] Fix the date 🔺 #p/alpha 📅 2026-02-30")
        report = Report()
        project_a = self.projects(report)[0]
        self.assertEqual(project_a.top_tasks[0], "🔺 Fix the date")
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.issues[0].lines, ["Fix the date 🔺 #p/alpha 📅 2026-02-30"])


class TestDueTasks(PlanningCase):

    def lines(self, report=None):
        with TasksStore().read_only() as conn:
            return due_tasks(conn, TODAY, report or Report())

    def test_due_within_a_week_or_slipped_soonest_first(self):
        self.task("- [ ] Use the $40 class voucher 🔼 ⏳ 2026-10-01")
        self.task("- [ ] check and pay the internet bill 📅 2026-10-21", "ticktick/Inbox.md")
        self.task("- [ ] File the expense report ⏫ #p/budget 📅 2026-10-16")
        self.task("- [ ] Plan the holiday trip 📅 2026-10-24")  # 8 days out
        self.task("- [ ] Book the dentist ⏳ 2026-10-17")  # scheduled in the future
        self.task("- [x] Already paid 📅 2026-10-15 ✅ 2026-10-15")
        self.task("- [ ] Water the plants 🔁 every week 📅 2026-10-16")  # chores are judged elsewhere
        self.assertEqual(self.lines(), [
            "🔼 Use the $40 class voucher: scheduled 15 days ago (Thu 10-01)",
            "⏫ File the expense report #p/budget: due today",
            "check and pay the internet bill: due in 5 days (Wed 10-21)",
        ])

    def test_long_lists_are_capped_with_a_count(self):
        for n in range(10):
            self.task(f"- [ ] Old task {n} ⏳ 2026-09-{10 + n:02d}")
        lines = self.lines()
        self.assertEqual(len(lines), 9)
        self.assertEqual(lines[-1], "(+2 more)")

    def test_a_date_that_isnt_real_is_left_out_and_returned_raw(self):
        self.task("- [ ] Pay the rent 📅 2026-02-30")
        self.task("- [ ] File the expense report ⏫ 📅 2026-10-16")
        report = Report()
        self.assertEqual(self.lines(report), ["⏫ File the expense report: due today"])
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.issues[0].problem, "1 line not recognized")
        self.assertEqual(report.issues[0].lines, ["Pay the rent 📅 2026-02-30"])
        self.assertEqual([(read.records, read.skipped) for read in report.reads], [(1, 1)])
