import unittest
from unittest.mock import patch

from core.scheduler.spec import (
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
