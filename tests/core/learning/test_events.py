"""The runtime's hooks into the learning loop (core/learning/events.py): what each one logs,
that it writes off the caller's thread, and that it can never fail what it's hooked into."""
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.learning import events
from core.loaders.tools_loader import ToolsLoader
from core.runtime.commands.base import SpokenCommand, SpokenContext, TypedContext
from core.runtime.commands.registry import CommandRegistry
from core.runtime.foundation.sessions import SessionManager
from core.storage.learning import COMMAND, REACTION, REFUSAL, SPEECH_FALLBACK, LearningStore


class _Observing(unittest.TestCase):
    """The hooks on (the suite turns them off), writing to this test's memory.db."""

    def setUp(self):
        patcher = patch.dict(os.environ, {"LEARNING_OBSERVE": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = LearningStore()

    def logged(self, kind):
        events.flush()
        return self.store.events(kind, 0)


class TestRecord(_Observing):

    def test_a_row_is_written_on_the_worker_thread(self):
        threads = []
        events.submit(lambda store: threads.append(threading.current_thread().name))
        events.record(COMMAND, agent_id="main", name="kill", detail={"args": "3"})
        [row] = self.logged(COMMAND)
        self.assertEqual((row["agent_id"], row["name"], row["detail"]), ("main", "kill", {"args": "3"}))
        self.assertTrue(threads[0].startswith("learning-events"))

    def test_nothing_is_written_with_the_switch_off(self):
        with patch.dict(os.environ, {"LEARNING_OBSERVE": "0"}):
            events.record(COMMAND, name="kill")
            events.record_speech_fallback("main")
        self.assertEqual(self.logged(COMMAND) + self.logged(SPEECH_FALLBACK), [])

    def test_a_write_that_fails_is_dropped_and_the_next_one_still_lands(self):
        events.submit(lambda store: 1 / 0)
        events.record_speech_fallback("day-planner")
        self.assertEqual([r["agent_id"] for r in self.logged(SPEECH_FALLBACK)], ["day-planner"])

    def test_reactions_and_refusals_keep_what_the_detectors_read(self):
        events.record_reaction("day-planner", "👎", message_id=77, channel_id=5, channel="day-planning")
        ctx = SimpleNamespace(agent_id="home-steward", channel_name="home", session_id="home-steward:discord:home")
        events.record_refusal(ctx, "home_assistant", "turn_on", "light.porch")
        [reaction], [refusal] = self.logged(REACTION), self.logged(REFUSAL)
        self.assertEqual((reaction["name"], reaction["channel"], reaction["detail"]),
                         ("👎", "day-planning", {"message_id": "77", "channel_id": "5"}))
        self.assertEqual((refusal["agent_id"], refusal["name"], refusal["session_id"], refusal["detail"]),
                         ("home-steward", "home_assistant", "home-steward:discord:home",
                          {"action": "turn_on", "path": "light.porch"}))


class _Focus:
    """A voice room whose focus a command can move."""

    def __init__(self, agent_id, channel_name):
        self.where = SimpleNamespace(agent_id=agent_id, channel_name=channel_name)
        self.agent_id = "main"

    def focus(self):
        return self.where


class _Phrase(SpokenCommand):
    """Recognizes "go back". One that answers moves the room back to #general, as GoBackCommand does."""

    def __init__(self, name, answers):
        self.name, self.aliases, self.answers = name, (), answers

    def match(self, text):
        return "" if text == "go back" else None

    async def run(self, ctx, args):
        if self.answers:
            ctx.voice.where = SimpleNamespace(agent_id="main", channel_name="general")
        return self.answers


class TestCommandLog(_Observing):

    def test_where_a_typed_command_was_given(self):
        ctx = TypedContext(channel=SimpleNamespace(name="day-planning"), agent_id="day-planner")
        self.assertEqual(events.command_origin(ctx), ("day-planner", "day-planning"))

    def test_where_a_spoken_command_was_given_is_the_rooms_focus(self):
        ctx = SpokenContext(voice=_Focus("reward-travel", "travel"), heard="go back")
        self.assertEqual(events.command_origin(ctx), ("reward-travel", "travel"))

    def test_the_origin_never_raises(self):
        voice = MagicMock()
        voice.focus.side_effect = RuntimeError("no room")
        self.assertEqual(events.command_origin(SpokenContext(voice=voice, heard="stop")), ("", ""))

    def test_the_command_that_answered_is_logged_where_it_was_given(self):
        import asyncio

        registry = CommandRegistry([_Phrase("declines", answers=False), _Phrase("go back", answers=True)],
                                   kind="spoken")
        ctx = SpokenContext(voice=_Focus("reward-travel", "travel"), heard="go back")
        self.assertEqual(asyncio.run(registry.answer("go back", ctx)).name, "go back")
        [row] = self.logged(COMMAND)
        self.assertEqual((row["name"], row["agent_id"], row["channel"]), ("go back", "reward-travel", "travel"),
                         "where the room was before the command moved it")
        self.assertEqual(row["detail"], {"registry": "spoken", "args": "", "heard": "go back"})

    def test_text_no_command_answers_logs_nothing(self):
        import asyncio

        registry = CommandRegistry([_Phrase("declines", answers=False)], kind="spoken")
        self.assertIsNone(asyncio.run(registry.answer("go back", SpokenContext(voice=_Focus("a", "b"), heard="go back"))))
        self.assertEqual(self.logged(COMMAND), [])


class TestRefusals(_Observing):

    def setUp(self):
        super().setUp()
        self.ctx = SessionManager.get_session(agent_id="main", source="discord", channel="general")

    def check(self, allowed, *args, **kwargs):
        with patch.object(ToolsLoader, "_permitted", return_value=allowed):
            return ToolsLoader().check_permission(self.ctx, *args, **kwargs)

    def test_a_refused_action_is_logged(self):
        self.assertFalse(self.check(False, "filesystem", "write", "wiki/plan.md"))
        [row] = self.logged(REFUSAL)
        self.assertEqual((row["agent_id"], row["name"], row["detail"]),
                         ("main", "filesystem", {"action": "write", "path": "wiki/plan.md"}))

    def test_probes_tool_level_checks_and_allowed_actions_are_not(self):
        self.assertFalse(self.check(False, "home_assistant", "turn_on", "light.porch", probe=True))
        self.assertFalse(self.check(False, "graph_call"))
        self.assertTrue(self.check(True, "filesystem", "read", "wiki/plan.md"))
        self.assertEqual(self.logged(REFUSAL), [])


class TestThumbsDown(_Observing):

    def runner(self):
        return SimpleNamespace(agent_id="day-planner",
                               bot=SimpleNamespace(user=SimpleNamespace(id=42),
                                                   get_channel=lambda channel_id: SimpleNamespace(name="day-planning")))

    def payload(self, **overrides):
        values = dict(member=SimpleNamespace(bot=False), message_author_id=42, message_id=77, channel_id=5,
                      emoji="👎")
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_your_thumbs_down_on_this_bots_message(self):
        from core.channel.discord.runner import BotRunner

        BotRunner._note_thumbs_down(self.runner(), self.payload(), "👎")
        [row] = self.logged(REACTION)
        self.assertEqual((row["agent_id"], row["name"], row["channel"]), ("day-planner", "👎", "day-planning"))

    def test_not_logged(self):
        from core.channel.discord.runner import BotRunner

        for name, payload in {"another bot's message": self.payload(message_author_id=43),
                              "a bot's reaction": self.payload(member=SimpleNamespace(bot=True)),
                              "a DM": self.payload(member=None)}.items():
            with self.subTest(name):
                BotRunner._note_thumbs_down(self.runner(), payload, "👎")
        self.assertEqual(self.logged(REACTION), [])

    def test_any_skin_tone_goes_to_the_thumbs_down_note_and_no_further(self):
        import asyncio
        from core.channel.discord.runner import BotRunner

        runner = SimpleNamespace(_note_thumbs_down=MagicMock())
        asyncio.run(BotRunner.on_raw_reaction_add(runner, self.payload(emoji="👎🏽")))
        runner._note_thumbs_down.assert_called_once()
        self.assertEqual(runner._note_thumbs_down.call_args[0][1], "👎🏽")


if __name__ == "__main__":
    unittest.main()
