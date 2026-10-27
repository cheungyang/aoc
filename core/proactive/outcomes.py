"""Outcomes: what came of each proactive post (stage S4; proposal §7.7, D9).

A proactive turn's post (delivery.py) gets one outcome, decided by code, with
no model:

  declined  ❌ on one of its messages; or your first turn with the recipient
            after the post, within OUTCOME_S, is a short no (below)
  engaged   ✅ on one of its messages; or that first turn otherwise: a reply, a
            poll vote (it reaches the recipient as your message), asking it to act
  ignored   none of the above within OUTCOME_S

A short no, whatever its case and punctuation: the whole reply is one of
DECLINE, or the reply is at most SHORT_WORDS words and starts with one ("not
now, maybe later"). A longer reply that starts with one is engaged ("skip the
eggs, add the rest"), and so is one that starts with a NOT_DECLINE phrase ("no
problem, add them"): a false decline would hold back a useful kind of nudge,
and ❌ is there for a plain no.

The first outcome sticks, except that ❌ within OUTCOME_S turns engaged into
declined. A route handed to a scheduled run (a `next` route) gets none: that
run's post says nothing about the item. Nor does a silent or failed turn.

An outcome lands on the route (SignalStore.set_outcome) and counts toward its
kind of nudge's acceptance, what the Governor's "worth posting?" draw samples,
unless you labeled the route: a route counts once, and your label wins. It also
writes one line in main's memory log (core/knowledge/memory/outcomes.py). Every
bot runner may offer the same reaction, so a repeat changes nothing, and writes
nothing.

Where it runs:
  - the service's sweep, each minute, after `resolve`: `settle` decides each
    post with no outcome yet from the turn ledger, once its evidence is in;
  - a reaction listener (core/channel/discord/radio.py, `on_reaction`), offered
    each ✅/❌ after team radio's own: `reacted`. It always answers False, so the
    runner goes on, and a ✅ still finishes the channel's inbox row.
Either way, what the ledger already says comes first: a reaction settles its
post from the ledger before it counts, so the sweep's timing changes nothing.

It needs no switch of its own: only a live post has an outcome to find.
"""
import asyncio
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.knowledge.memory.outcomes import outcome_line, record_outcome
from core.proactive.board import on_board
from core.storage.signals import DECLINED, ENGAGED, IGNORED, POSTED, SignalStore

# How long a post waits for its outcome: with nothing heard by then, it was ignored.
OUTCOME_S = 24 * 3600
# A short no: the whole of your reply, or how a reply of at most SHORT_WORDS words starts.
DECLINE = ("no", "nope", "no thanks", "not now", "not interested", "stop", "skip", "pass", "don't", "leave it")
SHORT_WORDS = 4
# Replies that start like a no, and aren't one.
NOT_DECLINE = ("no problem", "no worries", "no rush", "no doubt")
GOOD, BAD = "✅", "❌"

_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> List[str]:
    """`text` as lower-case words: an apostrophe joins its word ("don't" reads "dont"),
    and any other punctuation separates two."""
    return _WORD.findall((text or "").lower().replace("\u2019", "'").replace("'", ""))


# Longest first, so a reply is named by the longest phrase it starts with ("no thanks", not "no").
_DECLINES: List[Tuple[List[str], str]] = sorted(((words(phrase), phrase) for phrase in DECLINE),
                                                 key=lambda item: len(item[0]), reverse=True)
_NOT_DECLINES = [words(phrase) for phrase in NOT_DECLINE]


def declined_with(text: str) -> Optional[str]:
    """The DECLINE phrase that makes `text` a short no (module docstring); None if it isn't one.
    "No, thanks!" and "not now, maybe later" decline; "noted", "skip the eggs, add the rest"
    and "no problem" don't."""
    said = words(text)
    if any(said[:len(phrase)] == phrase for phrase in _NOT_DECLINES):
        return None
    for phrase, written in _DECLINES:
        if said == phrase or (len(said) <= SHORT_WORDS and said[:len(phrase)] == phrase):
            return written
    return None


class Outcomes:
    """Decides what came of each proactive post (module docstring): `settle` on the
    service's sweep, `reacted` for each ✅/❌ on Discord. `record` writes main's
    memory-log line."""

    def __init__(self, store: Optional[SignalStore] = None, clock: Callable[[], float] = time.time,
                 record: Callable[[str], Any] = record_outcome):
        self.store = store or SignalStore()
        self.clock = clock
        self.record = record

    async def settle(self) -> None:
        """Decides each post with no outcome yet whose evidence is in: your first turn with
        its recipient since it posted, or OUTCOME_S without one. Never raises: a post that
        fails waits for the next sweep, and doesn't hold back the others."""
        try:
            posts = await on_board(self.store.unsettled_posts)
        except Exception as e:
            print(f"[proactive] Could not settle the posts' outcomes: {type(e).__name__}: {e}")
            return
        now = self.clock()
        for post in posts:
            try:
                await self._settle(post, now)
            except Exception as e:
                print(f"[proactive] Could not settle the post of route #{post.get('route_id')}: "
                      f"{type(e).__name__}: {e}")

    async def reacted(self, message_id: str, emoji: str) -> bool:
        """✅ or ❌ on one of a proactive post's messages: engaged or declined, within
        OUTCOME_S. Always False: the runner goes on, so a ✅ still finishes the
        channel's inbox row. Never raises."""
        outcome = {GOOD: ENGAGED, BAD: DECLINED}.get(emoji)
        if outcome is None:
            return False
        try:
            post = await on_board(self.store.post_by_message, message_id)
            if post is None or post["status"] != POSTED or post["finished_at"] is None:
                return False
            now = self.clock()
            await self._settle(post, now)  # what the ledger already says comes first
            if now - post["finished_at"] <= OUTCOME_S:
                await self._decide(post, outcome, now, f"they reacted {emoji}",
                                   replaces=(ENGAGED,) if outcome == DECLINED else ())
        except Exception as e:
            print(f"[proactive] Could not take {emoji} on message {message_id}: {type(e).__name__}: {e}")
        return False

    async def _settle(self, post: Dict[str, Any], now: float) -> None:
        """`post`'s outcome from the turn ledger, if it has none and its evidence is in."""
        posted_at = post.get("finished_at")
        if post.get("outcome") or posted_at is None:
            return
        turn = await on_board(self.store.first_user_turn, post["to_agent"], after=posted_at,
                              until=posted_at + OUTCOME_S)
        if turn is not None:
            said = " ".join((turn.get("user_text") or "").split())
            if declined_with(said):
                await self._decide(post, DECLINED, turn["at"], f"they said \"{said}\"")  # a short reply: quoted whole
            else:
                await self._decide(post, ENGAGED, turn["at"], "they replied")
        elif now - posted_at >= OUTCOME_S:
            await self._decide(post, IGNORED, posted_at + OUTCOME_S, "no reply or reaction within a day")

    async def _decide(self, post: Dict[str, Any], outcome: str, at: float, how: str,
                      replaces: Sequence[str] = ()) -> None:
        """Sets `outcome` on the post's route, and writes its line: only if that changed it."""
        if not await on_board(self.store.set_outcome, post["route_id"], outcome, at=at, replaces=replaces):
            return
        post["outcome"] = outcome
        print(f"[proactive] The post of route #{post['route_id']}: {outcome} ({how}).")
        route = await on_board(self.store.route, post["route_id"])
        if route is not None:
            await asyncio.to_thread(self.record, outcome_line(route, outcome, how))
