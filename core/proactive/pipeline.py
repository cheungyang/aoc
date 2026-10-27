"""One Switchboard decision: from a session's unread turns to verdicts on the Board.

  1. Claim the turns. A turn is read by one decision only.
  2. Render the prompt: the teammates' cards, the tags, the Board, the exchange.
  3. Run the Switchboard (switchboard.run_model): the only model call.
  4. Parse its reply. For each signal, the Governor gives a verdict on each route.
  5. Record the decision, its signals and its routes, then tell team radio.

Everything that touches the Board runs on its worker thread (board.py). In
shadow mode, nothing reaches an agent: a `deliver` verdict only records when
it would have been delivered (`deliver_at`). `resolve` fills in those times
for `today` and `next` routes as they become known. It runs after each
decision and on the service's sweep.

Live (the switch, `PROACTIVE_DELIVERY`, on when the decision started), a
`deliver` verdict is also approved for delivery in the recipient's home
channel (delivery.py runs it): a `now` route at once, and the runner is woken
for it; a `today` route when `resolve` knows its time. A `now` approval also
puts the recipient's emoji on your message that started it, at once (`ack`):
the receipt gets 🧑‍🍳 seconds after it's saved, and Ramsay's post follows. A
`next` route isn't delivered as a proactive turn: it rides along on the
recipient's next scheduled run (delivery.SignalsForMe).
"""
import hashlib
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Tuple

from core.channel.discord.ownership import Ownership
from core.config.settings import Config
from core.loaders.agents_loader import AgentsLoader
from core.proactive.board import on_board
from core.proactive.cards import PeerCard, cards, offer_words
from core.proactive.delivery import held_here
from core.proactive.effects import TRIGGER
from core.proactive.governor import DELIVER, Governor, evidence_found, resolve_times
from core.proactive.switchboard import ModelRun, ProposedSignal, build_prompt, local, parse_reply, run_model
from core.proactive.taps import TAP_CHAT, TAP_EFFECT, TAP_SCHEDULED
from core.runtime.foundation.routing import home_channel
from core.storage.signals import SignalStore

# A decision's mode: SHADOW records when a route would be delivered; LIVE
# delivers it (stage S4). The switch, `PROACTIVE_DELIVERY`, picks one.
SHADOW = "shadow"
LIVE = "live"
# How far back the Switchboard sees the Board, so it doesn't route a fact twice.
BOARD_S = 72 * 3600
# Payload fields that identify a fact across wordings (a receipt, a booking, a trip).
STABLE_FIELDS = ("vendor", "date", "title", "start", "where", "from", "until", "concept")
_TAP_ORDER = (TAP_EFFECT, TAP_SCHEDULED, TAP_CHAT)


class Radio(Protocol):
    """Team radio (radio.py): told about each decision once it is recorded."""

    async def decided(self, decision: Dict[str, Any]) -> None: ...


class Ack(Protocol):
    """The `now` reaction (core/channel/discord/ack.py): puts `emoji` on `message`, as the
    surface names it, as `agent_id`'s own bot where it can. True if it did. Never raises."""

    async def react(self, message: str, emoji: str, agent_id: str) -> bool: ...


@dataclass(frozen=True)
class Approval:
    """A live `now` route approved for delivery, and what its reaction needs."""
    route_id: int
    to_agent: str
    emoji: str               # the recipient's card emoji
    message: Optional[str]   # your message that started the turn: "<channel id>/<message id>", or None


def dedupe_key(signal: ProposedSignal) -> str:
    """What makes two signals the same fact: the payload's identifying fields, or
    else the gist's words, regardless of order."""
    stable = {k: str(signal.payload[k]).lower() for k in STABLE_FIELDS if signal.payload.get(k)}
    basis = json.dumps(stable, sort_keys=True) if stable else " ".join(sorted(offer_words(signal.gist)))
    return f"{signal.kind}:{hashlib.sha1(basis.encode('utf-8')).hexdigest()[:12]}"


def payload_from(turns: List[Dict[str, Any]], signal: ProposedSignal) -> Dict[str, Any]:
    """An artifact's payload when the Switchboard gave none: the arguments of the
    turn's one trigger effect, written by code (the receipt as it was saved)."""
    if signal.payload or signal.kind != "artifact":
        return signal.payload
    triggers = [e for t in turns for e in (t.get("effects") or []) if e.get("role") == TRIGGER]
    if len(triggers) != 1:
        return {}
    return {"tool": triggers[0].get("tool"), **(triggers[0].get("args") or {})}


def source_message(turns: List[Dict[str, Any]], agent_id: str) -> Optional[str]:
    """The message of yours that started `agent_id`'s latest turn among `turns` that has
    one (the taps keep it in the turn's context), or None."""
    for turn in sorted(turns, key=lambda t: (t.get("at") or 0, t.get("id") or 0), reverse=True):
        message = (turn.get("context") or {}).get("message") if turn.get("agent_id") == agent_id else None
        if message:
            return message
    return None


def home_of(agent_id: str) -> Optional[str]:
    """Where a delivery to `agent_id` runs and posts: its home channel (routing.home_channel)."""
    return home_channel(agent_id, AgentsLoader())


class Pipeline:
    """The service's `decide`: one decision per call (module docstring).

    `home` finds a recipient's home channel. `wake`, the schedule runner's, is
    called after a live decision approved a `now` delivery, and `ack` reacts on
    your message for it, if this host holds the recipient (`ownership`, or
    Ownership() when not given).
    """

    def __init__(self, store: Optional[SignalStore] = None, *, mode: Optional[str] = None,
                 run: Callable[[str], Awaitable[ModelRun]] = run_model,
                 team: Callable[[], Dict[str, PeerCard]] = cards, radio: Optional[Radio] = None,
                 owns: Optional[Callable[[str], bool]] = None, rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.time, tags: Optional[str] = None,
                 home: Callable[[str], Optional[str]] = home_of, wake: Optional[Callable[[], None]] = None,
                 ack: Optional[Ack] = None, ownership: Any = None):
        self.store = store or SignalStore()
        self.mode = mode
        self.run = run
        self.team = team
        self.radio = radio
        self.owns = owns
        self.rng = rng
        self.clock = clock
        self.tags = tags
        self.home = home
        self.wake = wake
        self.ack = ack
        self.ownership = ownership

    def current_mode(self) -> str:
        """LIVE when the switch (`PROACTIVE_DELIVERY`) is on, else SHADOW; or the mode given."""
        return self.mode or (LIVE if Config().proactive_delivery else SHADOW)

    async def __call__(self, session_id: str, turns: List[Dict[str, Any]]) -> Optional[int]:
        """Decides on `turns`, one session's unread ledger rows; returns the decision's id.

        Once the turns are claimed, the decision is finished whatever happens: a
        failure (reading the team or the Board, the prompt, the reply, the
        Governor) becomes its error, and what the run cost is kept. Its turns stay
        read, so a failing step can't loop; `!signals` shows the decision as failed.
        """
        now = self.clock()
        mode = self.current_mode()  # read once: the decision's record and its approvals agree
        tap = min((t.get("tap") or TAP_CHAT for t in turns),
                  key=lambda name: _TAP_ORDER.index(name) if name in _TAP_ORDER else len(_TAP_ORDER))
        decision_id = await on_board(self.store.start_decision, tap=tap, session_id=session_id,
                                     first_turn_at=min(t["at"] for t in turns), mode=mode, at=now)
        claimed = set(await on_board(self.store.claim_turns, [t["id"] for t in turns], decision_id))
        turns = [t for t in turns if t["id"] in claimed]
        if not turns:
            await on_board(self.store.finish_decision, decision_id, errors="another decision read these turns")
            return decision_id

        run: Optional[ModelRun] = None
        prompt = ""
        signals: List[ProposedSignal] = []
        problems: List[str] = []
        approvals: List[Approval] = []
        try:
            team = self.team()
            board = await on_board(self.store.active_signals, since=now - BOARD_S, today=f"{local(now):%Y-%m-%d}")
            prompt, exchange = build_prompt(turns, team, board, now, tags=self.tags)
            run = await self.run(prompt)
            signals, problems = ([], [run.error]) if run.error else parse_reply(run.reply)
            approvals = await on_board(self._record, decision_id, turns, signals, exchange, team, now, mode)
        except Exception as e:
            problems.append(f"{type(e).__name__}: {e}")
        if approvals:
            self._wake()
            await self._acknowledge(approvals)
        cost = {} if run is None else {"took_ms": run.took_ms, "tokens_in": run.tokens_in,
                                       "tokens_out": run.tokens_out, "reply": run.reply}
        await on_board(self.store.finish_decision, decision_id, prompt=prompt, errors="; ".join(problems), **cost)
        print(f"[proactive] Decision #{decision_id} ({tap}, {len(turns)} turn(s)): {len(signals)} signal(s)"
              f"{'; ' + '; '.join(problems) if problems else ''}")
        if self.radio is not None:
            try:
                await self.radio.decided(await on_board(self.store.decision, decision_id))
            except Exception as e:
                print(f"[proactive] Team radio missed decision #{decision_id}: {type(e).__name__}: {e}")
        await self.resolve()
        return decision_id

    def _record(self, decision_id: int, turns: List[Dict[str, Any]], signals: List[ProposedSignal],
                exchange: str, team: Dict[str, PeerCard], now: float, mode: str) -> List[Approval]:
        """The signals and the Governor's verdict on each route, on the Board (worker thread).
        Live, a `now` delivery is approved at once; returns those approvals, each with its
        recipient's emoji and your message that started the source agent's latest turn, for
        `__call__` to wake the runner and react on the loop."""
        governor = Governor(self.store, team, owns=self.owns, rng=self.rng, clock=self.clock)
        source = turns[0]
        hops = max(int(t.get("hops") or 0) for t in turns)
        message = source_message(turns, source["agent_id"])
        approvals: List[Approval] = []
        for signal in signals:
            problem = governor.signal_problem(signal, exchange)
            key = dedupe_key(signal)
            signal_id = self.store.add_signal({
                "decision_id": decision_id, "at": now, "kind": signal.kind, "gist": signal.gist,
                "evidence": signal.evidence, "evidence_found": int(evidence_found(signal.evidence, exchange)),
                "tags": ",".join(signal.tags), "until": signal.until, "sensitivity": signal.sensitivity,
                "payload": payload_from(turns, signal), "source_agent": source["agent_id"],
                "source_channel": source.get("channel") or "", "hops": hops, "dedupe_key": key,
            })
            for route in signal.routes:
                verdict = governor.judge(signal, route, source_agent=source["agent_id"], hops=hops,
                                         dedupe_key=key, signal_problem=problem)
                route_id = self.store.add_route({
                    "signal_id": signal_id, "decision_id": decision_id, "to_agent": route.to,
                    "offer": verdict.offer or route.offer, "asked": route.urgency or "today",
                    "urgency": verdict.urgency, "window_start": route.window_start, "window_end": route.window_end,
                    "note": route.note, "verdict": verdict.verdict, "reason": verdict.reason,
                    "kind_key": verdict.kind_key, "sampled": verdict.sampled, "deliver_at": verdict.deliver_at,
                })
                if mode == LIVE and verdict.verdict == DELIVER and verdict.urgency == "now":
                    if self._approve(route_id, route.to, verdict.deliver_at, now):
                        card = team.get(route.to)
                        approvals.append(Approval(route_id, route.to, card.emoji if card else "", message))
        return approvals

    def _approve(self, route_id: int, to_agent: str, deliver_at: float, now: float) -> bool:
        """Approves a route for a proactive turn in its recipient's home channel (worker thread).
        A recipient with no home channel has nowhere to post, so nothing is approved."""
        channel = self.home(to_agent)
        if not channel:
            print(f"[proactive] Route #{route_id} isn't delivered: {to_agent} has no home channel.")
            return False
        return self.store.approve_delivery(route_id, to_agent=to_agent, channel=channel, deliver_at=deliver_at,
                                           at=now)

    def _wake(self) -> None:
        """Wakes the schedule runner, so a `now` delivery runs at once, not at the next tick."""
        if self.wake is None:
            return
        try:
            self.wake()
        except Exception as e:
            print(f"[proactive] Could not wake the schedule runner: {type(e).__name__}: {e}")

    async def _acknowledge(self, approvals: List[Approval]) -> None:
        """The `now` reaction (proposal D17): each approval's recipient emoji on your message
        that started it, and when, on the Board (`acked_at`, G4's acknowledgment time).

        Only where this host holds the recipient (delivery.held_here, the delivery's own
        rule): elsewhere its post may never come. One reaction per message and emoji. It
        stays even if the turn then stays silent: the recipient did take note. A reaction
        that fails is logged, and the decision goes on.
        """
        if self.ack is None:
            return
        ownership = self.ownership or Ownership()
        reacted: Dict[Tuple[str, str], bool] = {}
        for approval in approvals:
            key = (approval.message, approval.emoji)
            try:
                if not approval.message or not approval.emoji or not held_here(approval.to_agent, ownership):
                    continue
                if key not in reacted:
                    reacted[key] = bool(await self.ack.react(approval.message, approval.emoji, approval.to_agent))
                if reacted[key]:
                    await on_board(self.store.ack_delivery, approval.route_id, self.clock())
            except Exception as e:
                print(f"[proactive] No reaction for route #{approval.route_id}: {type(e).__name__}: {e}")

    async def resolve(self) -> None:
        """Fills in the would-deliver times the Board has learned (governor.resolve_times), and
        approves the live `today` routes among them for delivery."""
        try:
            await on_board(self._resolve, self.clock())
        except Exception as e:
            print(f"[proactive] Could not resolve delivery times: {type(e).__name__}: {e}")

    def _resolve(self, now: float) -> None:
        """resolve_times, then an approval for each `today` route it timed whose decision is live
        (worker thread). A `next` route isn't delivered as a proactive turn (module docstring)."""
        for route_id in resolve_times(self.store, now):
            route = self.store.route(route_id)
            if route and route.get("mode") == LIVE and route["urgency"] == "today":
                self._approve(route_id, route["to_agent"], route["deliver_at"], now)
