"""Tests for the deterministic routing decision.

Includes a snapshot of the live routing table. That test is the tripwire for the
main hazard of deriving routing from configuration: adding an agent to a channel
flips that channel from its agent to the fixed line with no code change and no
other signal. If it fails, the question is whether the config change was
intended -- not whether the test should be updated.
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from core.runtime.foundation.routing import (
    CHANNEL_MESSAGE,
    CONCIERGE,
    DIRECT_CALL,
    FIXED,
    answering_agent,
    decide,
    routing_enabled,
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
# The Concierge's shape: reachable everywhere, owning no channel, holding no tools.
HOST = {"channels": ["*"], "tools": {}}


def _loader(hosts=(), eligible=()):
    """A loader double: what the router asks of AgentsLoader, and nothing more."""
    loader = MagicMock()
    loader.hosts_channel.side_effect = lambda agent_id, channel: channel in hosts
    loader.eligible_agents_for_channel.return_value = list(eligible)
    return loader


def _load_agent_configs():
    """Reads every real `agent.json`, keyed by agent id."""
    configs = {}
    agents_dir = os.path.join(PROJECT_ROOT, "agents")
    for name in os.listdir(agents_dir):
        path = os.path.join(agents_dir, name, "agent.json")
        if os.path.exists(path):
            with open(path) as f:
                config = json.load(f)
            configs[config.get("agent_id") or name] = config
    return configs


class TestRoutingEnabled(unittest.TestCase):

    def test_hosting_the_channel_is_enough(self):
        """No tool is required: the Concierge holds none."""
        self.assertTrue(routing_enabled("main", "receipts", HOST, _loader(hosts=("receipts",))))

    def test_a_turn_addressed_to_the_agent_is_never_routed(self):
        """A delegation, a job, a scheduled run or a graph's worker is the agent's
        own turn. The dream standup reaches the Concierge by delegation, in
        #general: routed, it would get the fixed line instead of the model."""
        loader = _loader(hosts=("general",))
        for source in ("tool", "job", "scheduled", "graph"):
            with self.subTest(source=source):
                self.assertFalse(routing_enabled("main", "general", HOST, loader, source=source))

    def test_a_message_in_the_channel_is_routed_typed_spoken_or_replayed(self):
        """Voice turns run on the channel's `discord` session, and the voice
        replay (scripts/voice_dryrun.py) takes their path under its own source."""
        loader = _loader(hosts=("general",))
        for source in (CHANNEL_MESSAGE, "voice_replay"):
            with self.subTest(source=source):
                self.assertTrue(routing_enabled("main", "general", HOST, loader, source=source))

    def test_an_agent_that_answers_in_the_channel_does_not_route_it(self):
        """An agent hosting a channel it lists itself has nobody to hand it to.
        Routing it would give the agent's own channel the fixed line."""
        loader = _loader(hosts=("software-dev",))
        config = {"channels": ["general", "software-dev"], "tools": {"graph_call": {}}}
        self.assertFalse(routing_enabled("software-planner", "software-dev", config, loader))

    def test_specialist_that_hosts_nothing_never_routes(self):
        """Every agent shares this graph, so a specialist must fall straight
        through to its own ReAct behaviour."""
        config = {"channels": ["receipts", "general"], "tools": {"filesystem": {}}}
        self.assertFalse(routing_enabled("receipt-processor", "receipts", config, _loader(hosts=())))

    def test_non_hosted_channel_does_not_route(self):
        self.assertFalse(routing_enabled("main", "some-other-channel", HOST, _loader(hosts=("receipts",))))

    def test_missing_channel_does_not_route(self):
        self.assertFalse(routing_enabled("main", "", HOST, _loader()))


class TestDecide(unittest.TestCase):

    def test_sole_eligible_agent_is_called_directly(self):
        loader = _loader(hosts=("receipts",), eligible=["receipt-processor"])
        decision = decide("scan this", "main", "receipts", HOST, loader)

        self.assertEqual(decision.target, DIRECT_CALL)
        self.assertEqual(decision.agent_id, "receipt-processor")
        self.assertTrue(decision.is_direct)

    def test_a_shared_channel_gets_the_fixed_line(self):
        loader = _loader(hosts=("general",), eligible=["day-planner", "meal-planner"])
        decision = decide("what's for dinner?", "main", "general", HOST, loader)

        self.assertEqual(decision.target, FIXED)
        self.assertIsNone(decision.agent_id)
        self.assertEqual(decision.reason, "2 eligible agents in #general")

    def test_a_channel_nobody_lists_gets_the_fixed_line(self):
        loader = _loader(hosts=("aoc-debug",), eligible=[])
        self.assertEqual(decide("hello", "main", "aoc-debug", HOST, loader).target, FIXED)

    def test_the_llm_fallback_brings_the_model_back_where_no_single_agent_answers(self):
        """The rollback switch changes shared channels only: a channel's one
        agent is still reached without a model."""
        shared = _loader(hosts=("general",), eligible=["day-planner", "meal-planner"])
        decision = decide("plan my week", "main", "general", HOST, shared, llm_fallback=True)
        self.assertEqual(decision.target, CONCIERGE)
        self.assertIn("ROUTING_LLM_FALLBACK", decision.reason)

        sole = _loader(hosts=("receipts",), eligible=["receipt-processor"])
        self.assertEqual(decide("scan", "main", "receipts", HOST, sole, llm_fallback=True).target, DIRECT_CALL)

    def test_what_used_to_be_a_concierge_prefix_is_just_a_message(self):
        """`[main]`, `jobs` and `kill` once claimed a message for the concierge.
        They retired with its tools -- job verbs are typed commands now -- so
        these words go to the channel's agent like any others, unchanged."""
        loader = _loader(hosts=("receipts",), eligible=["receipt-processor"])
        for text in ("[main] what did we decide?", "jobs", "kill job abc123", "list all jobs"):
            with self.subTest(text=text):
                decision = decide(text, "main", "receipts", HOST, loader)
                self.assertEqual(decision.target, DIRECT_CALL)
                self.assertEqual(decision.prompt, text)

    def test_a_delegated_turn_reaches_the_agent_it_addresses(self):
        loader = _loader(hosts=("general",), eligible=["day-planner", "meal-planner"])
        decision = decide("<dream prompt>", "main", "general", HOST, loader, source="tool")

        self.assertEqual(decision.target, CONCIERGE)
        loader.eligible_agents_for_channel.assert_not_called()

    def test_the_caller_excludes_itself(self):
        loader = _loader(hosts=("receipts",), eligible=["receipt-processor"])
        decide("scan", "main", "receipts", HOST, loader)
        loader.eligible_agents_for_channel.assert_called_once_with("receipts", exclude_agent_id="main")


class TestAnsweringAgent(unittest.TestCase):
    """The voice focus and the channel inbox ask who answers a channel."""

    def _loader(self):
        loader = _loader(hosts=("general", "receipts"))
        loader.get_agent_config.return_value = HOST
        loader.eligible_agents_for_channel.side_effect = lambda channel, exclude_agent_id=None: {
            "general": ["day-planner", "meal-planner"],
            "receipts": ["receipt-processor"],
        }[channel]
        return loader

    def test_a_channel_with_one_agent_is_answered_by_it(self):
        self.assertEqual(answering_agent("main", "receipts", self._loader()), "receipt-processor")

    def test_a_shared_channel_is_answered_by_the_host(self):
        self.assertEqual(answering_agent("main", "general", self._loader()), "main")


class TestLiveRoutingTable(unittest.TestCase):
    """Pins the routing table produced by the real agent configs."""

    EXPECTED = {
        "agent-management": "agent-designer",  # Aki: an ordinary specialist, no bot of his own
        "content-creation": "content-creator",  # Coco: starts content runs, as Elephant starts coding
        "day-planning": "day-planner",
        "fitness-coach": "fitness-coach",
        "general": None,  # nearly every agent lists it: the fixed line
        "home-automation": "home-steward",
        "lifestyle-perk": "reward-travel",
        "market-scout": "market-scout",
        "meal-planning": "meal-planner",
        "note-taking": "note-taker",
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
        cls.configs = _load_agent_configs()

    def _eligible(self, channel):
        found = []
        for agent_id, config in self.configs.items():
            if agent_id == "main":
                continue
            channels = [str(c).lower() for c in (config.get("channels") or [])]
            if "*" in channels:
                continue
            if channel.lower() in channels:
                found.append(agent_id)
        return found

    def _sole_eligible(self, channel):
        eligible = self._eligible(channel)
        return eligible[0] if len(eligible) == 1 else None

    def _decision_for(self, channel):
        main = self.configs["main"]
        loader = MagicMock()
        loader.hosts_channel.side_effect = lambda agent_id, ch: ch in main["channel_hosts"]
        loader.eligible_agents_for_channel.return_value = self._eligible(channel)
        return decide("plan the week", "main", channel, main, loader)

    def test_hosted_channels_match_the_expected_table(self):
        hosts = self.configs["main"]["channel_hosts"]
        actual = {channel: self._sole_eligible(channel) for channel in hosts}
        self.assertEqual(
            actual,
            self.EXPECTED,
            "The routing table changed. A channel gaining or losing an agent silently "
            "switches it between its agent and the fixed line -- confirm that was "
            "intended before updating EXPECTED.",
        )

    def test_general_is_the_only_hosted_channel_without_one_agent(self):
        hosts = self.configs["main"]["channel_hosts"]
        self.assertEqual([c for c in hosts if self._sole_eligible(c) is None], ["general"])

    def test_general_gives_the_fixed_line(self):
        self.assertEqual(self._decision_for("general").target, FIXED)

    def test_every_other_hosted_channel_goes_straight_to_its_agent(self):
        for channel in self.configs["main"]["channel_hosts"]:
            if channel == "general":
                continue
            with self.subTest(channel=channel):
                decision = self._decision_for(channel)
                self.assertEqual(decision.target, DIRECT_CALL)
                self.assertEqual(decision.agent_id, self.EXPECTED[channel])

    def test_aoc_debug_is_not_hosted(self):
        """Unused since dev mode, and hosting it gave the concierge a channel with no agent."""
        self.assertNotIn("aoc-debug", self.configs["main"]["channel_hosts"])

    def test_goal_planning_is_not_hosted(self):
        """It is not a real Discord channel; hosting it only widened main's surface."""
        self.assertNotIn("goal-planning", self.configs["main"]["channel_hosts"])

    def test_wildcard_workers_never_count_as_eligible(self):
        """`graph-worker` is reachable everywhere. Counting it would make every
        channel look shared and give each the fixed line."""
        for worker in ("graph-worker", "graph-worker-low"):
            self.assertEqual(self.configs[worker]["channels"], ["*"])
        self.assertEqual(self._sole_eligible("receipts"), "receipt-processor")

    def test_the_concierge_holds_no_tools(self):
        """Its model runs only under ROUTING_LLM_FALLBACK, in #general. When it
        does, it must not be able to start a multi-hour graph or delegate."""
        self.assertEqual(self.configs["main"].get("tools"), {})

    def test_the_concierge_lists_no_channel_of_its_own(self):
        """`["*"]` is reachability, not ownership. Listing a channel it hosts
        would make the concierge answer it itself (`routing_enabled`)."""
        self.assertEqual(self.configs["main"]["channels"], ["*"])

    def test_no_agent_declares_concierge_prefixes(self):
        """Retired with the concierge's tools; the schema now rejects the key."""
        for agent_id, config in self.configs.items():
            with self.subTest(agent=agent_id):
                self.assertNotIn("concierge_prefixes", config)

    def test_coco_owns_the_content_graph(self):
        coco = self.configs["content-creator"]
        self.assertEqual(coco["channels"], ["content-creation"])
        self.assertIn("graph_call", coco["tools"])
        self.assertIn("graph_status", coco["tools"])

    def test_the_coding_graph_owner_holds_both_graph_tools(self):
        """Split ownership -- one agent starting a run, another reporting on it --
        was rejected as too fragmented, so this pins them together."""
        planner = self.configs["software-planner"]["tools"]
        self.assertIn("graph_call", planner)
        self.assertIn("graph_status", planner)


if __name__ == "__main__":
    unittest.main()
