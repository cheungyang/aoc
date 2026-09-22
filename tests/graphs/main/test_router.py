"""Tests for the deterministic routing decision.

Includes a snapshot of the live routing table. That test is the tripwire for the
main hazard of deriving routing from configuration: adding an agent to a channel
flips that channel from deterministic to LLM with no code change and no other
signal. If it fails, the question is whether the config change was intended --
not whether the test should be updated.
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from graphs.main.router import (
    CONCIERGE,
    DIRECT_CALL,
    PREFIX_CONFIG_KEY,
    concierge_prefixes,
    decide,
    is_bracketed,
    leading_text,
    match_concierge_prefix,
    routing_enabled,
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PREFIXES = ["[main]", "[concierge]", "list all jobs", "jobs", "kill"]
ORCHESTRATOR = {
    "tools": {"agent_call": {}, "job_list": {}},
    PREFIX_CONFIG_KEY: PREFIXES,
}


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


def _match(text, prefixes=None):
    return match_concierge_prefix(text, PREFIXES if prefixes is None else prefixes)


class TestPromptInspection(unittest.TestCase):

    def test_leading_text_of_multimodal(self):
        payload = [
            {"type": "text", "text": "what is this?"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]
        self.assertEqual(leading_text(payload), "what is this?")

    def test_bracketed_prefix_is_stripped(self):
        for raw in ("[main] do the thing", "[concierge] do the thing", "  [MAIN]   do the thing"):
            with self.subTest(raw=raw):
                matched = _match(raw)
                self.assertIsNotNone(matched)
                self.assertEqual(matched[1], "do the thing")

    def test_bare_prefix_is_kept(self):
        """The concierge needs the verb: "kill job abc123" minus "kill" is not
        an instruction it can act on."""
        matched = _match("kill job abc123")
        self.assertIsNotNone(matched)
        self.assertEqual(matched[1], "kill job abc123")

    def test_no_prefix_returns_none(self):
        self.assertIsNone(_match("main, do the thing"))
        self.assertIsNone(_match("do the thing"))
        self.assertIsNone(_match(""))

    def test_bracketed_prefix_preserves_attachments(self):
        payload = [
            {"type": "text", "text": "[main] look at this"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]
        matched = _match(payload)
        self.assertIsNotNone(matched)
        self.assertEqual(matched[1][0], {"type": "text", "text": "look at this"})
        self.assertEqual(matched[1][1], payload[1])

    def test_bare_prefixes_match_only_at_the_start(self):
        for text in ("jobs", "kill job abc123", "list all jobs"):
            with self.subTest(text=text):
                self.assertIsNotNone(_match(text))

    def test_the_matched_prefix_is_reported(self):
        """`decide` puts this in its reason, so it has to be the entry that
        actually fired rather than merely 'something matched'."""
        self.assertEqual(_match("kill job abc123")[0], "kill")
        self.assertEqual(_match("[main] hello")[0], "[main]")

    def test_longer_bare_prefixes_win_when_ordered_first(self):
        """Matching is first-wins, so config order is what distinguishes
        "list all jobs" from the broader "jobs"."""
        self.assertEqual(_match("list all jobs")[0], "list all jobs")

    def test_cancel_is_deliberately_not_a_prefix(self):
        """`cancel` is a synonym for `job_kill`, but as a message opener it is
        far more often domain content -- "cancel the 7pm table", "cancel my
        meal plan" -- and stealing those from the specialist is worse than
        making the rare job-kill go through `[main]`."""
        for text in ("cancel the sync", "cancel the 7pm reservation"):
            with self.subTest(text=text):
                self.assertIsNone(_match(text))

    def test_bare_prefixes_do_not_steal_ordinary_messages(self):
        """The anchoring is the point: these are the sentences a loose keyword
        list would misroute away from the specialist."""
        for text in (
            "what jobs did I log this week?",
            "the cancel policy on this hotel is strict",
            "should I kill this project?",
            "killing time before the flight",
        ):
            with self.subTest(text=text):
                self.assertIsNone(_match(text))

    def test_bracketed_form_is_what_selects_the_behaviour(self):
        self.assertTrue(is_bracketed("[main]"))
        self.assertFalse(is_bracketed("kill"))
        self.assertFalse(is_bracketed("[unclosed"))


class TestPrefixConfig(unittest.TestCase):
    """Reading the list out of `agent.json`."""

    def test_missing_key_yields_no_prefixes(self):
        self.assertEqual(concierge_prefixes({}), [])
        self.assertEqual(concierge_prefixes({PREFIX_CONFIG_KEY: None}), [])

    def test_blanks_and_non_strings_are_ignored(self):
        config = {PREFIX_CONFIG_KEY: ["[main]", "  ", "", None, 7, " kill "]}
        self.assertEqual(concierge_prefixes(config), ["[main]", "kill"])

    def test_a_bare_string_is_accepted_as_one_entry(self):
        self.assertEqual(concierge_prefixes({PREFIX_CONFIG_KEY: "[main]"}), ["[main]"])

    def test_without_config_nothing_is_claimed_for_the_concierge(self):
        """An orchestrator that declares no prefixes routes everything. The
        escape hatch is a config feature, not a built-in."""
        self.assertIsNone(_match("[main] do the thing", prefixes=[]))
        self.assertIsNone(_match("kill job abc123", prefixes=[]))


class TestRoutingEnabled(unittest.TestCase):

    def test_requires_agent_call_and_hosting(self):
        loader = _loader(hosts=("receipts",))
        self.assertTrue(routing_enabled("main", "receipts", ORCHESTRATOR, loader))

    def test_specialist_without_agent_call_never_routes(self):
        """Every agent shares this graph, so a specialist must fall straight
        through to its own ReAct behaviour."""
        loader = _loader(hosts=("agent-management",))
        self.assertFalse(
            routing_enabled("agent-designer", "agent-management", {"tools": {"filesystem": {}}}, loader)
        )

    def test_non_hosted_channel_does_not_route(self):
        loader = _loader(hosts=("receipts",))
        self.assertFalse(routing_enabled("main", "some-other-channel", ORCHESTRATOR, loader))

    def test_missing_channel_does_not_route(self):
        self.assertFalse(routing_enabled("main", "", ORCHESTRATOR, _loader()))


class TestDecide(unittest.TestCase):

    def test_sole_eligible_agent_is_called_directly(self):
        loader = _loader(hosts=("receipts",), eligible=["receipt-processor"])
        decision = decide("scan this", "main", "receipts", ORCHESTRATOR, loader)

        self.assertEqual(decision.target, DIRECT_CALL)
        self.assertEqual(decision.agent_id, "receipt-processor")
        self.assertTrue(decision.is_direct)

    def test_two_eligible_agents_fall_back_to_the_model(self):
        loader = _loader(hosts=("weekend-planning",), eligible=["day-planner", "excursion-planner"])
        decision = decide("plan my weekend", "main", "weekend-planning", ORCHESTRATOR, loader)

        self.assertEqual(decision.target, CONCIERGE)
        self.assertIsNone(decision.agent_id)

    def test_no_eligible_agent_falls_back_to_the_model(self):
        loader = _loader(hosts=("aoc-debug",), eligible=[])
        decision = decide("hello", "main", "aoc-debug", ORCHESTRATOR, loader)
        self.assertEqual(decision.target, CONCIERGE)

    def test_bracketed_prefix_wins_over_a_sole_agent(self):
        loader = _loader(hosts=("receipts",), eligible=["receipt-processor"])
        decision = decide("[main] what did we decide?", "main", "receipts", ORCHESTRATOR, loader)

        self.assertEqual(decision.target, CONCIERGE)
        self.assertEqual(decision.prompt, "what did we decide?")
        self.assertEqual(decision.reason, "concierge prefix '[main]'")

    def test_bare_prefix_wins_over_a_sole_agent(self):
        loader = _loader(hosts=("receipts",), eligible=["receipt-processor"])
        decision = decide("jobs", "main", "receipts", ORCHESTRATOR, loader)
        self.assertEqual(decision.target, CONCIERGE)
        self.assertEqual(decision.prompt, "jobs")
        self.assertEqual(decision.reason, "concierge prefix 'jobs'")

    def test_prefixes_come_from_config_not_code(self):
        """The same message routes differently under a different config, which
        is the whole point of moving the list into `agent.json`."""
        loader = _loader(hosts=("receipts",), eligible=["receipt-processor"])
        config = {"tools": {"agent_call": {}}, PREFIX_CONFIG_KEY: ["[boss]"]}

        self.assertEqual(
            decide("[boss] hello", "main", "receipts", config, loader).target, CONCIERGE
        )
        # `[main]` is not declared here, so it is just text.
        self.assertEqual(
            decide("[main] hello", "main", "receipts", config, loader).target, DIRECT_CALL
        )

    def test_the_caller_excludes_itself(self):
        loader = _loader(hosts=("receipts",), eligible=["receipt-processor"])
        decide("scan", "main", "receipts", ORCHESTRATOR, loader)
        loader.eligible_agents_for_channel.assert_called_once_with("receipts", exclude_agent_id="main")


class TestLiveRoutingTable(unittest.TestCase):
    """Pins the routing table produced by the real agent configs."""

    EXPECTED = {
        "aoc-debug": None,
        "content-creation": None,
        "day-planning": "day-planner",
        "general": None,
        "home-automation": "home-steward",
        "lifestyle-perk": "reward-travel",
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
        cls.configs = _load_agent_configs()

    def _sole_eligible(self, channel):
        eligible = []
        for agent_id, config in self.configs.items():
            if agent_id == "main":
                continue
            channels = [str(c).lower() for c in (config.get("channels") or [])]
            if "*" in channels:
                continue
            if channel.lower() in channels:
                eligible.append(agent_id)
        return eligible[0] if len(eligible) == 1 else None

    def test_hosted_channels_match_the_expected_table(self):
        hosts = self.configs["main"]["channel_hosts"]
        actual = {channel: self._sole_eligible(channel) for channel in hosts}
        self.assertEqual(
            actual,
            self.EXPECTED,
            "The routing table changed. A channel gaining or losing an agent silently "
            "switches it between deterministic routing and the LLM concierge -- confirm "
            "that was intended before updating EXPECTED.",
        )

    def test_goal_planning_is_not_hosted(self):
        """It is not a real Discord channel; hosting it only widened main's surface."""
        self.assertNotIn("goal-planning", self.configs["main"]["channel_hosts"])

    def test_wildcard_workers_never_count_as_eligible(self):
        """`graph-worker` is reachable everywhere. Counting it would make every
        channel look crowded and disable routing across the board."""
        for worker in ("graph-worker", "graph-worker-low"):
            self.assertEqual(self.configs[worker]["channels"], ["*"])
        self.assertEqual(self._sole_eligible("receipts"), "receipt-processor")

    def test_most_hosted_channels_are_deterministic(self):
        hosts = self.configs["main"]["channel_hosts"]
        deterministic = [c for c in hosts if self._sole_eligible(c)]
        self.assertGreaterEqual(len(deterministic), 11)

    def test_main_declares_its_concierge_prefixes(self):
        """Now that the list lives in config, deleting the key would remove the
        only way to reach the concierge in the 11 deterministic channels -- and
        would do it silently, since routing would simply carry on working."""
        declared = concierge_prefixes(self.configs["main"])
        self.assertIn("[main]", declared)
        self.assertIn("[concierge]", declared)

    def test_bare_prefixes_correspond_to_tools_main_actually_holds(self):
        """A bare verb earns its place by naming something main can do. `kill`
        without `job_kill` would route the message to an agent that then has to
        explain it cannot help."""
        main = self.configs["main"]
        bare = [p for p in concierge_prefixes(main) if not is_bracketed(p)]
        self.assertTrue(bare, "precondition: main declares at least one bare verb")
        self.assertTrue(
            any(t.startswith("job_") for t in main["tools"]),
            f"main declares {bare} but holds no job tools to service them",
        )

    def test_longer_bare_prefixes_are_declared_before_their_substrings(self):
        """Matching is first-wins, so "jobs" listed above "list all jobs" would
        make the longer entry unreachable and misreport the rule in the log."""
        declared = concierge_prefixes(self.configs["main"])
        for i, prefix in enumerate(declared):
            for longer in declared[i + 1:]:
                self.assertFalse(
                    longer.startswith(prefix + " "),
                    f"'{longer}' can never match: '{prefix}' precedes it",
                )

    def test_only_the_orchestrator_declares_prefixes(self):
        """`graphs/main` is every agent's default graph. A specialist declaring
        prefixes would suggest it routes, which `routing_enabled` denies."""
        for agent_id, config in self.configs.items():
            if agent_id == "main":
                continue
            with self.subTest(agent=agent_id):
                self.assertEqual(concierge_prefixes(config), [])


class TestGraphToolsDoNotLeak(unittest.TestCase):
    """`graph_call` must stay invisible wherever routing is deterministic.

    Main keeps `graph_call`/`graph_status` only for `content_creation`, whose
    channel has no eligible agent and so still reaches the concierge. Those
    grants are held by main's *LLM*; a deterministic turn never constructs it,
    so the tools are never offered. This pins that by construction rather than
    by inspection -- the failure it guards against is a future channel losing
    its sole specialist and quietly handing an unrelated conversation a tool
    that can start a multi-hour graph run.
    """

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

    def _decision_for(self, channel):
        main = self.configs["main"]
        loader = MagicMock()
        loader.hosts_channel.side_effect = (
            lambda agent_id, ch: ch in main["channel_hosts"]
        )
        loader.eligible_agents_for_channel.return_value = self._eligible(channel)
        return decide("plan the week", "main", channel, main, loader)

    def test_deterministic_channels_never_reach_the_concierge(self):
        main = self.configs["main"]
        self.assertIn("graph_call", main["tools"], "precondition: main still holds graph_call")

        for channel in main["channel_hosts"]:
            if len(self._eligible(channel)) != 1:
                continue
            with self.subTest(channel=channel):
                decision = self._decision_for(channel)
                self.assertEqual(
                    decision.target,
                    DIRECT_CALL,
                    f"#{channel} would build main's LLM, exposing graph_call",
                )

    def test_concierge_channels_are_the_only_holders_of_graph_tools(self):
        main = self.configs["main"]
        concierge_channels = [
            c for c in main["channel_hosts"] if self._decision_for(c).target == CONCIERGE
        ]
        self.assertEqual(sorted(concierge_channels), ["aoc-debug", "content-creation", "general"])

    def test_the_coding_graph_owner_holds_both_graph_tools(self):
        """Split ownership -- one agent starting a run, another reporting on it --
        was rejected as too fragmented, so this pins them together."""
        planner = self.configs["software-planner"]["tools"]
        self.assertIn("graph_call", planner)
        self.assertIn("graph_status", planner)


if __name__ == "__main__":
    unittest.main()
