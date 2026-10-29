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
from core.runtime.commands.spoken.switch import match_target, parse_switch
from core.runtime.observability.turn_trace import Outcome
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
                self.assertTrue(could_name_a_target(parse_switch(transcript).phrase, TARGETS))

    def test_requests_that_start_like_a_switch_are_not(self):
        for transcript in ("go to Daisy and add a task", "Talk to me about dinner",
                           "go to day planning and add a dentist task", "switch to Frankie then log my run",
                           "go to the store and pick up milk for the kids tonight",
                           "I want to talk to you about my workout", "can I talk to you about my run"):
            with self.subTest(transcript=transcript):
                self.assertFalse(could_name_a_target(parse_switch(transcript).phrase, TARGETS))


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
        self.previous = None   # where "go back" returns
        self.noise = None      # why the newest utterance isn't a voice, for a bare name
        self.live = False      # whether you were just talking where you are

    def directory(self):
        return DIRECTORY

    def focus(self):
        return self._focus

    def set_focus(self, focus, *, interrupt=True):
        self.moves.append((focus.key, interrupt))
        if focus.key != self._focus.key:
            self.previous = self._focus
        self._focus = focus

    def focus_channel(self, focus):
        return object() if self.reachable else None

    async def arrive(self, focus, say):
        self.arrivals.append(focus.key)

    async def switch_to_named(self, phrase, say, *, asked=True, bare=False, turn=None):
        return await self.switcher.switch_to_named(phrase, say, asked=asked, bare=bare, noise=lambda: self.noise,
                                                   live=lambda focus: self.live)

    async def go_back(self, say):
        if self.previous is None:
            return False
        return await self.switcher.switch_to(self.previous, DIRECTORY, say)

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

    async def test_start_with_switches_to_an_exact_name(self):
        self.assertTrue(await self.hear("Start with Daisy"))
        self.assertEqual(self.voice.moves, [("day-planning", False)])

    async def test_start_with_anyone_else_is_an_answer_with_no_model_call(self):
        """Said to Grace in the logs, about the directors: never "Do you mean ...?"."""
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertFalse(await self.hear("Let's start with Michael."))
        self.resolver.assert_not_awaited()
        self.assertEqual(self.voice.moves, [])

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


class TestNamesSaidWithoutASwitchPhrase(unittest.IsolatedAsyncioTestCase):
    """A name said without a switch phrase (core/runtime/commands/spoken/switch.py):
    "Grace.", "let's start with Grace". An instant switch, to an exact name
    only. The user's rules: named while you're with someone else, it
    switches, with "go back"; said with switch words to the agent you're
    with, "You're already with ..."; said bare to the agent you're with, it's
    an ordinary request for them."""

    def setUp(self):
        self.voice = _Voice()
        self.resolver = AsyncMock(return_value=None)
        self.switcher = self.voice.switcher = FocusSwitcher(self.voice, "main", resolver=self.resolver)
        self.say = _Said()
        self.turn = MagicMock()

    async def hear(self, transcript):
        """`transcript`, said in a turn: whether a spoken command answered it
        (False: it goes to the agent you're with), and the spoken context."""
        ctx = SpokenContext(voice=self.voice, heard=transcript, say=self.say, turn=self.turn)
        return await SPOKEN.answer(transcript, ctx) is not None, ctx

    async def test_a_name_on_its_own_switches_at_once_with_no_model_call(self):
        for transcript in ("Daisy.", "Daisy. Daisy.", "Hey Daisy", "Daisy, are you there?", "Daisy please"):
            with self.subTest(transcript=transcript):
                self.voice._focus = DIRECTORY.lobby
                self.say.lines.clear()
                answered, ctx = await self.hear(transcript)
                self.assertTrue(answered)
                self.assertEqual(self.voice.focus().key, "day-planning")
                self.assertEqual(self.say.lines, [("Daisy here.", "day-planner")])
                self.assertEqual(ctx.outcome, Outcome.FOCUS_SWITCH)
        self.resolver.assert_not_awaited()

    async def test_switch_words_without_a_verb_first_switch_too(self):
        """The ways the prod logs asked that no switch phrase took."""
        for transcript in ("Let's start with Frankie.", "Chat with Frankie.", "I'm with Frankie.",
                           "Can I talk to Frankie please", "Over to Frankie"):
            with self.subTest(transcript=transcript):
                self.voice._focus = DIRECTORY.lobby
                answered, _ = await self.hear(transcript)
                self.assertTrue(answered)
                self.assertEqual(self.voice.focus().key, "fitness-coach")
        self.resolver.assert_not_awaited()

    async def test_a_request_with_words_of_its_own_is_not_a_switch(self):
        """Those go on to the agent, and the router hands one that names an
        agent to it (graphs/main)."""
        for transcript in ("Daisy, continue our conversation.", "Daisy, what's next?", "not Daisy",
                           "Daisy and Frankie", "Frankie's workout", "Frankly speaking", "Let's go.", "Okay."):
            with self.subTest(transcript=transcript):
                answered, _ = await self.hear(transcript)
                self.assertFalse(answered)
        self.assertEqual(self.voice.moves, [])
        self.resolver.assert_not_awaited()

    async def test_only_an_exact_name_with_no_near_miss_on_top_of_a_guess(self):
        """"Muffins" may have been Marvin, but a name said without a switch
        phrase is already a guess: no model call, and the words go to the agent."""
        answered, _ = await self.hear("Muffins.")
        self.assertFalse(answered)
        self.resolver.assert_not_awaited()

    async def test_the_agent_you_are_with_gets_its_bare_name_as_a_request(self):
        self.voice._focus = DIRECTORY.targets["day-planning"]
        answered, _ = await self.hear("Thanks, Daisy.")
        self.assertFalse(answered)
        self.assertEqual(self.say.lines, [])

    async def test_asking_in_switch_words_for_where_you_are_says_so(self):
        self.voice._focus = DIRECTORY.targets["day-planning"]
        for transcript in ("Let's talk to Daisy", "Let's start with Daisy"):
            with self.subTest(transcript=transcript):
                self.say.lines.clear()
                answered, _ = await self.hear(transcript)
                self.assertTrue(answered)
                self.assertEqual(self.say.lines, [("You're already with Daisy.", "day-planner")])
        self.assertEqual(self.voice.moves, [])

    async def test_a_bare_name_that_does_not_sound_like_a_voice_is_dropped_as_noise(self):
        """The recognizer turns noise into agent names ("Receipt Processor." at
        17:44, 0.21 voiced): it neither moves the room nor reaches an agent."""
        self.voice.noise = "0.21 voiced"
        answered, ctx = await self.hear("Frankie.")
        self.assertTrue(answered, "answered: there's nothing in it for the agent")
        self.assertEqual(self.voice.moves, [])
        self.assertEqual(self.say.lines, [])
        self.assertEqual(ctx.outcome, Outcome.NO_TRANSCRIPT)
        self.turn.hold.assert_called_once()  # only this utterance goes; earlier words stay

    async def test_switch_words_are_not_held_to_the_noise_check(self):
        self.voice.noise = "0.21 voiced"
        answered, _ = await self.hear("Let's start with Frankie")
        self.assertTrue(answered)
        self.assertEqual(self.voice.focus().key, "fitness-coach")

    async def test_the_noise_check_is_only_for_a_name(self):
        """A whispered "yes" names no one: it goes to the agent, never dropped."""
        self.voice.noise = "0.10 voiced"
        answered, _ = await self.hear("Yes.")
        self.assertFalse(answered)
        self.turn.hold.assert_not_called()

    async def test_leaving_a_conversation_says_how_to_go_back_however_you_asked(self):
        for transcript in ("Frankie.", "Let's start with Frankie", "Switch me to Frankie"):
            with self.subTest(transcript=transcript):
                self.voice._focus = DIRECTORY.targets["day-planning"]
                self.voice.live = True
                self.say.lines.clear()
                self.assertTrue((await self.hear(transcript))[0])
                self.assertEqual(self.say.lines, [("Frankie here. Say 'go back' for Daisy.", "fitness-coach")])
                # And saying it does.
                self.assertTrue((await self.hear("Go back."))[0])
                self.assertEqual(self.voice.focus().key, "day-planning")
                self.assertEqual(self.say.lines[-1], ("Daisy here.", "day-planner"))

    async def test_no_hint_with_no_conversation_to_go_back_to(self):
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.voice.live = False
        await self.hear("Frankie.")
        self.assertEqual(self.say.lines, [("Frankie here.", "fitness-coach")])


class TestGoBack(unittest.IsolatedAsyncioTestCase):
    """"Go back" (core/runtime/commands/spoken/go_back.py), as the switcher
    answers it. When it stops being a command is VoiceManager's
    (tests/core/voice/test_voice_manager.py)."""

    def setUp(self):
        self.voice = _Voice()
        self.voice.switcher = FocusSwitcher(self.voice, "main", resolver=AsyncMock(return_value=None))
        self.say = _Said()

    async def hear(self, transcript):
        ctx = SpokenContext(voice=self.voice, heard=transcript, say=self.say, turn=MagicMock())
        return await SPOKEN.answer(transcript, ctx) is not None

    async def test_with_nowhere_to_go_back_to_it_is_a_request(self):
        self.assertFalse(await self.hear("Go back."))
        self.assertEqual(self.voice.moves, [])

    async def test_it_undoes_any_move_and_greets_you_there(self):
        self.assertTrue(await self.hear("Switch me to Daisy"))
        self.assertTrue(await self.hear("No, go back"))
        self.assertEqual(self.voice.focus().key, "general")
        self.assertEqual(self.say.lines[-1], ("Concierge here.", "main"))

    async def test_go_back_to_a_name_is_a_switch_phrase(self):
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertTrue(await self.hear("Go back to Frankie"))
        self.assertEqual(self.voice.focus().key, "fitness-coach")


class TestSwitchToAgent(unittest.IsolatedAsyncioTestCase):
    """An agent's reply handing you to a teammate (<switch to="..."/>)."""

    def setUp(self):
        self.voice = _Voice()
        self.switcher = self.voice.switcher = FocusSwitcher(self.voice, "main", resolver=AsyncMock(return_value=None))
        self.say = _Said()

    async def test_by_id_or_by_name_the_room_moves_and_the_teammate_greets_you(self):
        for agent, key, greeting in (("fitness-coach", "fitness-coach", ("Frankie here.", "fitness-coach")),
                                     ("Daisy", "day-planning", ("Daisy here.", "day-planner"))):
            with self.subTest(agent=agent):
                self.assertTrue(await self.switcher.switch_to_agent(agent, self.say))
                self.assertEqual(self.voice.focus().key, key)
                self.assertEqual(self.say.lines[-1], greeting)

    async def test_no_one_to_go_to_or_the_agent_itself_moves_nothing(self):
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertFalse(await self.switcher.switch_to_agent("nobody", self.say))
        self.assertFalse(await self.switcher.switch_to_agent("day-planner", self.say))
        self.assertEqual(self.voice.moves, [])
        self.assertEqual(self.say.lines, [])

    async def test_out_of_a_conversation_it_says_how_to_come_back(self):
        """Like any move: the agent you were talking to handed you over."""
        self.voice._focus = DIRECTORY.targets["day-planning"]
        self.assertTrue(await self.switcher.switch_to_agent("fitness-coach", self.say, live=lambda focus: True))
        self.assertEqual(self.say.lines, [("Frankie here. Say 'go back' for Daisy.", "fitness-coach")])


if __name__ == "__main__":
    unittest.main()
