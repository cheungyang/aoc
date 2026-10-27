"""Peer cards: what each teammate wants to hear about, and what it can offer.

The Switchboard matches what just happened against these cards. The Governor
then checks that each route names one of the recipient's offers. A card comes
from the agent's `proactivity` block in agent.json:

    "proactivity": {"interests": ["groceries bought", ...],
                    "offers": ["update the kitchen inventory", ...]}

An agent without a block gets a derived card: its description says what it
cares about, and any offer is accepted (`derived`). So a new agent joins the
team just by describing itself, and no other agent changes. The review
record's §7.1 has the first cards; S6 writes one for every agent.

Who has a card: every agent that runs a model and keeps a conversation in a
channel of its own. These don't:
  - the Concierge and the graph workers (`channels: ["*"]`);
  - stateless agents;
  - script-executor.
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from core.loaders.agents_loader import AgentsLoader
from core.runtime.agent_factory import SCRIPT_EXECUTOR
from core.runtime.foundation.routing import home_channel

# Words that don't decide whether a route names an offer.
_FILLER = frozenset({"a", "an", "the", "to", "for", "of", "and", "or", "on", "in", "with", "my", "your", "their"})
_WORD = re.compile(r"[a-z0-9]+")


def offer_words(text: str):
    return frozenset(w for w in _WORD.findall((text or "").lower()) if w not in _FILLER)


@dataclass(frozen=True)
class PeerCard:
    agent_id: str
    name: str
    emoji: str
    home: str                    # the channel it keeps its conversation in, or ""
    description: str
    interests: Tuple[str, ...]
    offers: Tuple[str, ...]
    topics: Tuple[str, ...]      # memory_topics: the tags it reads, and so is subscribed to
    derived: bool                # no `proactivity` block: any offer is accepted

    def offer_named(self, offer: str) -> Optional[str]:
        """The card's offer that `offer` names, or None when it names none.

        Wording may differ, but one offer's words must contain the other's
        ("update kitchen inventory" names "update the kitchen inventory"). A
        derived card has no list, so any offer that isn't empty is accepted as is.
        """
        words = offer_words(offer)
        if not words:
            return None
        if self.derived:
            return offer.strip()
        for own in self.offers:
            mine = offer_words(own)
            if mine and (mine <= words or words <= mine):
                return own
        return None

    def text(self) -> str:
        """The card as the Switchboard reads it."""
        where = f" · #{self.home}" if self.home else ""
        topics = f" · tags: {', '.join(self.topics)}" if self.topics else ""
        lines = [f"- {self.agent_id} · {self.name} {self.emoji}{where}{topics}".rstrip()]
        if self.derived:
            lines.append(f"  about: {self.description}")
            lines.append("  can offer: anything within that")
        else:
            lines.append(f"  wants to hear about: {'; '.join(self.interests)}")
            lines.append(f"  can offer: {'; '.join(self.offers)}")
        return "\n".join(lines)


def card_for(agent_id: str, config: Dict[str, Any], loader: Any) -> Optional[PeerCard]:
    """`agent_id`'s card, or None for an agent that has none (module docstring)."""
    channels = [str(c).lower() for c in (config.get("channels") or [])]
    if agent_id == SCRIPT_EXECUTOR or not channels or "*" in channels or config.get("stateless"):
        return None
    block = config.get("proactivity") or {}
    interests = tuple(str(i).strip() for i in block.get("interests") or [] if str(i).strip())
    offers = tuple(str(o).strip() for o in block.get("offers") or [] if str(o).strip())
    return PeerCard(
        agent_id=agent_id,
        name=config.get("name") or agent_id,
        emoji=config.get("emoji") or "🤖",
        home=home_channel(agent_id, loader) or "",
        description=" ".join(str(config.get("description") or "").split()),
        interests=interests,
        offers=offers,
        topics=tuple(config.get("memory_topics") or ()),
        derived=not offers,
    )


def cards(loader: Any = None) -> Dict[str, PeerCard]:
    """Every teammate's card, by agent id."""
    loader = loader or AgentsLoader()
    out = {}
    for agent_id in loader.list_agent_ids():
        card = card_for(agent_id, loader.get_agent_config(agent_id) or {}, loader)
        if card is not None:
            out[agent_id] = card
    return out
