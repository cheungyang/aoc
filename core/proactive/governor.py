"""The Governor: what each of the Switchboard's routes may become.

The Switchboard proposes and code disposes. Every rule here is code, and no
number comes from a model. Each route gets one verdict:

  deliver   a proactive turn for the recipient at `deliver_at`. Live (stage
            S4) that is a post in the recipient's own channel; in shadow it is
            only the time it would have been.
  absorb    no post: the fact is only good to know (Live Context, S4)
  drop      not allowed, with the reason

The rules, in order. The first one that settles a route decides:

  evidence      the signal's evidence must be in the exchange, word for word → drop
  until         a situation needs `until`, and it mustn't be past → drop
  recipient     a teammate with a card, not the one the exchange was with → drop
  offer         one of the recipient's offers (any, for a derived card) → drop
  window        if given, it must overlap the next 7 days → drop
  sensitivity   a fact tagged health or finance reaches only a teammate that
                reads that tag (its memory_topics) → drop
  ownership     the recipient must be answered by this host → drop
  dedupe        the same fact to the same teammate within 72 h → drop
  absorb asked  the Switchboard asked for no message → absorb
  cascade       a signal from a proactive turn (hops ≥ 1) can't start another → absorb
  active        you've talked to the recipient in the last 10 minutes → absorb
  urgency cap   `now` only for an artifact, a request, or a window opening
                within 2 h; otherwise `today`
  worth posting a draw from the kind's acceptance below WORTH_POSTING → absorb.
                Thompson sampling, per recipient/kind/tag:
                Beta(2 + engaged, 1 + ignored + declined). A new kind gets
                the benefit of the doubt (posted about 9 times in 10). One you
                keep declining fades: about half are absorbed after 3
                declines, three in four after 6. It still gets an occasional
                chance.

When a delivery happens (`deliver_at`):
  - `now` happens at once.
  - `today` happens at the first 30-minute quiet stretch. If the window
    closes first, it moves to the next touchpoint.
  - `next` happens at the recipient's next scheduled run or your next
    conversation with it.
`today` and `next` times are only known later; `resolve_times` fills them in
as the Board sees them happen.

Dormant levers: designed, but not built. Each one is switched on when its
condition holds, and decided at the gate named (review record §7.5):

  quiet_hours         hold today/next deliveries overnight. When: the "would
                      deliver" times in shadow include ones you don't like, or a
                      night post annoys you. G3/G4.
  delivery_window     deliver `today` only within set hours, and by a set time at
                      the latest. When: `today` items land late or at odd hours. G4.
  per_signal_cap      at most 2 posts from one signal. When: one signal fans out
                      to 3 or more posts. G3/G4.
  per_agent_daily_cap at most N posts per agent per day. When: an agent posts
                      more than 3 a day for a week. G4/G6.
  global_daily_cap    at most N posts a day. When: more than 10 posts a day in
                      total. G4/G6.
  long_shot_cap       at most 1 low-acceptance nudge a day. When: long shots are
                      more than 20% of posts, or you decline many of them. G6.
  open_follow_ups_cap at most N open follow-ups per agent. When: one agent keeps
                      more than 5 open, or checks cost more than expected. G5.
"""
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from core.channel.discord.ownership import Ownership
from core.proactive.cards import PeerCard
from core.proactive.switchboard import ProposedRoute, ProposedSignal, local
from core.storage.signals import SignalStore

DELIVER, ABSORB, DROP = "deliver", "absorb", "drop"

# Tags whose facts reach only the teammates that read them (D10, decided at G3).
SENSITIVE_TAGS = frozenset({"health", "finance"})
DEDUPE_S = 72 * 3600
ACTIVE_S = 10 * 60
QUIET_STRETCH_S = 30 * 60
NOW_WINDOW_S = 2 * 3600
HORIZON_DAYS = 7
# The acceptance draw a nudge needs to be worth a post rather than absorbed,
# and the prior every kind starts from: a kind with no history is posted about
# 9 times in 10 (calibrated at G3, from shadow data).
WORTH_POSTING = 0.3
PRIOR_ENGAGED, PRIOR_DECLINED = 2, 1
# Kinds that may be delivered `now`: something was made, or the user asked.
NOW_KINDS = frozenset({"artifact", "request"})

_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                         "\u2014": "-", "\u2026": "..."})
_EDGES = " \"'.,;:!?-"


@dataclass
class Verdict:
    verdict: str
    urgency: str             # as granted: now, today, next, or absorb
    reason: str = ""
    offer: str = ""          # the recipient's offer the route named
    kind_key: str = ""
    sampled: Optional[float] = None
    deliver_at: Optional[float] = None


def normalize(text: str) -> str:
    return " ".join((text or "").translate(_QUOTES).casefold().split())


def evidence_found(evidence: str, exchange: str) -> bool:
    """Whether `evidence` is in `exchange`, word for word, ignoring case,
    spacing and curly quotes. An ellipsis may join pieces quoted in order."""
    wanted = normalize(evidence).strip(_EDGES)
    if len(wanted) < 3:
        return False
    text = normalize(exchange)
    if wanted in text:
        return True
    pieces = [p.strip(_EDGES) for p in wanted.split("...")]
    pieces = [p for p in pieces if len(p) >= 3]
    if len(pieces) < 2:
        return False
    position = 0
    for piece in pieces:
        position = text.find(piece, position)
        if position < 0:
            return False
        position += len(piece)
    return True


def kind_key(to: str, signal: ProposedSignal) -> str:
    """What acceptance is counted per: recipient/kind/first tag."""
    return f"{to}/{signal.kind}/{signal.tags[0] if signal.tags else '-'}"


def _day(text: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


class Governor:
    """Judges proposals against the rules in the module docstring."""

    def __init__(self, store: SignalStore, cards: Dict[str, PeerCard], *,
                 owns: Optional[Callable[[str], bool]] = None, rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.time):
        self.store = store
        self.cards = cards
        self.owns = owns or (lambda agent_id: Ownership().is_mine(agent_id))
        self.rng = rng or random.Random()
        self.clock = clock

    def signal_problem(self, signal: ProposedSignal, exchange: str) -> Optional[str]:
        """Why every route of `signal` is dropped, or None."""
        if not evidence_found(signal.evidence, exchange):
            return "evidence not found in the exchange"
        today = local(self.clock()).date()
        until = _day(signal.until)
        if signal.kind == "situation" and until is None:
            return "a situation needs an until date"
        if until is not None and until < today:
            return f"until {signal.until} is past"
        return None

    def judge(self, signal: ProposedSignal, route: ProposedRoute, *, source_agent: str, hops: int = 0,
              dedupe_key: str = "", signal_problem: Optional[str] = None) -> Verdict:
        """The verdict on one route of `signal`, a signal from `source_agent`'s exchange."""
        now = self.clock()
        asked = route.urgency if route.urgency in ("now", "today", "next", "absorb") else "today"
        key = kind_key(route.to, signal)

        def drop(reason: str) -> Verdict:
            return Verdict(DROP, asked, reason, kind_key=key)

        if signal_problem:
            return drop(signal_problem)
        card = self.cards.get(route.to)
        if card is None:
            return drop(f"{route.to or 'nobody'} is not a teammate with a card")
        if route.to == source_agent:
            return drop("the exchange was with that teammate already")
        offer = card.offer_named(route.offer)
        if offer is None:
            return drop(f"{route.offer!r} isn't one of {card.name}'s offers")
        if not self._window_in_horizon(route, now):
            return drop("its window isn't in the next 7 days")
        tagged = sorted(SENSITIVE_TAGS.intersection(signal.tags))
        missing = [tag for tag in tagged if tag not in card.topics]
        if missing:
            return drop(f"tagged {', '.join(missing)}, which {card.name} doesn't read")
        if not tagged and signal.sensitivity == "sensitive" and not SENSITIVE_TAGS.intersection(card.topics):
            return drop(f"sensitive, and {card.name} reads neither health nor finance")
        if not self.owns(route.to):
            return drop(f"{card.name} is answered by another host")
        if self.store.recent_route(to_agent=route.to, dedupe_key=dedupe_key, since=now - DEDUPE_S):
            return drop(f"{card.name} already had this within 72 h")

        def absorb(reason: str) -> Verdict:
            return Verdict(ABSORB, "absorb", reason, offer=offer, kind_key=key)

        if asked == "absorb":
            return absorb("the Switchboard asked for no message")
        if hops >= 1:
            return absorb("it came from a proactive turn, which can't start another")
        last = self.store.last_turn_at(agent_id=route.to)
        if last is not None and now - last < ACTIVE_S:
            return absorb(f"you're talking to {card.name} right now")

        urgency, reason = asked, ""
        if urgency == "now" and not self._may_be_now(signal, route, now):
            urgency, reason = "today", "now only for an artifact, a request, or a window opening within 2 h"
        counts = self.store.acceptance(key)
        sampled = self.rng.betavariate(PRIOR_ENGAGED + counts["engaged"],
                                       PRIOR_DECLINED + counts["ignored"] + counts["declined"])
        if sampled < WORTH_POSTING:
            verdict = absorb(f"not worth a post yet (acceptance draw {sampled:.2f})")
            verdict.sampled = sampled
            return verdict
        return Verdict(DELIVER, urgency, reason, offer=offer, kind_key=key, sampled=sampled,
                       deliver_at=now if urgency == "now" else None)

    def _may_be_now(self, signal: ProposedSignal, route: ProposedRoute, now: float) -> bool:
        if signal.kind in NOW_KINDS:
            return True
        start = _day(route.window_start)
        if start is None:
            return False
        opens = datetime.combine(start, datetime.min.time(), tzinfo=local(now).tzinfo).timestamp()
        return opens - now <= NOW_WINDOW_S

    def _window_in_horizon(self, route: ProposedRoute, now: float) -> bool:
        start, end = _day(route.window_start), _day(route.window_end)
        if start is None and end is None:
            return True
        today = local(now).date()
        horizon = today + timedelta(days=HORIZON_DAYS)
        return (start or end) <= horizon and (end or start) >= today


def resolve_times(store: SignalStore, now: Optional[float] = None, quiet_s: float = QUIET_STRETCH_S) -> List[int]:
    """Sets `deliver_at` on the `today` and `next` routes whose time has come to be
    known, and returns their ids (module docstring, "When a delivery happens").

    A `today` route waits for 30 minutes without a turn of yours. If its
    window closes first, it waits for the next touchpoint instead.
    """
    now = time.time() if now is None else now
    resolved = []
    for route in store.undelivered():
        since = route["signal_at"]
        at = None
        if route["urgency"] == "today":
            at = quiet_stretch(store.user_turn_times(since - quiet_s), since, now, quiet_s)
            closes = _window_close(route.get("window_end"), since)
            if closes is not None and ((at is None and now > closes) or (at is not None and at > closes)):
                store.set_route_timing(route["id"], urgency="next",
                                       reason="no quiet stretch before its window closed")
                continue
        elif route["urgency"] == "next":
            touch = store.next_turn_of(route["to_agent"], after=since)
            at = touch["at"] if touch else None
        if at is not None:
            store.set_route_timing(route["id"], deliver_at=at)
            resolved.append(route["id"])
    return resolved


def quiet_stretch(activity: List[float], since: float, now: float, quiet_s: float) -> Optional[float]:
    """When the first `quiet_s` without activity, after `since`, ended: or None if it hasn't yet."""
    last = max([t for t in activity if t <= since], default=since - quiet_s)
    for t in sorted(t for t in activity if t > since):
        if t - last >= quiet_s:
            break
        last = t
    end = max(since, last + quiet_s)
    return end if end <= now else None


def _window_close(window_end: Optional[str], since: float) -> Optional[float]:
    day = _day(window_end)
    if day is None:
        return None
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=local(since).tzinfo).timestamp()
