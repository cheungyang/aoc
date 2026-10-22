"""Tests for the channel inbox (core/inbox)."""


class Loader:
    """Literal agent configs, with the lookups graphs/main's router makes."""

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


CONFIGS = {
    "main": {"name": "Concierge", "channels": ["*"], "tools": {"agent_call": {}},
             "voice_config": {"enabled": True},
             "channel_hosts": ["general", "day-planning", "topic-research", "fitness-coach", "software-dev",
                               "pkm-wiki", "market-scout", "real-estate"]},
    "day-planner": {"name": "Daisy", "channels": ["day-planning", "general"]},
    "topic-researcher": {"name": "Ted", "channels": ["topic-research"]},
    "fitness-coach": {"name": "Frankie", "channels": ["fitness-coach"]},
    "wiki-gardener": {"name": "William", "channels": ["pkm-wiki"]},
    "market-scout": {"name": "Marvin", "channels": ["market-scout"]},
    "property-scout": {"name": "Rio", "channels": ["real-estate"]},
}


def loader():
    return Loader(CONFIGS)
