"""friction_query: the learning loop's friction data, read-only, for Kaizen."""
from typing import Literal

from core.storage.learning import LearningStore
from core.tooling import Report, ToolError, agent_tool
from agents.agent_learner.lib import views


@agent_tool
def friction_query(report: Report, view: Literal["patterns", "cards", "episode", "ledger", "proposals"] = "patterns",
                   agent: str = "", detector: str = "", days: int = 14, card: int = 0, limit: int = 10) -> dict:
    """The team's friction, read-only: patterns, cards, one card's episode, the prompt ledger, proposals.

    Reads: memory.db: the friction cards and labels, the session rows and turns around a card, the prompt ledger, and the proposals.
    Computes: patterns (one agent's cards from one detector) with cards, sessions, severity, whether they recur, and the checked proposals open for each; each detector's labeled precision; the turns around a card, cut and clipped.
    Leaves out: what the friction means and what to change (your friction_review skill decides).

    Args:
        view: patterns | cards | episode (needs card) | ledger | proposals.
        agent: Only this agent's ("?" for cards with no agent); empty for all.
        detector: Only this detector's cards (cards view).
        days: How far back, 1-30 (default 14).
        card: The card id, for the episode view.
        limit: How many to return, at most 30 (default 10).
    """
    store = LearningStore()
    if not store.exists():
        raise ToolError("No memory.db yet: the nightly scan hasn't run.")
    report.read(f"memory.db: {view}")
    if view == "patterns":
        return views.patterns_view(store, agent, days, limit)
    if view == "cards":
        return views.cards_view(store, agent, detector, days, limit)
    if view == "episode":
        if card <= 0:
            raise ToolError("The episode view needs `card`, a card id from the patterns or cards view.")
        found = views.episode_view(store, card)
        if found is None:
            raise ToolError(f"No card #{card}.")
        return found
    if view == "ledger":
        return views.ledger_view(store)
    return views.proposals_view(store, days, limit)
