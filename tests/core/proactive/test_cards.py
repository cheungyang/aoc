"""Peer cards (core/proactive/cards.py)."""
import pytest

from core.proactive import cards as cards_module
from core.proactive.cards import card_for, cards


class FakeLoader:
    """AgentsLoader's surface the cards read, over a dict of configs."""

    def __init__(self, configs):
        self.configs = configs

    def list_agent_ids(self):
        return list(self.configs)

    def get_agent_config(self, agent_id):
        return self.configs.get(agent_id)


@pytest.fixture(autouse=True)
def homes(monkeypatch):
    """Where each agent keeps its conversation: routing.home_channel's answer (tested in routing)."""
    homes = {"meal-planner": "meal-planning", "reward-travel": "lifestyle-perk"}
    monkeypatch.setattr(cards_module, "home_channel", lambda agent_id, loader: homes.get(agent_id))


RAMSAY = {"name": "Ramsay", "emoji": "🧑‍🍳", "channels": ["meal-planning", "general"],
          "memory_topics": ["food", "health", "family"], "description": "Kitchen inventory and meals.",
          "proactivity": {"interests": ["groceries bought", "trips away from home"],
                          "offers": ["update the kitchen inventory", "plan meals around events"]}}
MILES = {"name": "Miles", "emoji": "✈️", "channels": ["lifestyle-perk", "general"],
         "memory_topics": ["travel", "finance"], "description": "Card credits and award travel."}
CONFIGS = {
    "main": {"name": "Concierge", "channels": ["*"], "channel_hosts": ["meal-planning", "lifestyle-perk", "general"]},
    "graph-worker": {"name": "Graph Worker", "channels": ["*"], "stateless": True},
    "script-executor": {"name": "Terminator", "channels": ["general"]},
    "meal-planner": RAMSAY,
    "reward-travel": MILES,
}


def test_only_teammates_with_a_conversation_of_their_own_have_a_card():
    team = cards(FakeLoader(CONFIGS))
    assert sorted(team) == ["meal-planner", "reward-travel"]


def test_a_card_from_the_proactivity_block():
    card = cards(FakeLoader(CONFIGS))["meal-planner"]
    assert not card.derived
    assert card.offers == ("update the kitchen inventory", "plan meals around events")
    assert card.topics == ("food", "health", "family")
    text = card.text()
    assert text.startswith("- meal-planner · Ramsay 🧑‍🍳 · #meal-planning · tags: food, health, family")
    assert "wants to hear about: groceries bought; trips away from home" in text
    assert "can offer: update the kitchen inventory; plan meals around events" in text


def test_a_derived_card_takes_any_offer():
    card = cards(FakeLoader(CONFIGS))["reward-travel"]
    assert card.derived
    assert card.offer_named("track card credits") == "track card credits"
    assert card.offer_named("  ") is None
    assert "about: Card credits and award travel." in card.text()


def test_an_offer_is_named_whatever_the_filler_words():
    card = card_for("meal-planner", RAMSAY, FakeLoader(CONFIGS))
    assert card.offer_named("update kitchen inventory") == "update the kitchen inventory"
    assert card.offer_named("Plan meals around the events") == "plan meals around events"
    assert card.offer_named("update the kitchen inventory with the receipt") == "update the kitchen inventory"
    assert card.offer_named("book a restaurant") is None
