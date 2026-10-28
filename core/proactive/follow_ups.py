"""Follow-up checks: each agent's open loops, checked on the schedule runner.

Only with the switch on (`PROACTIVE_DELIVERY`). A follow-up (core/storage/follow_ups.py,
opened with tools/follow_up.py) is due when its `next_check_at` comes. The
schedule runner calls DueFollowUps on every tick (`add_due_work`), as it does
DueDeliveries. Each call:

  1. frees the claims older than CLAIM_S: a check that never finished (a
     restart, or another host took the agent) is due again;
  2. reads the due follow-ups, by your wall clock's date: open, unclaimed, and
     not past their `until`;
  3. skips one whose agent this host doesn't hold right now, as run_schedule
     would (delivery.held_here). It stays due, and the wait is logged once;
  4. claims each with one conditional UPDATE, so of two ticks only one runs it,
     and at most MAX_PER_TICK a tick: the rest wait for the next tick;
  5. returns a FollowUpSchedule per claim. The runner runs it through
     run_schedule: the same ownership check and limiter as a schedule.

The check is the agent's own turn, in its home channel, with source "followup":
  - when the check is one of the agent's scans, the scan runs first, off the
    loop, like a schedule's precondition. Its schedule id is `followup:<id>`,
    so its `last_success_at` is this follow-up's own last good check. Nothing
    found: no turn. What it couldn't read is posted in the agent's channel,
    once per issue a day. What it found is fingerprinted (`fingerprint`), and
    the same findings as last time are nothing new: no turn either. Only new
    findings start the turn, and ride along in it;
  - a model check is a short turn at each due check;
  - the turn (FollowUpPrompt) knows the goal, how long it has watched and what
    the agent last told you. It posts one short message, or replies <silent/>
    and posts nothing. When the goal is met it closes the follow-up itself: the
    tool closes on this surface, but won't open or extend one;
  - the last check, when no other would come before `until` ends
    (`is_last_check`), always runs its turn: a scan's with nothing new too,
    with what it sees now, or with what it couldn't read instead of the
    notice. That turn never replies <silent/>: whatever it found, it ends with
    one short question, whether to keep watching and until when. You answer
    in the conversation, where the agent can extend it (tools/follow_up.py),
    or with `!followups extend`. An extension posts no radio line;
  - its post is like a scheduled one: in the channel, in the channel inbox with
    its speech version, and in the session that answers your reply;
  - the taps read it like a scheduled run, hops = 0, so what it finds can still
    reach teammates. It's never briefed with the team's `next` routes: the
    runner briefs only source "scheduled".

Then the store counts the check (`finish_check`): a post is FOUND, a silent
reply or no work EMPTY, a scan that couldn't read PROBLEM, and a failed turn, or
one whose post reached no channel, FAILED. It sets the next check from that.
The last check always posts, so there a post is FOUND only when a scan found
something new; otherwise it's EMPTY, or PROBLEM for a scan that couldn't read,
and what it posted isn't kept as the last report: a model check's last post may
be only the offer.

FollowUpSweep is one of the proactive service's sweeps, every minute, with the
switch on or off:
  1. it ends the follow-ups whose `until` has passed: ending isn't a check;
  2. it puts each one opened and each one closed on team radio, once:

       📻 🔁 ✈️ Miles opened follow-up #12 · <goal> · every 2d until 2026-12-15 · check: award_watch
       📻 🔁 ✈️ Miles' follow-up #12 closed · <goal> · <why> · 6 checks, 1 find

     An event older than ANNOUNCE_S is marked without a line, so a host that
     was down doesn't flood the thread. Each line gets one attempt, posted here
     or not, as TeamRadio.decided does, and takes no reactions.
"""
import hashlib
import time
from typing import Any, Callable, Dict, List, Optional, Set

from core.channel.discord.ownership import Ownership
from core.config.settings import Config
from core.loaders.agents_loader import AgentsLoader
from core.prompts.proactive import FollowUpPrompt
from core.proactive.board import on_board
from core.proactive.cards import PeerCard, cards
from core.proactive.delivery import DeliveryOutput, held_here, is_silent
from core.proactive.radio import RadioChannel
from core.proactive.switchboard import local
from core.proactive.taps import FOLLOWUP_SOURCE, clip
from core.runtime.foundation.routing import home_channel
from core.scheduler.preconditions import build_precondition
from core.scheduler.spec import Findings, Problems, PromptSchedule, ScheduleContext, SessionPolicy, WorkDecision
from core.storage.follow_ups import Check, FollowUp, FollowUpStore, Result, Status, cadence_text, end_of
from core.storage.signals import POSTED, SILENT

# A check still claimed after this long never finished: its claim is freed, and it's due again.
CLAIM_S = 3600
# Checks claimed per tick, the longest due first. A burst waits for the next tick, 30 s on.
MAX_PER_TICK = 5
# How a voice briefing names a follow-up's post: "Miles has a follow-up".
LABEL = "a follow-up"
# How much of a post the next check sees, as what the agent last told you.
REPORT_CHARS = 600
# Opened and closed lines older than this are marked, never posted.
ANNOUNCE_S = 86400


def fingerprint(decision: WorkDecision) -> str:
    """What a scan found, as a hash: its findings' text and issues, or its reason when it
    has no findings. The same findings twice are nothing new."""
    detail = decision.detail
    if isinstance(detail, Findings):
        basis = "\n".join([detail.text.strip(), *detail.issues])
    else:
        basis = decision.reason or ""
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def checks_text(checks: int) -> str:
    """How many checks a follow-up has had, as the prompt says it."""
    if not checks:
        return "you haven't checked it since"
    return "you've checked it once since" if checks == 1 else f"you've checked it {checks} times since"


def is_last_check(follow_up: FollowUp, now: float) -> bool:
    """Whether a check at `now` is its follow-up's last: no other would come before `until`
    ends. That's when `every` from now reaches the end, whatever the interval now: as the
    end nears, `finish_check` puts the interval back to `every`, so the next check comes
    `every` from now whenever that's before the end. Counting a stretched interval would
    call a late check the last too soon, and the real last one would offer again."""
    return now + follow_up.every_s >= end_of(follow_up.until)


def check_line(check: Check, decision: Optional[WorkDecision], new: bool = True) -> str:
    """What the turn is to look at: a model check's `what`, or what a scan check found. On
    the last check a scan's turn runs with nothing new too (`new` False): it says so, and
    what the scan couldn't read, if that's why."""
    if check.is_model:
        return f"Check now: {check.what}"
    detail = decision.detail if decision is not None else None
    reason = decision.reason if decision is not None else ""
    if isinstance(detail, Problems):
        return f"Your {check.type} check couldn't read what it needed: {'; '.join(detail.issues)}."
    if not new:
        if isinstance(detail, Findings):
            return "Your check found nothing new since the last one: what it sees now is below."
        return f"Your {check.type} check found nothing new{f': {reason}' if reason else '.'}"
    if isinstance(detail, Findings):
        return "Your check found something new: below."
    return f"Your {check.type} check found something new{f': {reason}' if reason else '.'}"


def render_prompt(follow_up: FollowUp, now: float, check: str, last: bool = False) -> str:
    """The check's prompt for one claimed follow-up (module docstring). `check` is its check_line,
    and `last` whether it's the last check, which offers to keep watching."""
    found_at = follow_up.last_found_at
    return FollowUpPrompt(
        id=follow_up.id,
        now=f"{local(now):%A %Y-%m-%d %H:%M}",
        goal=follow_up.goal,
        close_when=follow_up.close_when,
        opened=f"{local(follow_up.opened_at):%A %Y-%m-%d}",
        until=follow_up.until,
        checks=checks_text(follow_up.checks),
        last_report=follow_up.last_report,
        reported=f"{local(found_at):%A %Y-%m-%d}" if found_at else "",
        check=check,
        last_check=last,
    ).render()


def radio_line(follow_up: FollowUp, team: Dict[str, PeerCard]) -> str:
    """A follow-up's team radio line: opened, or closed and why (module docstring). The opened
    line names the agent that opened it. The closed line only says whose it was: its why says
    who closed it (FollowUp.closed_why), and that may be you, your words, or `until`."""
    card = team.get(follow_up.agent_id)
    emoji, name = (card.emoji, card.name) if card else ("🤖", follow_up.agent_id)
    if follow_up.is_open:
        return (f"📻 🔁 {emoji} {name} opened follow-up #{follow_up.id} · {follow_up.goal} · "
                f"every {cadence_text(follow_up.every_s)} until {follow_up.until} · check: {follow_up.check.text()}")
    return (f"📻 🔁 {emoji} {possessive(name)} follow-up #{follow_up.id} closed · {follow_up.goal} · "
            f"{follow_up.closed_why(name)} · {follow_up.tally()}")


def possessive(name: str) -> str:
    """Miles' for Miles, Ramsay's for Ramsay."""
    return f"{name}'" if name.endswith("s") else f"{name}'s"


class FollowUpSchedule(PromptSchedule):
    """One follow-up's due check as a one-shot schedule: its agent's turn in its home channel,
    else in the channel the follow-up was opened in.

    It never goes through the registry: it has no cron, and is made from a
    claimed FollowUp (DueFollowUps). Its schedule id is `followup:<id>`, so the
    schedule state keeps one row per follow-up. Its priority is the default, so
    its post enters the channel inbox with a speech version, as a delivery's does.
    A scan check is built here, so a check that can't be built fails at once.
    """

    source = FOLLOWUP_SOURCE

    def __init__(self, follow_up: FollowUp, *, store: Optional[FollowUpStore] = None, loader: Any = None):
        self.follow_up = follow_up
        self.store = store or FollowUpStore()
        self.output: Optional[DeliveryOutput] = None
        self.found: Optional[str] = None  # a scan check's fingerprint of what it found (has_work)
        self.new = False                  # whether a scan check found something new (has_work)
        self.skipped = Result.EMPTY       # how a check with nothing new ends: EMPTY, or PROBLEM
        check = follow_up.check
        precondition = None
        if not check.is_model:
            precondition = build_precondition({"type": check.type, "args": dict(check.args)}, follow_up.agent_id)
        super().__init__(
            prompt="",  # made per run: build_prompt
            session_policy=SessionPolicy.STATELESS,
            thinking_level=None,
            schedule_id=f"followup:{follow_up.id}",
            agent_id=follow_up.agent_id,
            cron="",
            channel=home_channel(follow_up.agent_id, loader or AgentsLoader()) or follow_up.channel or None,
            precondition=precondition,
            label=LABEL,
            # How the runner names it when it posts what a scan couldn't read.
            entry={"id": f"follow-up #{follow_up.id} ({check.type})"},
        )

    @property
    def follow_up_id(self) -> int:
        return self.follow_up.id

    def has_work(self, ctx: ScheduleContext) -> WorkDecision:
        """Off the loop. A model check always runs; a scan check only on findings new since
        the last check, but on the last check whatever it found (module docstring)."""
        if self.precondition is None:
            return WorkDecision(True, f"follow-up #{self.follow_up_id} is due")
        decision = self._check_precondition(ctx)
        if decision:
            self.found = fingerprint(decision)
            self.new = self.found != self.follow_up.last_fingerprint
            if self.new:
                return decision
            decision = WorkDecision(False, "nothing new since the last check", detail=decision.detail)
        else:
            self.skipped = Result.PROBLEM if isinstance(decision.detail, Problems) else Result.EMPTY
        if is_last_check(self.follow_up, ctx.now):
            # The turn runs all the same, to offer to keep watching. It sees what the scan saw,
            # or what it couldn't read: the turn says so, and the runner posts no notice.
            return WorkDecision(True, decision.reason, detail=decision.detail)
        return decision

    def build_prompt(self, ctx: ScheduleContext, decision: Optional[WorkDecision] = None) -> str:
        """The FollowUpPrompt, and after it a scan check's findings block: new ones, or on the
        last check what the scan sees now."""
        last = is_last_check(self.follow_up, ctx.now)
        # Before the last check a scan's turn runs only on something new, as has_work decided.
        line = check_line(self.follow_up.check, decision, new=self.new or not last)
        prompt = render_prompt(self.follow_up, ctx.now, line, last=last)
        if decision is not None and isinstance(decision.detail, Findings):
            return f"{prompt}\n\n{decision.detail.as_block()}"
        return prompt

    def describe(self) -> str:
        return f"{self.agent_id} [followup] #{self.follow_up_id} -> #{self.channel}"

    def is_silent(self, reply: Any) -> bool:
        return is_silent(reply)

    def output_for(self, channel: Any) -> DeliveryOutput:
        self.output = DeliveryOutput(channel)
        return self.output

    def on_success(self, ctx: ScheduleContext) -> None:
        """A post is a find, kept as what the agent last told you. The last check always posts,
        to offer to keep watching, so there only a scan's new findings make its post a find;
        otherwise it counts as the check went (EMPTY, or PROBLEM), and isn't kept. A silent
        reply is an empty check, but keeps a scan's new fingerprint: the agent judged those
        findings not worth a post, so they don't come back as new."""
        outcome = self.output.outcome if self.output is not None else None
        if outcome == POSTED and (self.new or not is_last_check(self.follow_up, ctx.now)):
            self._finish(Result.FOUND, fingerprint=self.found, report=clip(self.output.text, REPORT_CHARS))
        elif outcome == POSTED:
            self._finish(self.skipped, "its last check, which always posts", fingerprint=self.found)
        elif outcome == SILENT:
            self._finish(Result.EMPTY, fingerprint=self.found)
        else:
            self._finish(Result.FAILED, f"nothing reached #{self.channel}")

    def on_skip(self, ctx: ScheduleContext, reason: str) -> None:
        self._finish(self.skipped, reason)

    def on_failure(self, ctx: ScheduleContext) -> None:
        self._finish(Result.FAILED, "the turn failed")

    def _finish(self, result: Result, why: str = "", *, fingerprint: Optional[str] = None,
                report: Optional[str] = None) -> None:
        self.store.finish_check(self.follow_up_id, result, time.time(), fingerprint=fingerprint, report=report)
        print(f"[follow-ups] The check of follow-up #{self.follow_up_id} ({self.agent_id}): {result.value}"
              f"{f' ({why})' if why else ''}")


class DueFollowUps:
    """The schedule runner's due work (ScheduleRunner.add_due_work): the follow-ups whose
    check is due now, each claimed once, as FollowUpSchedules (module docstring).
    `ownership` says which agents this host holds; Ownership() when not given."""

    def __init__(self, store: Optional[FollowUpStore] = None, ownership: Any = None):
        self.store = store or FollowUpStore()
        self.ownership = ownership
        self._waiting: Set[int] = set()  # follow-ups already logged as waiting for their agent

    async def __call__(self, now: float) -> List[FollowUpSchedule]:
        # Read on every call: turning the switch off stops the checks at the next tick.
        if not Config().proactive_delivery:
            return []
        for follow_up_id in await on_board(self.store.release_stale, now, CLAIM_S):
            print(f"[follow-ups] The check of follow-up #{follow_up_id} never finished in "
                  f"{CLAIM_S // 60} min: its claim is freed.")
        ownership = self.ownership or Ownership()
        specs: List[FollowUpSchedule] = []
        # Every due follow-up is read, and at most MAX_PER_TICK claimed: ones held elsewhere
        # don't take the places of the ones this host can run.
        for follow_up in await on_board(self.store.due, now, f"{local(now):%Y-%m-%d}"):
            if len(specs) >= MAX_PER_TICK:
                break
            if not held_here(follow_up.agent_id, ownership):
                if follow_up.id not in self._waiting:
                    self._waiting.add(follow_up.id)
                    print(f"[follow-ups] Follow-up #{follow_up.id} waits: this host doesn't hold "
                          f"{follow_up.agent_id} right now.")
                continue
            if not await on_board(self.store.claim, follow_up.id, now):
                continue  # another tick took it
            try:
                specs.append(FollowUpSchedule(follow_up, store=self.store))
            except Exception as e:
                # Claimed but unrunnable: finished at once, so it's due again an interval from now.
                await on_board(self.store.finish_check, follow_up.id, Result.FAILED, now)
                print(f"[follow-ups] Could not make the check of follow-up #{follow_up.id}: "
                      f"{type(e).__name__}: {e}")
        return specs


class FollowUpSweep:
    """Expiry, and team radio's opened and closed lines: one of the proactive service's
    sweeps (service.py), with the switch on or off (module docstring). `radio` is team
    radio's channel (core/channel/discord/radio.py); with none, no line is posted or marked."""

    def __init__(self, radio: Optional[RadioChannel] = None, store: Optional[FollowUpStore] = None,
                 team: Callable[[], Dict[str, PeerCard]] = cards, clock: Callable[[], float] = time.time):
        self.radio = radio
        self.store = store or FollowUpStore()
        self.team = team
        self.clock = clock

    async def __call__(self) -> None:
        now = self.clock()
        for ended in await on_board(self.store.expire, f"{local(now):%Y-%m-%d}", now):
            print(f"[follow-ups] Follow-up #{ended.id} ({ended.agent_id}) ended: until {ended.until} passed.")
        if self.radio is None:
            return
        pending = await on_board(self.store.pending_lines)
        if not pending:
            return
        team, since = self.team(), now - ANNOUNCE_S
        for follow_up in pending:
            state = follow_up.status  # the line it's owed: opened, or closed
            at = follow_up.opened_at if state is Status.OPEN else follow_up.closed_at
            if (at or 0) >= since:
                try:
                    await self.radio.post(radio_line(follow_up, team))
                except Exception as e:  # one attempt: the line is marked all the same
                    print(f"[follow-ups] Team radio missed follow-up #{follow_up.id}'s {state.value} line: "
                          f"{type(e).__name__}: {e}")
            await on_board(self.store.mark_line, follow_up.id, state)
