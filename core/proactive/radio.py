"""Team radio: one line per Switchboard route, in a #general thread, for your ✅/❌.

    📻 #42 🧾→🧑‍🍳 now · Groceries from 99 Ranch, Oct 24 · would deliver at once
    📻 #43 🌼→🏕️ today · In Lake Tahoe until Tue · held for your first quiet stretch
    📻 #43 🌼→🛎️ absorb · The house is empty until Tue · the Switchboard asked for no message
    📻 #44 🌿→🔬 dropped · No wiki page for prompt caching · evidence not found in the exchange

React ✅ or ❌ on a line to label its route (a dropped route was never offered,
so its line takes no label). A label:
  - counts toward that kind of nudge's acceptance at once (SignalStore.label_routes);
  - writes one line in main's memory log for tonight's dream
    (core/knowledge/memory/outcomes.py).
❌ also brings a poll under the line: wrong agent · wrong timing · not worth it
· wrong fact. `!signals` (core/runtime/commands/typed/signals.py) shows the
traces and stats, and labels from the keyboard.

The Discord side is a RadioChannel (core/channel/discord/radio.py), so this
module never touches discord itself.
"""
import asyncio
from typing import Any, Callable, Dict, Optional, Protocol, Sequence

from core.knowledge.memory.outcomes import REASONS, record_outcome, route_line
from core.proactive.board import on_board
from core.proactive.cards import PeerCard, cards
from core.storage.signals import SignalStore

GOOD, BAD = "✅", "❌"
_LABELS = {GOOD: "good", BAD: "bad"}
_SHOWN = {"now": "now", "today": "today", "next": "next"}


class RadioChannel(Protocol):
    async def post(self, text: str, reactions: Sequence[str] = ()) -> Optional[str]: ...

    async def poll(self, reply_to: Optional[str], question: str, answers: Sequence[str]) -> Optional[str]: ...


def line(decision: Dict[str, Any], route: Dict[str, Any], team: Dict[str, PeerCard]) -> str:
    """A route's radio line (module docstring)."""
    def emoji(agent_id: Optional[str]) -> str:
        card = team.get(agent_id or "")
        return card.emoji if card else "🤖"

    verdict = route.get("verdict")
    if verdict == "drop":
        shown, detail = "dropped", route.get("reason") or "not allowed"
    elif verdict == "absorb":
        shown, detail = "absorb", route.get("reason") or "good to know; no message"
    else:
        shown = _SHOWN.get(route.get("urgency"), route.get("urgency") or "?")
        name = team[route["to_agent"]].name if route.get("to_agent") in team else route.get("to_agent")
        live = decision.get("mode") == "live"
        detail = {
            "now": "delivering now" if live else "would deliver at once",
            "today": "held for your first quiet stretch",
            "next": f"waits for {name}'s next run or your next conversation with them",
        }.get(route.get("urgency"), "")
        if route.get("reason"):
            detail = f"{detail} ({route['reason']})" if detail else route["reason"]
    source = route.get("source_agent") or ""
    return f"📻 #{decision['id']} {emoji(source)}→{emoji(route.get('to_agent'))} {shown} · {route.get('gist')} · {detail}"


class TeamRadio:
    """Posts each decision's routes and takes your labels (module docstring)."""

    def __init__(self, channel: RadioChannel, store: Optional[SignalStore] = None,
                 team: Callable[[], Dict[str, PeerCard]] = cards):
        self.channel = channel
        self.store = store or SignalStore()
        self.team = team

    async def decided(self, decision: Dict[str, Any]) -> None:
        """One line per route of a recorded decision (the pipeline's Radio)."""
        if not decision or not decision.get("routes"):
            return
        team = self.team()
        for route in decision["routes"]:
            reactions = () if route.get("verdict") == "drop" else (GOOD, BAD)
            message_id = await self.channel.post(line(decision, route, team), reactions)
            if message_id:
                await on_board(self.store.set_radio_message, route["id"], message_id)

    async def reacted(self, message_id: str, emoji: str) -> bool:
        """✅/❌ on a radio line: labels its route. False for any other message."""
        label = _LABELS.get(emoji)
        if label is None:
            return False
        route = await on_board(self.store.route_by_radio_message, message_id)
        if route is None:
            return False
        if route.get("verdict") == "drop":
            return True  # never offered: nothing to label
        await on_board(self.store.label_routes, [route["id"]], label)
        await asyncio.to_thread(record_outcome, route_line(route, label))
        if label == "bad" and not route.get("poll_message_id"):
            poll_id = await self.channel.poll(message_id, f"What was wrong with #{route['decision_id']}?", REASONS)
            if poll_id:
                await on_board(self.store.set_poll_message, route["id"], poll_id)
        return True

    async def voted(self, poll_message_id: str, answer_id: int) -> bool:
        """A vote in a reason poll: the ❌'s reason. False for any other poll."""
        route = await on_board(self.store.route_by_poll_message, poll_message_id)
        if route is None:
            return False
        if 1 <= answer_id <= len(REASONS):
            reason = REASONS[answer_id - 1]
            await on_board(self.store.set_label_reason, [route["id"]], reason)
            await asyncio.to_thread(record_outcome, route_line(route, "bad", reason))
        return True
