"""Delivery: a live route becomes a proactive turn of its recipient (stage S4).

Only with the switch on (`PROACTIVE_DELIVERY`). The pipeline approves a route
on the Board (SignalStore.approve_delivery): a `now` route at once, a `today`
route once its quiet stretch is known. A `next` route isn't delivered this way:
it will reach the recipient's next run (stage S4, part 3).

The schedule runner calls DueDeliveries on every tick (`add_due_work`), and a
`now` approval wakes it, so that turn runs at once rather than at the next 30 s
tick. Each call:

  1. fails the deliveries left running for STUCK_S, and those due more than
     EXPIRE_S ago: a stuck run may have posted already, and a post that late
     would be stale. For posts to you, at most once beats at least once;
  2. skips a delivery whose recipient this host doesn't hold right now, as
     run_schedule would. It stays approved: it runs if the agent comes back
     here, and expires otherwise;
  3. claims each due delivery with one conditional UPDATE, so of two ticks
     only one runs it;
  4. returns a DeliverySchedule per claim. The runner runs it through
     run_schedule: the same ownership check and limiter as a schedule.

The turn is the recipient's own, in its home channel, with source "proactive":
  - one-shot and stateless, at thinking level THINKING. Its prompt
    (DeliveryPrompt) says what a teammate learned and what the recipient could
    offer, and never names the teammate;
  - its post is like a scheduled one: in the channel, in the channel inbox
    with its speech version, and in the session that answers your reply
    (a "proactive" source is on the scheduled surface: ExecutionContext.get_surface);
  - a reply of <silent/>, or nothing, posts nothing and records nothing;
  - the taps mark it hops = 1, so the Governor absorbs any signal it starts:
    a delivery never starts another.

The delivery is then finished: posted, silent or failed.
"""
import json
import re
import time
from typing import Any, Dict, List, Optional, Set

from core.channel.discord.output import DiscordOutput
from core.channel.discord.ownership import PROD, Ownership
from core.config.settings import Config
from core.prompts.proactive import DeliveryPrompt
from core.proactive.board import on_board
from core.proactive.switchboard import local
from core.proactive.taps import PROACTIVE_SOURCE
from core.scheduler.spec import PromptSchedule, ScheduleContext, SessionPolicy, WorkDecision
from core.storage.signals import FAILED, POSTED, SILENT, SignalStore

# A delivery still running after this long is failed, and never run again.
STUCK_S = 10 * 60
# An approved delivery due longer ago than this is failed unrun: never posted late.
EXPIRE_S = 3 * 3600
# Posts are short, and G4 wants post p95 <= 60 s: raise it if their quality suffers.
THINKING = "low"
# How a voice briefing names a proactive post: "Ramsay has a suggestion".
LABEL = "a suggestion"

# A reply that posts nothing: <silent/>, however it's spaced or cased.
_SILENT = re.compile(r"<\s*silent\s*/?\s*>", re.IGNORECASE)


def is_silent(reply: Any) -> bool:
    """Whether a proactive turn's reply posts nothing: blank, or <silent/> anywhere in it.
    `reply` is the reply's text or its AgentResponse, whose poll counts as something to post."""
    text = reply if isinstance(reply, str) else (getattr(reply, "text", None) or "")
    if _SILENT.search(text):
        return True
    poll = None if isinstance(reply, str) else getattr(reply, "poll_data", None)
    return not text.strip() and not (poll or {}).get("options")


def window_text(start: Optional[str], end: Optional[str]) -> str:
    """A route's window as the prompt says it: "2026-10-24 to 2026-10-27", one day, or one end."""
    if start and end:
        return start if start == end else f"{start} to {end}"
    if start:
        return f"from {start}"
    return f"until {end}" if end else ""


def render_prompt(delivery: Dict[str, Any], now: float) -> str:
    """The proactive turn's prompt for one due delivery (SignalStore.due_deliveries).

    It never names the source agent or its channel, and leaves the payload's
    `tool` out: that's how the fact was learned, which the recipient never says.
    """
    payload = {k: v for k, v in (delivery.get("payload") or {}).items() if k != "tool"}
    return DeliveryPrompt(
        now=f"{local(now):%A %Y-%m-%d %H:%M}",
        gist=delivery.get("gist") or "",
        evidence=delivery.get("evidence") or "",
        details=json.dumps(payload, ensure_ascii=False, default=str) if payload else "",
        until=delivery.get("until") or "",
        window=window_text(delivery.get("window_start"), delivery.get("window_end")),
        offer=delivery.get("offer") or "",
        note=delivery.get("note") or "",
    ).render()


def held_here(agent_id: str, ownership: Any) -> bool:
    """Whether this host runs `agent_id`'s turns right now, by run_schedule's rule:
    before the control thread is read, prod does and a dev host doesn't."""
    if not ownership.is_ready():
        return ownership.role == PROD
    return ownership.is_mine(agent_id)


class DeliveryOutput:
    """The recipient's channel as a proactive turn's output (core/runtime/foundation/output.py).

    A DiscordOutput that posts nothing for a silent reply. `outcome` says what
    reached the channel (posted or silent), so the delivery is finished right.
    """

    def __init__(self, channel: Any):
        self.channel = channel
        self.discord = DiscordOutput(channel)
        self.outcome: Optional[str] = None

    async def deliver(self, ctx: Any, response: Any) -> None:
        if is_silent(response):
            self.outcome = SILENT
            return
        await self.discord.deliver(ctx, response)
        self.outcome = POSTED

    async def notify(self, text: str) -> None:
        await self.discord.notify(text)

    def to(self, channel: str) -> Optional[DiscordOutput]:
        return self.discord.to(channel)


class DeliverySchedule(PromptSchedule):
    """One due delivery as a one-shot schedule: the recipient's turn in its home channel.

    It never goes through the registry: it has no cron, and is made from a
    claimed row (DueDeliveries). Its schedule id is `proactive:<agent>`, so the
    schedule state keeps one row per agent, not one per delivery. Its priority
    is the default, so its post enters the channel inbox with a speech version.
    """

    source = PROACTIVE_SOURCE

    def __init__(self, delivery: Dict[str, Any], *, store: Optional[SignalStore] = None,
                 now: Optional[float] = None):
        self.delivery = delivery
        self.route_id = delivery["route_id"]
        self.store = store or SignalStore()
        self.output: Optional[DeliveryOutput] = None
        super().__init__(
            prompt=render_prompt(delivery, time.time() if now is None else now),
            session_policy=SessionPolicy.STATELESS,
            thinking_level=THINKING,
            schedule_id=f"proactive:{delivery['to_agent']}",
            agent_id=delivery["to_agent"],
            cron="",
            channel=delivery.get("channel") or None,
            label=LABEL,
        )

    def has_work(self, ctx: ScheduleContext) -> WorkDecision:
        return WorkDecision(True, f"route #{self.route_id} is due")

    def describe(self) -> str:
        return f"{self.agent_id} [proactive] route #{self.route_id} -> #{self.channel}"

    def is_silent(self, reply: Any) -> bool:
        return is_silent(reply)

    def output_for(self, channel: Any) -> DeliveryOutput:
        self.output = DeliveryOutput(channel)
        return self.output

    def on_success(self, ctx: ScheduleContext) -> None:
        """Finishes the delivery as posted or silent, by what reached the channel."""
        outcome = self.output.outcome if self.output is not None else None
        if outcome is None:
            self._finish(FAILED, f"nothing reached #{self.channel}")
        else:
            self._finish(outcome)

    def on_failure(self, ctx: ScheduleContext) -> None:
        """Finishes the delivery as failed. It isn't run again."""
        self._finish(FAILED, "the turn failed")

    def _finish(self, status: str, error: str = "") -> None:
        self.store.finish_delivery(self.route_id, status, error=error)
        print(f"[proactive] Delivery of route #{self.route_id} to {self.agent_id}: {status}"
              f"{f' ({error})' if error else ''}")


class DueDeliveries:
    """The schedule runner's due work (ScheduleRunner.add_due_work): the deliveries due
    now, each claimed once, as DeliverySchedules (module docstring). `ownership` says
    which agents this host holds; Ownership() when not given."""

    def __init__(self, store: Optional[SignalStore] = None, ownership: Any = None):
        self.store = store or SignalStore()
        self.ownership = ownership
        self._waiting: Set[int] = set()  # routes already logged as waiting for their agent

    async def __call__(self, now: float) -> List[DeliverySchedule]:
        # Read on every call: turning the switch off stops deliveries at the next tick.
        if not Config().proactive_delivery:
            return []
        for route_id in await on_board(self.store.fail_stuck, now, STUCK_S):
            print(f"[proactive] The delivery of route #{route_id} was still running after "
                  f"{STUCK_S // 60} min: failed, not run again.")
        expired = await on_board(self.store.expire_deliveries, now, EXPIRE_S)
        if expired:
            print(f"[proactive] {expired} approved deliver{'y' if expired == 1 else 'ies'} due over "
                  f"{EXPIRE_S // 3600} h ago: failed, not posted late.")
        ownership = self.ownership or Ownership()
        specs = []
        for delivery in await on_board(self.store.due_deliveries, now):
            route_id = delivery["route_id"]
            if not held_here(delivery["to_agent"], ownership):
                # Left approved: it runs if the agent comes back here, and expires otherwise.
                if route_id not in self._waiting:
                    self._waiting.add(route_id)
                    print(f"[proactive] The delivery of route #{route_id} waits: this host doesn't hold "
                          f"{delivery['to_agent']} right now.")
                continue
            if not await on_board(self.store.claim_delivery, route_id, now):
                continue  # another tick took it
            try:
                specs.append(DeliverySchedule(delivery, store=self.store, now=now))
            except Exception as e:
                # Claimed but unrunnable: finished at once, rather than failed as stuck later.
                await on_board(self.store.finish_delivery, route_id, FAILED, error=f"{type(e).__name__}: {e}")
                print(f"[proactive] Could not make the turn for route #{route_id}: {type(e).__name__}: {e}")
        return specs
