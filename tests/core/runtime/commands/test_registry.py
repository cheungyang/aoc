"""CommandRegistry, and the three registries: which commands, in which order."""
import unittest

from core.runtime.commands.base import CommandContext, SpokenCommand, TypedCommand
from core.runtime.commands.registry import CONTROL, SPOKEN, TYPED, CommandRegistry


class _Typed(TypedCommand):
    def __init__(self, name, aliases=()):
        self.name, self.aliases, self.calls = name, aliases, []

    async def run(self, ctx, args):
        self.calls.append(args)


class _Spoken(SpokenCommand):
    """Recognizes `phrase`; answers it if `answers`."""

    def __init__(self, name, phrase, answers=True):
        self.name, self.phrase, self.answers, self.calls = name, phrase, answers, []

    def match(self, transcript):
        return transcript if transcript == self.phrase else None

    async def run(self, ctx, args):
        self.calls.append(args)
        return self.answers


class TestCommandRegistry(unittest.IsolatedAsyncioTestCase):

    def test_commands_are_found_by_name_or_alias_in_the_order_registered(self):
        echo, other = _Typed("echo", ("say",)), _Typed("other")
        registry = CommandRegistry([echo, other])
        self.assertIs(registry.find("echo"), echo)
        self.assertIs(registry.find("say"), echo)
        self.assertIsNone(registry.find("nope"))
        self.assertEqual(list(registry), [echo, other])
        self.assertEqual(registry.match("!say hi"), (echo, "hi"))
        self.assertIsNone(registry.match("hello"))

    def test_a_name_is_taken_once(self):
        registry = CommandRegistry([_Typed("echo", ("say",))])
        with self.assertRaises(ValueError):
            registry.register(_Typed("echo"))
        with self.assertRaises(ValueError):
            registry.register(_Typed("clash", ("say",)))

    async def test_answer_runs_the_first_command_that_answers(self):
        declines, answers = _Spoken("a", "hello", answers=False), _Spoken("b", "hello")
        registry = CommandRegistry([declines, answers])
        self.assertIs(await registry.answer("hello", CommandContext()), answers)
        self.assertEqual((declines.calls, answers.calls), (["hello"], ["hello"]))
        self.assertIsNone(await registry.answer("goodbye", CommandContext()))

    async def test_a_command_not_answered_here_is_skipped(self):
        echo = _Typed("echo")
        echo.available = lambda ctx: False
        self.assertIsNone(await CommandRegistry([echo]).answer("!echo", CommandContext()))
        self.assertEqual(echo.calls, [])


class TestTheRegistries(unittest.TestCase):

    def test_typed(self):
        self.assertEqual([c.name for c in TYPED], ["new", "newall", "compact", "restart", "jobs", "job", "kill",
                                                   "python", "signals", "followups", "join", "leave", "inbox",
                                                   "enroll", "voiceprint", "gate", "help"])
        self.assertIs(TYPED.find("summarize"), TYPED.find("compact"))

    def test_spoken_in_the_order_they_are_tried(self):
        """A stop first; "next agent", "where am I" and "go back" before the
        switch, which would otherwise take them for a name."""
        self.assertEqual([c.name for c in SPOKEN], [
            "stop", "wait", "next agent", "where am I", "go back", "switch",
        ])

    def test_control(self):
        self.assertEqual([c.name for c in CONTROL], ["claim", "release", "status"])

    def test_every_command_has_a_summary(self):
        for command in (*TYPED, *SPOKEN, *CONTROL):
            self.assertTrue(command.summary, command.name)
        for command in SPOKEN:
            self.assertTrue(command.examples and command.outcome, command.name)


if __name__ == "__main__":
    unittest.main()
