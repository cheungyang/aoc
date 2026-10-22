"""The focus directory: which agent and text channel each voice switch leads to.

Pinned against the live agent configs, like the routing table in
tests/graphs/main/test_router.py: a channel gaining an agent, or an agent its
first channel, changes what "switch me to <name>" does with no code change.
If a test here fails, confirm the config change was intended first.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.loaders.agents_loader import AgentsLoader
from core.runtime.channel_context import reply_sessions
from core.runtime.session_manager import SessionManager
from core.voice.room.focus import DEFAULT_LOBBY_CHANNEL, FocusDirectory, spoken_label
from core.voice.room.room_switch import _key, build_aliases


def _live():
    return FocusDirectory.build(AgentsLoader(), voice_host="main")


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


SMALL = {
    "main": {"name": "Concierge", "channels": ["*"], "tools": {"agent_call": {}},
             "channel_hosts": ["general", "day-planning", "aoc-debug"]},
    "day-planner": {"name": "Daisy", "channels": ["day-planning", "general"]},
    "helper": {"name": "Helper", "channels": ["general"]},
}


class TestLiveDirectory(unittest.TestCase):

    # channel -> who answers there. Matches graphs/main's routing table: every
    # channel is Concierge's, handed to its one agent when it has one.
    EXPECTED = {
        "agent-management": "agent-designer",
        "aoc-debug": "main",
        "content-creation": "main",
        "day-planning": "day-planner",
        "fitness-coach": "fitness-coach",
        "general": "main",
        "home-automation": "home-steward",
        "lifestyle-perk": "reward-travel",
        "market-scout": "market-scout",
        "meal-planning": "meal-planner",
        "pkm-wiki": "wiki-gardener",
        "project-planning": "goal-setter",
        "real-estate": "property-scout",
        "receipts": "receipt-processor",
        "software-dev": "software-planner",
        "topic-research": "topic-researcher",
        "weekend-planning": "excursion-planner",
        "weekly-planning": "day-planner",
    }

    @classmethod
    def setUpClass(cls):
        cls.directory = _live()
        cls.aliases = build_aliases(cls.directory.alias_sources())

    def test_targets_match_the_expected_table(self):
        actual = {k: t.agent_id for k, t in self.directory.targets.items()}
        self.assertEqual(actual, self.EXPECTED)

    def test_the_lobby_is_general(self):
        """D1: the lobby is Concierge in #general, not a voice-only session."""
        lobby = self.directory.lobby
        self.assertEqual((lobby.key, lobby.channel_name, lobby.agent_id, lobby.is_lobby),
                         ("general", "general", "main", True))
        self.assertEqual([k for k, t in self.directory.targets.items() if t.is_lobby], ["general"])

    def test_concierge_leads_to_the_lobby(self):
        self.assertEqual(self.aliases["concierge"], "general")
        self.assertEqual(self.aliases["general"], "general")
        for word in ("lobby", "main menu", "main", "back"):
            self.assertNotIn(word, self.aliases, word)

    def test_agent_names_lead_to_their_home_channel(self):
        expected = {
            "daisy": "day-planning",          # her first channel, not weekly-planning
            "weekly planning": "weekly-planning",
            "elephant": "software-dev",       # lists #general first, but 13 agents share it
            "butler": "home-automation",
            "aki": "agent-management",        # an ordinary specialist: reached through Concierge
            "frankie": "fitness-coach",
            "rio": "real-estate",
            "marvin": "market-scout",
            "receipt processor": "receipts",
        }
        for alias, channel in expected.items():
            self.assertEqual(self.aliases.get(alias), channel, alias)

    def test_agent_ids_are_not_names_to_switch_by(self):
        """"day planner" is how "Do you mean …?" describes Daisy; saying it is a near miss."""
        for alias in ("day planner", "agent designer", "property scout", "software planner"):
            self.assertNotIn(alias, self.aliases, alias)

    def test_internal_agents_have_no_alias(self):
        for alias in ("terminator", "graph worker", "graph worker low"):
            self.assertNotIn(alias, self.aliases)

    def test_how_each_focus_is_spoken_about(self):
        d, t = self.directory, self.directory.targets
        self.assertEqual(d.greeting(d.lobby), "Concierge here.")
        self.assertEqual(d.greeting(t["day-planning"]), "Daisy here.")
        self.assertEqual(d.greeting(t["agent-management"]), "Aki here.")
        self.assertEqual(d.greeting(t["aoc-debug"]), "You're in aoc debug.")
        self.assertEqual(d.role(t["fitness-coach"]), "fitness coach Frankie")
        self.assertEqual(d.role(t["receipts"]), "Receipt Processor")  # its id says the same
        self.assertEqual(d.role(t["aoc-debug"]), "aoc debug")
        self.assertEqual(d.role(d.lobby), "the concierge")
        self.assertEqual(d.whereabouts(t["day-planning"]), "You're in day planning, with Daisy.")
        self.assertEqual(d.whereabouts(d.lobby), "You're in general, with the concierge.")
        self.assertEqual(d.already(t["day-planning"]), "You're already with Daisy.")
        self.assertEqual(d.already(t["aoc-debug"]), "You're already in aoc debug.")
        self.assertEqual(d.already(d.lobby), "You're already with the concierge.")
        # The words "Do you mean …?" and the digest tell you to say.
        self.assertEqual(d.words_to_switch(t["fitness-coach"]), "switch to Frankie")
        self.assertEqual(d.words_to_switch(t["day-planning"]), "switch to Daisy")
        self.assertEqual(d.words_to_switch(t["aoc-debug"]), "switch to aoc debug")
        self.assertEqual(d.words_to_switch(d.lobby), "switch to concierge")

    def test_the_words_to_switch_always_lead_back_to_the_same_focus(self):
        """Every spoken alias is one the switch phrase maps back to its focus."""
        for key, focus in self.directory.targets.items():
            with self.subTest(channel=key):
                alias = self.directory.spoken_alias(focus)
                self.assertEqual(self.aliases.get(_key(alias)), key, alias)


class TestDirectoryRules(unittest.TestCase):

    def test_targets_are_the_voice_hosts_channels(self):
        directory = FocusDirectory.build(_Loader(SMALL), voice_host="main")
        self.assertEqual({k: t.agent_id for k, t in directory.targets.items()},
                         {"general": "main", "day-planning": "day-planner", "aoc-debug": "main"})
        self.assertEqual(directory.homes, {"day-planner": "day-planning"})  # Helper has no channel of its own

    def test_the_lobby_channel_can_be_configured(self):
        directory = FocusDirectory.build(_Loader(SMALL), voice_host="main", lobby_channel="#AOC-Debug")
        self.assertEqual(directory.lobby.key, "aoc-debug")
        self.assertTrue(directory.targets["aoc-debug"].is_lobby)
        self.assertFalse(directory.targets["general"].is_lobby)

    def test_a_lobby_exists_even_when_configs_cant_be_read(self):
        directory = FocusDirectory.build(MagicMock(), voice_host="main")
        self.assertEqual(directory.targets, {})
        self.assertEqual((directory.lobby.key, directory.lobby.agent_id), (DEFAULT_LOBBY_CHANNEL, "main"))
        self.assertEqual(directory.greeting(directory.lobby), "Concierge here.")
        self.assertIs(directory.get("general"), directory.lobby)

    def test_for_channel_takes_a_name_a_channel_or_a_thread(self):
        directory = FocusDirectory.build(_Loader(SMALL), voice_host="main")
        self.assertEqual(directory.for_channel("#Day-Planning").key, "day-planning")
        self.assertEqual(directory.for_channel(SimpleNamespace(name="day-planning")).key, "day-planning")
        thread = SimpleNamespace(name="Tuesday", is_thread=True, parent=SimpleNamespace(name="day-planning"))
        self.assertEqual(directory.for_channel(thread).key, "day-planning")
        self.assertIsNone(directory.for_channel("nowhere"))
        self.assertIsNone(directory.for_channel(None))

    def test_spoken_label(self):
        self.assertEqual(spoken_label("day-planning"), "day planning")
        self.assertEqual(spoken_label("pkm_wiki"), "pkm wiki")


class TestSessionParity(unittest.TestCase):
    """I1: a voice turn under a focus is answered from the session an untagged
    typed message in that channel would be -- never a voice-only one."""

    def test_every_focus_answers_from_the_typed_messages_session(self):
        loader = AgentsLoader()
        for channel, focus in _live().targets.items():
            with self.subTest(channel=channel):
                if focus.agent_id != "main":
                    answering = SessionManager.get_session(focus.agent_id, source="tool", channel=channel)
                else:
                    answering = SessionManager.get_session("main", source="discord", channel=channel)
                typed = {s.session_id for s in reply_sessions(channel, loader=loader)}
                self.assertIn(answering.session_id, typed)

    def test_the_voice_turn_runs_as_concierge_on_the_channels_session(self):
        """As a typed message does: Concierge hosts #agent-management too, and
        hands the turn to Aki on the session typed messages there reach."""
        focus = _live().targets["agent-management"]
        session = SessionManager.get_session("main", source="discord", channel=focus.channel_name,
                                             surface="voice")
        self.assertEqual(session.session_id, "main:discord:agent-management")
        self.assertEqual(
            [s.session_id for s in reply_sessions("agent-management", loader=AgentsLoader())],
            ["agent-designer:tool:agent-management"],
        )


if __name__ == "__main__":
    unittest.main()
