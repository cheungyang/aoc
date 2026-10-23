"""Switching by voice: the alias table "switch me to X" is matched against,
and the switcher moving the focus, with its "Do you mean …?" for a name that
isn't exact, which tells you the words to say and waits for nothing.

What is said reaches the switcher as it does in a turn: through the spoken
commands (core/runtime/commands/spoken/), whose phrases are tested with them
(tests/core/runtime/commands/spoken/).
"""
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.runtime.commands.base import SpokenContext
from core.runtime.commands.registry import SPOKEN
from core.runtime.commands.spoken.switch import match_target, parse_room_request
from core.voice.room.aliases import build_aliases
from core.voice.room.focus import FocusDirectory
from core.voice.room.room_switch import FocusSwitcher, could_name_a_target

TARGETS = build_aliases([
    ("day-planning", ["day planning", "Daisy"]),
    ("fitness-coach", ["fitness coach", "Frankie"]),
    ("meal-planning", ["meal planning", "Ramsay"]),
    ("home-automation", ["home automation", "Butler"]),
    ("general", ["general", "concierge"]),
])


class TestAliases(unittest.TestCase):

    def test_an_alias_two_targets_claim_is_dropped(self):
        aliases = build_aliases([
            ("weekly-planning", ["weekly planning", "Grace"]),
            ("project-planning", ["project planning", "Grace"]),
        ])
        self.assertNotIn("grace", aliases)
        self.assertEqual(aliases["weekly planning"], "weekly-planning")

    def test_filler_words_are_ignored_on_both_sides(self):
        """"the concierge" is said with "the" and stored without: both must meet."""
        aliases = build_aliases([("general", ["the concierge"])])
        self.assertEqual(aliases, {"concierge": "general"})
        self.assertEqual(match_target("take me to the concierge", aliases), "general")

    def test_the_same_name_twice_for_one_target_is_not_ambiguous(self):
        aliases = build_aliases([("market-scout", ["market scout"]), ("market-scout", ["Marvin", "market-scout"])])
        self.assertEqual(aliases["market scout"], "market-scout")


class TestNearMissCandidates(unittest.TestCase):
    """Which phrases that name no one exactly are worth asking the model about."""

    def test_short_descriptions_and_misheard_names_are(self):
        for transcript in ("take me to exercise", "take me to frankly", "switch me to my workout coach",
                           "take me to fitness coach Frankie", "take me to the airport on Friday"):
            with self.subTest(transcript=transcript):
                self.assertTrue(could_name_a_target(parse_room_request(transcript), TARGETS))

    def test_requests_that_start_like_a_switch_are_not(self):
        for transcript in ("go to Daisy and add a task", "Talk to me about dinner",
                           "go to day planning and add a dentist task", "switch to Frankie then log my run",
                           "go to the store and pick up milk for the kids tonight"):
            with self.subTest(transcript=transcript):
                self.assertFalse(could_name_a_target(parse_room_request(transcript), TARGETS))


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
             "channel_hosts": ["general", "day-planning", "fitness-coach"]},
    "day-planner": {"name": "Daisy", "channels": ["day-planning", "general"]},
    "fitness-coach": {"name": "Frankie", "channels": ["fitness-coach", "general"],
                      "description": "Curates and tracks home workouts."},
}), voice_host="main")


class _Voice:
    """VoiceManager, reduced to what the switcher uses (the focus) and what
    spoken commands ask of the room (VoiceControls)."""

    def __init__(self, reachable=True):
        self._focus = DIRECTORY.lobby
        self.reachable = reachable
        self.moves, self.arrivals = [], []
        self.switcher = None

    def directory(self):
        return DIRECTORY

    def focus(self):
        return self._focus

    def set_focus(self, focus, *, interrupt=True):
        self.moves.append((focus.key, interrupt))
        self._focus = focus

    def focus_channel(self, focus):
        return object() if self.reachable else None

    async def arrive(self, focus, say):
        self.arrivals.append(focus.key)

    async def switch_to_named(self, phrase, say):
        return await self.switcher.switch_to_named(phrase, say)

    def whereabouts(self):
        return DIRECTORY.whereabouts(self._focus), self._focus.agent_id

    async def next_agent(self, say):
        return False  # no feature (the inbox's hook) offers one


class _Said:
    def __init__(self):
        self.lines = []

    async def __call__(self, text, agent_id=None, **kwargs):
        self.lines.append((text, agent_id))


class TestFocusSwitcher(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.voice = _Voice()
        self.resolver = AsyncMock(return_value=None)
        self.switcher = self.voice.switcher = FocusSwitcher(self.voice, "main", resolver=self.resolver)
        self.say = _Said()

    async def hear(self, transcript):
        """`transcript`, said in a turn: True if a spoken command answered it,
        False if it goes to the agent you're with."""
        ctx = SpokenContext(voice=self.voice, heard=transcript, say=self.say, turn=MagicMock())
        return await SPOKEN.answer(transcript, ctx) is not None

    async def test_an_exact_name_switches_at_once_and_the_agent_greets_you(self):
        self.assertTrue(await self.hear("Switch me to Daisy"))
        self.assertEqual(self.voice.moves, [("day-planning", False)])  # the asking turn isn't cancelled
        self.assertEqual(self.say.lines, [("Daisy here.", "day-planner")])
        # Then whatever Daisy left you there (the voice owner says it).
        self.assertEqual(self.voice.arrivals, ["day-planning"])
        self.resolver.assert_not_awaited()

    async def test_start_with_is_a_switch_phrase(self):
        self.assertTrue(await self.hear("Start with Daisy"))
        self.assertEqual(self.voice.moves, [("day-planning", False)])

    async def test_take_me_to_concierge_goes_back_to_the_lobby(self):
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertTrue(await self.hear("Take me to concierge"))
        self.assertEqual(self.voice.moves, [("general", False)])
        self.assertEqual(self.say.lines, [("Concierge here.", "main")])

    async def test_asking_for_where_you_are(self):
        """Nothing moves, but what Daisy left you still plays: an announcement
        about the agent you're with says to switch to it."""
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertTrue(await self.hear("switch me to Daisy"))
        self.assertEqual(self.voice.moves, [])
        self.assertEqual(self.say.lines, [("You're already with Daisy.", "day-planner")])
        self.assertEqual(self.voice.arrivals, ["day-planning"])

    async def test_an_unreachable_channel_is_said_by_the_concierge(self):
        self.voice.reachable = False
        self.assertTrue(await self.hear("take me to Frankie"))
        self.assertEqual(self.voice.moves, [])
        self.assertEqual(self.say.lines, [("I can't reach fitness coach right now.", "main")])
        self.assertEqual(self.voice.arrivals, [])

    async def test_where_am_i_is_answered_by_whoever_you_are_with(self):
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertTrue(await self.hear("Where am I?"))
        self.assertEqual(self.say.lines, [("You're in day planning, with Daisy.", "day-planner")])
        self.resolver.assert_not_awaited()

    async def test_a_near_miss_tells_you_the_words_and_waits_for_nothing(self):
        self.resolver.return_value = "fitness-coach"
        self.assertTrue(await self.hear("take me to exercise"))
        self.resolver.assert_awaited_once_with("exercise", DIRECTORY)
        self.assertEqual(self.say.lines, [
            ("Do you mean fitness coach Frankie? Say 'switch to Frankie' to get there.", "main"),
        ])
        self.assertEqual(self.voice.moves, [])
        # No mode: a "yes" is an ordinary request for the agent you're with.
        self.assertFalse(await self.hear("Yes."))
        self.assertEqual(self.voice.moves, [])
        # The words it said switch.
        self.assertTrue(await self.hear("Switch to Frankie"))
        self.assertEqual(self.voice.moves, [("fitness-coach", False)])
        self.assertEqual(self.say.lines[-1], ("Frankie here.", "fitness-coach"))

    async def test_a_near_miss_for_a_channel_the_concierge_answers_names_the_channel(self):
        self.resolver.return_value = "general"
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertTrue(await self.hear("take me to the front desk"))
        self.assertEqual(self.say.lines, [("Do you mean the concierge? Say 'switch to concierge' to get there.", "main")])

    async def test_switch_to_is_how_hooks_switch(self):
        """A hook ("next agent") arrives exactly as a spoken switch does."""
        self.assertTrue(await self.switcher.switch_to(DIRECTORY.targets["fitness-coach"], DIRECTORY, self.say))
        self.assertEqual(self.voice.moves, [("fitness-coach", False)])
        self.assertEqual(self.say.lines, [("Frankie here.", "fitness-coach")])
        self.assertEqual(self.voice.arrivals, ["fitness-coach"])

    async def test_next_agent_is_not_the_switchers(self):
        """Without the inbox's hook, "next agent" is no command: it goes to the agent."""
        self.assertFalse(await self.hear("Next agent."))
        self.assertEqual(self.voice.moves, [])

    async def test_nothing_close_is_an_ordinary_request(self):
        self.assertFalse(await self.hear("take me to the airport on Friday"))
        self.resolver.assert_awaited_once()
        self.assertEqual(self.say.lines, [])

    async def test_a_near_miss_for_where_you_already_are(self):
        self.resolver.return_value = "general"
        self.assertTrue(await self.hear("take me to the front desk"))
        self.assertEqual(self.say.lines, [("You're already with the concierge.", "main")])

    async def test_a_model_reply_naming_no_target_is_an_ordinary_request(self):
        self.resolver.return_value = "nowhere"
        self.assertFalse(await self.hear("take me to narnia"))

    async def test_requests_that_arent_switches_never_call_the_model(self):
        for transcript in ("What's on my list today?", "Who are you?", "Go to Daisy and add a dentist task",
                           "Talk to me about dinner"):
            with self.subTest(transcript=transcript):
                self.assertFalse(await self.hear(transcript))
        self.resolver.assert_not_awaited()
        self.assertEqual(self.voice.moves, [])


if __name__ == "__main__":
    unittest.main()
