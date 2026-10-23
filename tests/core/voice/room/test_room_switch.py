"""Switching by voice: matching "switch me to X" against the known focus
targets, and the switcher's "Do you mean …?" for a name that isn't exact,
which tells you the words to say and waits for nothing."""
import unittest
from unittest.mock import AsyncMock

from core.voice.room.focus import FocusDirectory
from core.voice.room.aliases import build_aliases
from core.voice.room.room_switch import (
    FocusSwitcher, could_name_a_target, is_where_am_i, match_target, parse_room_request,
)

TARGETS = build_aliases([
    ("day-planning", ["day planning", "Daisy"]),
    ("fitness-coach", ["fitness coach", "Frankie"]),
    ("meal-planning", ["meal planning", "Ramsay"]),
    ("home-automation", ["home automation", "Butler"]),
    ("general", ["general", "concierge"]),
])


class TestMatching(unittest.TestCase):

    def test_commands_that_switch(self):
        cases = {
            "Take me to day planning.": "day-planning",
            "take me to Daisy's room": "day-planning",
            "Let's go to the fitness coach room": "fitness-coach",
            "Hey concierge, switch to meal planning please": "meal-planning",
            "Can you take me to Butler?": "home-automation",
            "I want to talk to Ramsay": "meal-planning",
            "Okay, go to Frankie.": "fitness-coach",
            "move me into home automation": "home-automation",
            "Switch me to Daisy.": "day-planning",
            "Take me to concierge.": "general",
            "Back to concierge.": "general",
            "Take me back to the concierge": "general",
            "go back to day planning": "day-planning",
            "Take me to general": "general",
            "Start with Daisy.": "day-planning",
            "start from meal planning": "meal-planning",
        }
        for transcript, target in cases.items():
            with self.subTest(transcript=transcript):
                self.assertEqual(match_target(transcript, TARGETS), target)

    def test_ordinary_requests_are_not_switches(self):
        """A phrase that names no target goes to the agent you're with."""
        for transcript in (
            "go to day planning and add a task",
            "What's on my day planning list?",
            "Talk to me about dinner",
            "take me to the airport on Friday",
            "Daisy, what's next?",
            "Back to the dentist thing: move it to Friday",
        ):
            with self.subTest(transcript=transcript):
                self.assertIsNone(match_target(transcript, TARGETS))

    def test_names_only_not_agent_ids_or_old_lobby_words(self):
        """"the day planner" is a near miss (Daisy's id, not her name); "lobby"
        and "main menu" are gone: the lobby is the concierge."""
        for transcript in ("switch over to the day planner", "take me to the lobby", "return to the main menu"):
            with self.subTest(transcript=transcript):
                self.assertIsNone(match_target(transcript, TARGETS))
                self.assertIsNotNone(parse_room_request(transcript))

    def test_parse_extracts_the_target_phrase(self):
        self.assertEqual(parse_room_request("Take me to the Day-Planning room, please!"), "day planning")
        self.assertEqual(parse_room_request("Switch me to Daisy."), "daisy")
        self.assertEqual(parse_room_request("take me to exercise"), "exercise")
        self.assertIsNone(parse_room_request("Turn off the kitchen lights"))


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


class TestWhereAmI(unittest.TestCase):

    def test_questions_about_the_place(self):
        for transcript in ("Where am I?", "which channel am I in", "Hey concierge, where am I right now?"):
            with self.subTest(transcript=transcript):
                self.assertTrue(is_where_am_i(transcript))

    def test_who_questions_go_to_the_agent(self):
        """"Who are you?" is answered by the agent you're with, in its own words."""
        for transcript in ("who am I talking to", "Who is this?", "Who are you?"):
            with self.subTest(transcript=transcript):
                self.assertFalse(is_where_am_i(transcript))

    def test_other_questions_are_not(self):
        for transcript in ("Where am I going tomorrow?", "Who is on my calendar today?", "where are my keys"):
            with self.subTest(transcript=transcript):
                self.assertFalse(is_where_am_i(transcript))


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
    """The focus owner (VoiceManager), reduced to what the switcher uses."""

    def __init__(self, reachable=True):
        self._focus = DIRECTORY.lobby
        self.reachable = reachable
        self.moves, self.arrivals = [], []

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


class _Said:
    def __init__(self):
        self.lines = []

    async def __call__(self, text, agent_id=None, **kwargs):
        self.lines.append((text, agent_id))


class TestFocusSwitcher(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.voice = _Voice()
        self.resolver = AsyncMock(return_value=None)
        self.switcher = FocusSwitcher(self.voice, "main", resolver=self.resolver)
        self.say = _Said()

    async def test_an_exact_name_switches_at_once_and_the_agent_greets_you(self):
        self.assertTrue(await self.switcher.handle("Switch me to Daisy", self.say))
        self.assertEqual(self.voice.moves, [("day-planning", False)])  # the asking turn isn't cancelled
        self.assertEqual(self.say.lines, [("Daisy here.", "day-planner")])
        # Then whatever Daisy left you there (the voice owner says it).
        self.assertEqual(self.voice.arrivals, ["day-planning"])
        self.resolver.assert_not_awaited()

    async def test_start_with_is_a_switch_phrase(self):
        self.assertTrue(await self.switcher.handle("Start with Daisy", self.say))
        self.assertEqual(self.voice.moves, [("day-planning", False)])

    async def test_take_me_to_concierge_goes_back_to_the_lobby(self):
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertTrue(await self.switcher.handle("Take me to concierge", self.say))
        self.assertEqual(self.voice.moves, [("general", False)])
        self.assertEqual(self.say.lines, [("Concierge here.", "main")])

    async def test_asking_for_where_you_are(self):
        """Nothing moves, but what Daisy left you still plays: an announcement
        about the agent you're with says to switch to it."""
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertTrue(await self.switcher.handle("switch me to Daisy", self.say))
        self.assertEqual(self.voice.moves, [])
        self.assertEqual(self.say.lines, [("You're already with Daisy.", "day-planner")])
        self.assertEqual(self.voice.arrivals, ["day-planning"])

    async def test_an_unreachable_channel_is_said_by_the_concierge(self):
        self.voice.reachable = False
        self.assertTrue(await self.switcher.handle("take me to Frankie", self.say))
        self.assertEqual(self.voice.moves, [])
        self.assertEqual(self.say.lines, [("I can't reach fitness coach right now.", "main")])
        self.assertEqual(self.voice.arrivals, [])

    async def test_where_am_i_is_answered_by_whoever_you_are_with(self):
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertTrue(await self.switcher.handle("Where am I?", self.say))
        self.assertEqual(self.say.lines, [("You're in day planning, with Daisy.", "day-planner")])
        self.resolver.assert_not_awaited()

    async def test_a_near_miss_tells_you_the_words_and_waits_for_nothing(self):
        self.resolver.return_value = "fitness-coach"
        self.assertTrue(await self.switcher.handle("take me to exercise", self.say))
        self.resolver.assert_awaited_once_with("exercise", DIRECTORY)
        self.assertEqual(self.say.lines, [
            ("Do you mean fitness coach Frankie? Say 'switch to Frankie' to get there.", "main"),
        ])
        self.assertEqual(self.voice.moves, [])
        # No mode: a "yes" is an ordinary request for the agent you're with.
        self.assertFalse(await self.switcher.handle("Yes.", self.say))
        self.assertEqual(self.voice.moves, [])
        # The words it said switch.
        self.assertTrue(await self.switcher.handle("Switch to Frankie", self.say))
        self.assertEqual(self.voice.moves, [("fitness-coach", False)])
        self.assertEqual(self.say.lines[-1], ("Frankie here.", "fitness-coach"))

    async def test_a_near_miss_for_a_channel_the_concierge_answers_names_the_channel(self):
        self.resolver.return_value = "general"
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertTrue(await self.switcher.handle("take me to the front desk", self.say))
        self.assertEqual(self.say.lines, [("Do you mean the concierge? Say 'switch to concierge' to get there.", "main")])

    async def test_switch_to_is_how_hooks_switch(self):
        """A hook ("next agent") arrives exactly as a spoken switch does."""
        self.assertTrue(await self.switcher.switch_to(DIRECTORY.targets["fitness-coach"], DIRECTORY, self.say))
        self.assertEqual(self.voice.moves, [("fitness-coach", False)])
        self.assertEqual(self.say.lines, [("Frankie here.", "fitness-coach")])
        self.assertEqual(self.voice.arrivals, ["fitness-coach"])

    async def test_next_agent_is_not_the_switchers(self):
        """Without the inbox's hook, "next agent" is just a near miss."""
        self.assertFalse(await self.switcher.handle("Next agent.", self.say))
        self.assertEqual(self.voice.moves, [])

    async def test_nothing_close_is_an_ordinary_request(self):
        self.assertFalse(await self.switcher.handle("take me to the airport on Friday", self.say))
        self.resolver.assert_awaited_once()
        self.assertEqual(self.say.lines, [])

    async def test_a_near_miss_for_where_you_already_are(self):
        self.resolver.return_value = "general"
        self.assertTrue(await self.switcher.handle("take me to the front desk", self.say))
        self.assertEqual(self.say.lines, [("You're already with the concierge.", "main")])

    async def test_a_model_reply_naming_no_target_is_an_ordinary_request(self):
        self.resolver.return_value = "nowhere"
        self.assertFalse(await self.switcher.handle("take me to narnia", self.say))

    async def test_requests_that_arent_switches_never_call_the_model(self):
        for transcript in ("What's on my list today?", "Who are you?", "Go to Daisy and add a dentist task",
                           "Talk to me about dinner"):
            with self.subTest(transcript=transcript):
                self.assertFalse(await self.switcher.handle(transcript, self.say))
        self.resolver.assert_not_awaited()
        self.assertEqual(self.voice.moves, [])


if __name__ == "__main__":
    unittest.main()
