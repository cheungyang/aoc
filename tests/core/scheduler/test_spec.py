import unittest
from unittest.mock import patch

from core.scheduler.spec import (
    Findings,
    Problems,
    PromptSchedule,
    ScheduleContext,
    ScriptSchedule,
    ScriptStep,
    SessionPolicy,
    WorkDecision,
)


def ctx(**kw):
    base = dict(schedule_id="a:x", agent_id="a", now=100.0)
    base.update(kw)
    return ScheduleContext(**base)


class StubPrecondition:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def evaluate(self, ctx):
        self.calls += 1
        return self.answer


def common(**kw):
    base = dict(schedule_id="a:x", agent_id="a", cron="* * * * *", channel="c")
    base.update(kw)
    return base


class TestSessionPolicy(unittest.TestCase):
    def test_default_is_stateless(self):
        self.assertIs(SessionPolicy.parse(None), SessionPolicy.STATELESS)

    def test_parses_case_insensitively(self):
        self.assertIs(SessionPolicy.parse(" Persistent "), SessionPolicy.PERSISTENT)
        self.assertIs(SessionPolicy.parse(SessionPolicy.PERSISTENT), SessionPolicy.PERSISTENT)

    def test_shared_is_not_a_policy(self):
        with self.assertRaises(ValueError) as cm:
            SessionPolicy.parse("shared")
        self.assertIn("stateless", str(cm.exception))


class TestWorkDecision(unittest.TestCase):
    def test_truthiness_and_unpacking(self):
        decision = WorkDecision(True, "why")
        self.assertTrue(decision)
        has_work, reason = decision
        self.assertEqual((has_work, reason), (True, "why"))
        self.assertFalse(WorkDecision(False))

    def test_coerce_accepts_tuple_bool_and_itself(self):
        self.assertEqual(WorkDecision.coerce((False, "no")), WorkDecision(False, "no"))
        self.assertEqual(WorkDecision.coerce(True), WorkDecision(True, ""))
        same = WorkDecision(True, "x")
        self.assertIs(WorkDecision.coerce(same), same)

    def test_coerce_rejects_other_types(self):
        with self.assertRaises(TypeError):
            WorkDecision.coerce("yes")
        with self.assertRaises(TypeError):
            WorkDecision.coerce(None)


class TestPromptSchedule(unittest.TestCase):
    def test_has_work_delegates_to_precondition(self):
        pre = StubPrecondition(WorkDecision(False, "empty"))
        spec = PromptSchedule(prompt="p", precondition=pre, **common())
        decision = spec.has_work(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "empty")
        self.assertEqual(pre.calls, 1)

    def test_no_precondition_means_work(self):
        spec = PromptSchedule(prompt="p", **common())
        self.assertTrue(spec.has_work(ctx()))

    def test_build_prompt_and_policy(self):
        spec = PromptSchedule(prompt="hello", session_policy="persistent", **common())
        self.assertEqual(spec.build_prompt(ctx()), "hello")
        self.assertIs(spec.session_policy, SessionPolicy.PERSISTENT)
        self.assertIn("[prompt]", spec.describe())
        self.assertIn("#c", spec.describe())

    def test_findings_are_appended_as_a_block(self):
        spec = PromptSchedule(prompt="Scan the market.", **common())
        decision = WorkDecision(True, "r", detail=Findings("market_screen", "CAT -6%\n"))
        self.assertEqual(spec.build_prompt(ctx(), decision),
                         'Scan the market.\n\n<findings source="market_screen">\nCAT -6%\n</findings>')

    def test_other_details_leave_the_prompt_alone(self):
        spec = PromptSchedule(prompt="Scan the market.", **common())
        self.assertEqual(spec.build_prompt(ctx(), WorkDecision(True, "r", detail=["x"])), "Scan the market.")

    def test_findings_pass_on_what_the_scan_couldnt_read(self):
        block = Findings("market_screen", "CAT -6%", ("pkm/wallet/stock/watchlist.md: 'XYZ' has no quote",)).as_block()
        self.assertEqual(block, (
            '<findings source="market_screen">\nCAT -6%\n\n'
            "The scan couldn't read everything; tell the user what's missing in one line "
            "and don't fill the gap with a guess:\n"
            "- pkm/wallet/stock/watchlist.md: 'XYZ' has no quote\n</findings>"
        ))


class TestProblems(unittest.TestCase):
    def test_the_notice_names_the_check_and_each_issue(self):
        problems = Problems("property_screen", ("criteria.md: names no state", "criteria.md: max price unreadable"))
        self.assertEqual(problems.message("The scheduled check `weekly-scout`"), (
            "⚠️ The scheduled check `weekly-scout` couldn't run normally:\n"
            "- criteria.md: names no state\n- criteria.md: max price unreadable\n"
            "Reply here and I'll help fix it."
        ))
        self.assertTrue(problems.message().startswith("⚠️ The property_screen scan couldn't run normally:"))

    def test_the_notice_quotes_the_first_raw_lines_under_their_issue(self):
        problems = Problems(
            "property_screen",
            ("criteria.md: names no state", "criteria.md: 3 lines not recognized"),
            raw=(("criteria.md: 3 lines not recognized", ("Lot: big yard", "Near a park", "Quiet street")),),
        )
        self.assertEqual(problems.message("The scheduled check `weekly-scout`"), (
            "⚠️ The scheduled check `weekly-scout` couldn't run normally:\n"
            "- criteria.md: names no state\n"
            "- criteria.md: 3 lines not recognized\n"
            "> Lot: big yard\n> Near a park\n> ... and 1 more\n"
            "Reply here and I'll help fix it."
        ))

    def test_raw_lines_dont_change_what_counts_as_the_same_problem(self):
        issues = ("criteria.md: 1 line not recognized",)
        self.assertEqual(Problems("property_screen", issues, raw=((issues[0], ("Lot: big yard",)),)),
                         Problems("property_screen", issues))

    def test_findings_quote_the_raw_lines_too(self):
        issue = "pkm/wallet/stock/watchlist.md: 1 line not recognized"
        block = Findings("market_screen", "XYZ -6%", (issue,), raw=((issue, ("* xyz corp",)),)).as_block()
        self.assertIn(f"- {issue}\n> * xyz corp\n</findings>", block)


class TestScriptSchedule(unittest.TestCase):
    def test_step_label(self):
        self.assertEqual(ScriptStep("x.py", ("--a", "1")).label(), "x.py --a 1")

    def test_precondition_false_short_circuits_scripts(self):
        spec = ScriptSchedule(
            steps=[ScriptStep("a.py")],
            precondition=StubPrecondition((False, "gate")),
            **common(),
        )
        with patch("core.scheduler.script_runner.call_has_work") as call:
            decision = spec.has_work(ctx())
        self.assertFalse(decision)
        call.assert_not_called()

    def test_only_steps_with_work_are_selected(self):
        a, b = ScriptStep("a.py"), ScriptStep("b.py")
        spec = ScriptSchedule(steps=[a, b], **common())
        answers = {"a.py": WorkDecision(False, "idle"), "b.py": WorkDecision(True, "busy")}
        with patch(
            "core.scheduler.script_runner.call_has_work",
            side_effect=lambda step, c, scripts_dir=None: answers[step.script],
        ):
            decision = spec.has_work(ctx())
        self.assertTrue(decision)
        self.assertEqual(decision.detail, [b])
        self.assertIn("b.py: busy", decision.reason)
        self.assertEqual(spec.script, "a.py")

    def test_no_step_with_work(self):
        spec = ScriptSchedule(steps=[ScriptStep("a.py")], **common())
        with patch(
            "core.scheduler.script_runner.call_has_work",
            return_value=WorkDecision(False, "idle"),
        ):
            decision = spec.has_work(ctx())
        self.assertFalse(decision)
        self.assertEqual(decision.detail, [])


if __name__ == "__main__":
    unittest.main()
