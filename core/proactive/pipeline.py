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
"""
import hashlib
import json
import random
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol

from core.config.settings import Config
from core.proactive.board import on_board
from core.proactive.cards import PeerCard, cards, offer_words
from core.proactive.effects import TRIGGER
from core.proactive.governor import Governor, evidence_found, resolve_times
from core.proactive.switchboard import ModelRun, ProposedSignal, build_prompt, local, parse_reply, run_model
from core.proactive.taps import TAP_CHAT, TAP_EFFECT, TAP_SCHEDULED
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


class Pipeline:
    """The service's `decide`: one decision per call (module docstring)."""

    def __init__(self, store: Optional[SignalStore] = None, *, mode: Optional[str] = None,
                 run: Callable[[str], Awaitable[ModelRun]] = run_model,
                 team: Callable[[], Dict[str, PeerCard]] = cards, radio: Optional[Radio] = None,
                 owns: Optional[Callable[[str], bool]] = None, rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.time, tags: Optional[str] = None):
        self.store = store or SignalStore()
        self.mode = mode
        self.run = run
        self.team = team
        self.radio = radio
        self.owns = owns
        self.rng = rng
        self.clock = clock
        self.tags = tags

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
        tap = min((t.get("tap") or TAP_CHAT for t in turns),
                  key=lambda name: _TAP_ORDER.index(name) if name in _TAP_ORDER else len(_TAP_ORDER))
        decision_id = await on_board(self.store.start_decision, tap=tap, session_id=session_id,
                                     first_turn_at=min(t["at"] for t in turns), mode=self.current_mode(), at=now)
        claimed = set(await on_board(self.store.claim_turns, [t["id"] for t in turns], decision_id))
        turns = [t for t in turns if t["id"] in claimed]
        if not turns:
            await on_board(self.store.finish_decision, decision_id, errors="another decision read these turns")
            return decision_id

        run: Optional[ModelRun] = None
        prompt = ""
        signals: List[ProposedSignal] = []
        problems: List[str] = []
        try:
            team = self.team()
            board = await on_board(self.store.active_signals, since=now - BOARD_S, today=f"{local(now):%Y-%m-%d}")
            prompt, exchange = build_prompt(turns, team, board, now, tags=self.tags)
            run = await self.run(prompt)
            signals, problems = ([], [run.error]) if run.error else parse_reply(run.reply)
            await on_board(self._record, decision_id, turns, signals, exchange, team, now)
        except Exception as e:
            problems.append(f"{type(e).__name__}: {e}")
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
                exchange: str, team: Dict[str, PeerCard], now: float) -> None:
        """The signals and the Governor's verdict on each route, on the Board (worker thread)."""
        governor = Governor(self.store, team, owns=self.owns, rng=self.rng, clock=self.clock)
        source = turns[0]
        hops = max(int(t.get("hops") or 0) for t in turns)
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
                self.store.add_route({
                    "signal_id": signal_id, "decision_id": decision_id, "to_agent": route.to,
                    "offer": verdict.offer or route.offer, "asked": route.urgency or "today",
                    "urgency": verdict.urgency, "window_start": route.window_start, "window_end": route.window_end,
                    "note": route.note, "verdict": verdict.verdict, "reason": verdict.reason,
                    "kind_key": verdict.kind_key, "sampled": verdict.sampled, "deliver_at": verdict.deliver_at,
                })

    async def resolve(self) -> None:
        """Fills in the would-deliver times the Board has learned (governor.resolve_times)."""
        try:
            await on_board(resolve_times, self.store, self.clock())
        except Exception as e:
            print(f"[proactive] Could not resolve delivery times: {type(e).__name__}: {e}")
