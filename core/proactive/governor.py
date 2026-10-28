"""The Governor: what each of the Switchboard's routes may become.

The Switchboard proposes and code disposes. Every rule here is code, and no
number comes from a model. Each route gets one verdict:

  deliver   a proactive turn for the recipient at `deliver_at`. Live, that is
            a post in the recipient's own channel; in shadow it is only the
            time it would have been.
  absorb    no post: the fact is only good to know (Live Context)
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
  no schedule   `next` for a teammate with no scheduled run (its card's
                `scheduled`) becomes `today`: there's no run for it to ride along on
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
    conversation with it. Live, it's delivered only on a scheduled run
    (delivery.SignalsForMe), hence the no-schedule rule.
`today` and `next` times are only known later; `resolve_times` fills them in
as the Board sees them happen.

A follow-up close the Switchboard proposes (switchboard.ProposedClose) is
code's call too (`close_problem`): the follow-up must be open now, and its
evidence at least MIN_CLOSE_WORDS words (a lone "done" could be about
anything), found word for word, as a signal's is, in what you said in the
exchange (`your_words`). An agent's reply doesn't count, nor what one agent
asked another (agent_call, the Concierge's too), nor the prompt a scheduled
run, a follow-up's check or a proactive turn starts from. Live, the pipeline
then closes it; in shadow, it records that it would.

Dormant levers: designed, but not built. Each one is switched on when its
condition holds:

  quiet_hours         hold today/next deliveries overnight. When: the "would
                      deliver" times in shadow include ones you don't like, or a
                      night post annoys you.
  delivery_window     deliver `today` only within set hours, and by a set time at
                      the latest. When: `today` items land late or at odd hours.
  per_signal_cap      at most 2 posts from one signal. When: one signal fans out
                      to 3 or more posts.
  per_agent_daily_cap at most N posts per agent per day. When: an agent posts
                      more than 3 a day for a week.
  global_daily_cap    at most N posts a day. When: more than 10 posts a day in
                      total.
  long_shot_cap       at most 1 low-acceptance nudge a day. When: long shots are
                      more than 20% of posts, or you decline many of them.
  open_follow_ups_cap at most N open follow-ups per agent. When: one agent keeps
                      more than 5 open, or checks cost more than expected.
"""
import random
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Collection, Dict, List, Mapping, Optional, Sequence

from core.channel.discord.ownership import Ownership
from core.proactive.cards import PeerCard
from core.proactive.switchboard import ProposedClose, ProposedRoute, ProposedSignal, local
from core.proactive.taps import TAP_SCHEDULED, asked_by_agent
from core.storage.signals import SENSITIVE_TAGS, SignalStore

DELIVER, ABSORB, DROP = "deliver", "absorb", "drop"

# SENSITIVE_TAGS (health, finance) reach only the teammates that read them. The
# Board defines them: Live Context follows the same rule.
DEDUPE_S = 72 * 3600
ACTIVE_S = 10 * 60
QUIET_STRETCH_S = 30 * 60
NOW_WINDOW_S = 2 * 3600
HORIZON_DAYS = 7
# The acceptance draw a nudge needs to be worth a post rather than absorbed,
# and the prior every kind starts from: a kind with no history is posted about
# 9 times in 10 (calibrated from shadow data).
WORTH_POSTING = 0.3
PRIOR_ENGAGED, PRIOR_DECLINED = 2, 1
# Kinds that may be delivered `now`: something was made, or the user asked.
NOW_KINDS = frozenset({"artifact", "request"})
# Why a proposed follow-up close doesn't stand (close_problem).
NOT_OPEN = "not open"
TOO_SHORT = "too short to be sure"
NOT_YOUR_WORDS = "evidence not found in your own words"
# The fewest words a close's evidence needs: a lone "done" or "stop" could be about anything.
MIN_CLOSE_WORDS = 2
# A ledger turn's surface when nobody asked for it: a scheduled run, a follow-up's check,
# a proactive turn (ExecutionContext.get_surface).
SCHEDULED_SURFACE = "scheduled"

_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                         "\u2014": "-", "\u2026": "..."})
_EDGES = " \"'.,;:!?-"
# A word, for MIN_CLOSE_WORDS: a run without spaces, or one Chinese character, since Chinese is
# written without spaces (the privacy guard, core/git/privacy.py, counts them the same way).
# Only a run with a letter or a digit in it counts (word_count).
_CJK_CHARS = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_WORD = re.compile(rf"[{_CJK_CHARS}]|[^\s{_CJK_CHARS}]+")


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


def word_count(text: str) -> int:
    """How many words `text` has (_WORD): runs between spaces with a letter or a digit in
    them, and each Chinese character. "done!" is one, "stop ..." one, "I'm done" two."""
    return sum(1 for word in _WORD.findall(text or "") if any(ch.isalnum() for ch in word))


def your_words(turns: Sequence[Mapping[str, Any]]) -> str:
    """What you said in `turns` (ledger rows), one turn per line: the `user_text` of the turns
    you started (`caller` main, as the taps record a message routed from a channel). A turn
    another agent asked for starts from that agent's words, even when it's the Concierge,
    whose calls say main too: the taps mark those (taps.asked_by_agent). A scheduled run, a
    follow-up's check and a proactive turn (the scheduled tap or surface, hops 1) start from
    a prompt. None of those count."""
    return "\n".join(turn.get("user_text") or "" for turn in turns
                     if turn.get("caller") == "main" and not asked_by_agent(turn)
                     and turn.get("tap") != TAP_SCHEDULED and turn.get("surface") != SCHEDULED_SURFACE
                     and not int(turn.get("hops") or 0))


def close_problem(close: ProposedClose, open_ids: Collection[int], user_words: str) -> str:
    """Why a proposed close doesn't stand, or "" when it does: the follow-up must be open now
    (`open_ids`), and its evidence at least MIN_CLOSE_WORDS words, found word for word in what
    you said (`user_words`, from your_words). Words found only in an agent's reply don't count."""
    if close.follow_up_id not in open_ids:
        return NOT_OPEN
    if word_count(close.evidence) < MIN_CLOSE_WORDS:
        return TOO_SHORT
    if not evidence_found(close.evidence, user_words):
        return NOT_YOUR_WORDS
    return ""


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
        if urgency == "next" and not card.scheduled:
            urgency, reason = "today", f"{card.name} has no scheduled run to wait for"
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
