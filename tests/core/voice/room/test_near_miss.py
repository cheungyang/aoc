"""The near-miss resolver: one small model call for "take me to exercise"."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.voice.room import near_miss
from core.voice.room.focus import FocusDirectory
from core.voice.room.near_miss import closest_target, parse_choice, target_lines


class _Loader:
    """Literal agent configs, with the two lookups graphs/main's router makes."""

    def __init__(self, configs):
        self.configs = configs

    def list_agent_ids(self):
        return list(self.configs)

    def get_agent_config(self, agent_id):
        return self.configs.get(agent_id)

    def hosts_channel(self, agent_id, channel):
        return channel in (self.configs.get(agent_id) or {}).get("channel_hosts", [])

    def eligible_agents_for_channel(self, channel, exclude_agent_id=None):
        return sorted(a for a, c in self.configs.items()
                      if a != exclude_agent_id and channel in c.get("channels", []) and "*" not in c["channels"])


DIRECTORY = FocusDirectory.build(_Loader({
    "main": {"name": "Concierge", "channels": ["*"], "tools": {"agent_call": {}},
             "channel_hosts": ["general", "day-planning", "fitness-coach", "aoc-debug"]},
    "day-planner": {"name": "Daisy", "channels": ["day-planning", "general"],
                    "description": "Plans your day."},
    "fitness-coach": {"name": "Frankie", "channels": ["fitness-coach", "general"],
                      "description": "Curates and tracks home workouts."},
}), voice_host="main")


class TestPrompt(unittest.TestCase):

    def test_each_target_is_one_line_with_who_answers_and_what_they_do(self):
        lines = target_lines(DIRECTORY).splitlines()
        self.assertEqual(lines, [
            "aoc-debug | the concierge | #aoc-debug | the aoc debug channel",
            "day-planning | day planner Daisy | #day-planning | Plans your day.",
            "fitness-coach | fitness coach Frankie | #fitness-coach | Curates and tracks home workouts.",
            "general | the concierge | #general | routes you to agents, runs jobs",
        ])


class TestParseChoice(unittest.TestCase):

    def test_a_known_key_however_it_is_wrapped(self):
        for reply in ("fitness-coach", " Fitness-Coach\n", "`fitness-coach`", '"fitness-coach".',
                      [{"type": "text", "text": "fitness-coach"}]):
            with self.subTest(reply=reply):
                self.assertEqual(parse_choice(reply, DIRECTORY), "fitness-coach")

    def test_anything_else_is_none(self):
        for reply in ("none", "None.", "frankie", "I think fitness-coach", "", None, 42):
            with self.subTest(reply=reply):
                self.assertIsNone(parse_choice(reply, DIRECTORY))


class TestClosestTarget(unittest.IsolatedAsyncioTestCase):

    def _patches(self, ainvoke):
        llm = MagicMock()
        llm.ainvoke = ainvoke
        return (
            patch("core.runtime.llm_retry.RetryingChatGoogleGenerativeAI", return_value=llm),
            patch("core.runtime.token_usage_handler.TokenUsageHandler"),
            patch("core.runtime.session_manager.SessionManager.get_session"),
        )

    async def test_returns_the_key_the_model_names(self):
        ainvoke = AsyncMock(return_value=SimpleNamespace(content="fitness-coach"))
        p1, p2, p3 = self._patches(ainvoke)
        with p1 as llm_cls, p2, p3 as get_session:
            self.assertEqual(await closest_target("exercise", DIRECTORY), "fitness-coach")
        prompt = ainvoke.await_args.args[0][0].content
        self.assertIn('"exercise"', prompt)
        self.assertIn("fitness coach Frankie", prompt)
        # A small model, booked to the voice host's voice work, outside any conversation.
        self.assertIn("lite", llm_cls.call_args.kwargs["model"])
        self.assertEqual(get_session.call_args.args[0], "main")
        self.assertEqual(get_session.call_args.kwargs.get("surface"), "voice")
        self.assertIs(get_session.call_args.kwargs.get("record_memory"), False)

    async def test_none_when_the_model_says_none(self):
        p1, p2, p3 = self._patches(AsyncMock(return_value=SimpleNamespace(content="none")))
        with p1, p2, p3:
            self.assertIsNone(await closest_target("the airport on friday", DIRECTORY))

    async def test_none_when_the_call_fails(self):
        p1, p2, p3 = self._patches(AsyncMock(side_effect=RuntimeError("503")))
        with p1, p2, p3:
            self.assertIsNone(await closest_target("exercise", DIRECTORY))

    async def test_none_when_the_call_is_too_slow(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
            return SimpleNamespace(content="fitness-coach")

        p1, p2, p3 = self._patches(slow)
        with p1, p2, p3, patch.object(near_miss, "RESOLVE_TIMEOUT_S", 0.01):
            self.assertIsNone(await closest_target("exercise", DIRECTORY))


if __name__ == "__main__":
    unittest.main()
