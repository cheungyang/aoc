"""The live peer cards: who the Switchboard can route to, where each posts, and
what each offers (core/proactive/cards.py).

Pinned like the routing table (tests/core/runtime/foundation/test_routing.py).
Each card lives with its own agent, so nothing else shows the whole team at
once; this table does, and a change to it shows up in review. If it fails, the
question is whether the change was meant -- an offer added or dropped, a
teammate gone -- not whether the table should be updated. `!signals cards`
shows the same cards in full, with what each wants to hear about.
"""
import unittest

from core.loaders.agents_loader import AgentsLoader
from core.proactive.cards import cards

# agent -> (home channel, offers). None: no offers written, so the card is
# built from the agent's description and the Governor accepts any offer.
EXPECTED = {
    "agent-designer": ("agent-management", ("design an agent or skill", "fix an agent's instructions")),
    "ayla-caretaker": ("ayla-care", ("log a milestone", "log health details")),
    "content-creator": ("content-creation", ("turn them into videos and posts",)),
    "day-planner": ("day-planning", ("adjust the daily plan", "a reminder in the morning digest")),
    "excursion-planner": ("weekend-planning", ("activity ideas", "bookings", "calendar holds")),
    "fitness-coach": ("fitness-coach", ("a lighter session", "a shorter workout", "ease back in after a break")),
    "goal-setter": ("project-planning", ("a coaching session", "turn a goal into milestones")),
    "google-knowledge": ("google-knowledge", ("a sparring session", "prep for a meeting or decision")),
    "home-steward": ("home-automation", ("away mode", "automations")),
    "market-scout": ("market-scout", ("research a ticker", "add to the watchlist")),
    "meal-planner": ("meal-planning", ("update the kitchen inventory", "plan meals around events",
                                       "flag food at risk")),
    "note-taker": ("note-taking", ("a sounding-board session", "save it as a note")),
    "property-scout": ("real-estate", ("evaluate a listing", "update the search criteria")),
    "receipt-processor": ("receipts", ("ask for the receipt",)),
    "reward-travel": ("lifestyle-perk", ("award flight search", "best card for a purchase", "statement credits to use")),
    "software-planner": ("software-dev", ("a co-design session", "a design spec")),
    "topic-researcher": ("topic-research", ("syllabi", "topic hubs")),
}


class TestLiveCards(unittest.TestCase):

    def test_the_team_matches_the_expected_cards(self):
        team = cards(AgentsLoader())
        actual = {agent_id: (card.home, None if card.derived else card.offers) for agent_id, card in team.items()}
        self.assertEqual(
            actual,
            EXPECTED,
            "The team's cards changed. Who the Switchboard can route to, and what it may ask "
            "of them, follows from these -- confirm the change was intended before updating EXPECTED.",
        )

    def test_every_card_posts_in_a_channel_of_its_own(self):
        """A delivery goes to the recipient's home channel; a card without one gets nothing."""
        for agent_id, card in cards(AgentsLoader()).items():
            with self.subTest(agent=agent_id):
                self.assertTrue(card.home)


if __name__ == "__main__":
    unittest.main()
