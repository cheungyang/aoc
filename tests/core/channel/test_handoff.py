"""How a hand-over shows in a channel (core/channel/handoff.py): the pointer an
agent's <switch to="..."/> leaves under its reply."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.channel.handoff import switch_pointer


class _Loader:
    """Agent configs, with the lookups `home_channel` makes (core/runtime/foundation/routing.py)."""

    CONFIGS = {
        "main": {"name": "Concierge", "channels": ["*"], "channel_hosts": ["general", "project-planning"]},
        "goal-setter": {"name": "Grace", "emoji": "🚀", "channels": ["project-planning", "general"]},
        "graph-worker": {"name": "Graph Worker", "channels": ["*"], "stateless": True},
    }

    def list_agent_ids(self):
        return list(self.CONFIGS)

    def get_agent_config(self, agent_id):
        return self.CONFIGS.get(agent_id)

    def hosts_channel(self, agent_id, channel):
        return channel in (self.CONFIGS.get(agent_id) or {}).get("channel_hosts", [])

    def eligible_agents_for_channel(self, channel, exclude_agent_id=None):
        return sorted(a for a, c in self.CONFIGS.items()
                      if a != exclude_agent_id and "*" not in c["channels"] and channel in c["channels"])


def test_the_pointer_names_the_teammate_and_where_to_find_them():
    assert switch_pointer("goal-setter", None, loader=_Loader()) == "→ Over to 🚀 Grace in #project-planning"


def test_the_teammate_may_be_named_instead_of_given_by_id():
    assert switch_pointer("grace", None, loader=_Loader()) == "→ Over to 🚀 Grace in #project-planning"


def test_the_channel_is_a_link_where_the_guild_has_it():
    planning = SimpleNamespace(name="project-planning", mention="<#7>")
    source = MagicMock(guild=SimpleNamespace(text_channels=[planning]))
    assert switch_pointer("goal-setter", source, loader=_Loader()) == "→ Over to 🚀 Grace in <#7>"


def test_no_pointer_for_no_one_to_go_to():
    for agent in ("nobody", "main", "graph-worker", ""):
        assert switch_pointer(agent, None, loader=_Loader()) is None, agent
