"""The near-miss resolver: one small model call for "take me to exercise"."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.prompts.voice import NearMissPrompt
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
                      [{"type": "text", "text": "fitness-coach"}], "<payload>fitness-coach</payload>",
                      "<payload>\n  fitness-coach\n</payload>"):
            with self.subTest(reply=reply):
                self.assertEqual(parse_choice(reply, DIRECTORY), "fitness-coach")

    def test_anything_else_is_none(self):
        for reply in ("none", "None.", "frankie", "I think fitness-coach", "", None, 42, "<payload>none</payload>"):
            with self.subTest(reply=reply):
                self.assertIsNone(parse_choice(reply, DIRECTORY))


class TestClosestTarget(unittest.IsolatedAsyncioTestCase):
    """One task for the stateless worker, through `ask_worker`."""

    def _worker(self, text=None, fails=False):
        result = None if fails else SimpleNamespace(text=text, response=SimpleNamespace(text=text))
        return patch("core.runtime.delegation.ask_worker", new=AsyncMock(return_value=result))

    async def test_returns_the_key_the_worker_names(self):
        with self._worker("<payload>fitness-coach</payload>") as ask:
            self.assertEqual(await closest_target("exercise", DIRECTORY), "fitness-coach")
        prompt = ask.await_args.args[0]
        self.assertIn('"exercise"', prompt)
        self.assertIn("fitness coach Frankie", prompt)
        self.assertIn("<assigned_task>", prompt, "in the worker's playbook format")
        # Asked from the lobby, on the voice budget.
        self.assertEqual(ask.await_args.kwargs["channel"], "general")
        self.assertEqual(ask.await_args.kwargs["caller"], "near-miss")
        self.assertEqual(ask.await_args.kwargs["timeout"], NearMissPrompt.timeout)

    async def test_none_when_the_worker_says_none(self):
        with self._worker("<payload>none</payload>"):
            self.assertIsNone(await closest_target("the airport on friday", DIRECTORY))

    async def test_none_when_the_worker_fails_or_is_too_slow(self):
        """ask_worker returns None for both, and has logged why."""
        with self._worker(fails=True):
            self.assertIsNone(await closest_target("exercise", DIRECTORY))


if __name__ == "__main__":
    unittest.main()
