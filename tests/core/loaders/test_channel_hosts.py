"""channel_hosts: which channels an agent hosts, "*" included."""
import unittest
from unittest.mock import MagicMock

from core.loaders.channel_hosts import (
    WILDCARD,
    hosted_channels,
    hosts,
    listed_channels,
    named_channels,
    shared_channels,
)

CONFIGS = {
    "main": {"channels": ["*"], "channel_hosts": ["*"], "shared_channels": ["general"]},
    "day-planner": {"channels": ["day-planning", "weekly-planning", "general"]},
    "receipt-processor": {"channels": ["#Receipts", "general"]},
    "graph-worker": {"channels": ["*"], "stateless": True},
    "no-channels": {"name": "No channels"},
}


class TestHosts(unittest.TestCase):
    """"Does it host #x?": what the Discord runner, the router and the scheduler ask."""

    def test_the_wildcard_hosts_every_channel_with_a_name(self):
        """Listed by an agent or not: a channel no agent lists gets the notice that it isn't set up."""
        for channel in ("receipts", "#General", "random", "aoc-voice"):
            with self.subTest(channel=channel):
                self.assertTrue(hosts([WILDCARD], channel))

    def test_nothing_hosts_a_channel_with_no_name(self):
        """A DM has no name: only an entry naming its id hosts it (named_channels)."""
        for value in ([WILDCARD], ["general"]):
            for channel in ("", "  ", "#", None, 42):
                with self.subTest(value=value, channel=channel):
                    self.assertFalse(hosts(value, channel))

    def test_an_explicit_list_hosts_only_its_names(self):
        self.assertTrue(hosts(["General", "#day-planning"], "#general"))
        self.assertTrue(hosts(["General", "#day-planning"], "Day-Planning"))
        self.assertFalse(hosts(["general"], "random"))

    def test_a_value_that_isnt_a_list_hosts_nothing(self):
        for value in (None, "*", {"general": True}, [None, 3, ""]):
            with self.subTest(value=value):
                self.assertFalse(hosts(value, "general"))

    def test_named_channels_leave_the_wildcard_out(self):
        self.assertEqual(named_channels(["*", "General", "123"]), ["general", "123"])
        self.assertEqual(named_channels(["*"]), [])


class TestListedChannels(unittest.TestCase):

    def test_every_channel_an_agent_lists_once_sorted_and_clean(self):
        self.assertEqual(listed_channels(CONFIGS.values()),
                         ["day-planning", "general", "receipts", "weekly-planning"])

    def test_an_agent_reachable_everywhere_owns_no_channel(self):
        """The Concierge and the graph workers list "*": counted, every name
        would be a channel."""
        self.assertEqual(listed_channels([{"channels": ["*", "lounge"]}]), [])

    def test_malformed_configs_list_nothing(self):
        self.assertEqual(listed_channels([None, "x", {"channels": "general"}, {"channels": [3, "", " # "]}]), [])


class TestHostedChannels(unittest.TestCase):
    """"Which channels does it host?": the list the voice focus, the lint and the routing table read."""

    def test_the_wildcard_lists_every_channel_an_agent_lists(self):
        self.assertEqual(hosted_channels([WILDCARD], CONFIGS.values()), listed_channels(CONFIGS.values()))

    def test_an_explicit_list_keeps_its_order_and_reads_no_config(self):
        """The voice rooms and focus follow the list's order; the configs are
        only read for "*"."""
        never = MagicMock()
        never.__iter__.side_effect = AssertionError("read the configs")
        self.assertEqual(hosted_channels(["General", "#day-planning", "general"], never), ["general", "day-planning"])

    def test_a_name_beside_the_wildcard_is_listed_too(self):
        self.assertEqual(hosted_channels(["lounge", "*"], CONFIGS.values()),
                         ["lounge", "day-planning", "general", "receipts", "weekly-planning"])

    def test_a_value_that_isnt_a_list_lists_nothing(self):
        for value in (None, "*", {"general": True}, [None, 3, ""]):
            with self.subTest(value=value):
                self.assertEqual(hosted_channels(value, CONFIGS.values()), [])

    def test_the_wildcard_twice_reads_a_generator_once(self):
        configs = (config for config in CONFIGS.values())
        self.assertEqual(hosted_channels(["*", "*"], configs), listed_channels(CONFIGS.values()))


class TestSharedChannels(unittest.TestCase):
    """Where the host expects no single agent, and gives its fixed line instead of the notice."""

    def test_the_hosts_shared_channels_clean(self):
        self.assertEqual(shared_channels(CONFIGS["main"]), ["general"])
        self.assertEqual(shared_channels({"shared_channels": ["#General", " lounge "]}), ["general", "lounge"])

    def test_none_without_the_key_or_with_a_bad_one(self):
        for config in ({}, None, {"shared_channels": "general"}, {"shared_channels": [None, 3]}):
            with self.subTest(config=config):
                self.assertEqual(shared_channels(config), [])


if __name__ == "__main__":
    unittest.main()
