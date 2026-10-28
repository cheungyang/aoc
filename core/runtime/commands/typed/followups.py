"""`!followups`: the agents' follow-ups (core/storage/follow_ups.py), and your cancel.

    !followups                    the open ones, soonest check first: agent, goal,
                                  cadence, next check, until, checks and finds
    !followups closed             the last 10 closed, newest first, with why
    !followups cancel <id> [why]  closes one, any agent's, as yours

Read from memory.db, with no model. A cancel closes the follow-up as you
(`closed_by` "you"): team radio says so (core/proactive/follow_ups.py), and it
leaves its agent's Live Context. With the switch off and follow-ups still open,
a banner says nothing checks them. Agents are named by id, as in `!signals`.
"""
import asyncio
import time
from datetime import datetime
from typing import Optional

from core.config.settings import Config
from core.runtime.commands.base import ChannelCommand, TypedContext
from core.storage.follow_ups import Closer, FollowUp, FollowUpStore, Status, cadence_text
from core.util.local_time import get_local_timezone

RECENT = 10
BANNER = "⏸ PROACTIVE_DELIVERY is off: nothing is checked."
# A next check closer than this is named by its weekday; one further out, by its date.
WEEKDAY_S = 6 * 86400


def local(at: float) -> datetime:
    """`at` on your wall clock, not the container's."""
    return datetime.fromtimestamp(at, get_local_timezone())


def next_text(follow_up: FollowUp, now: float) -> str:
    """When a follow-up is checked next: "next Thu 09:00", "next Nov 12 09:00" a week or
    more out, "due now", or "checking now" while a check holds it."""
    if follow_up.claimed_at:
        return "checking now"
    at = follow_up.next_check_at
    if at <= now:
        return "due now"
    moment = local(at)
    day = f"{moment:%a}" if at - now < WEEKDAY_S else f"{moment:%b} {moment.day}"
    return f"next {day} {moment:%H:%M}"


def open_line(follow_up: FollowUp, now: float) -> str:
    """`#12 reward-travel · "goal" · every 4d (asked 2d) · next Thu 09:00 · until ... · checks · check: ...`"""
    every = cadence_text(follow_up.interval_s)
    if follow_up.interval_s != follow_up.every_s:
        every += f" (asked {cadence_text(follow_up.every_s)})"
    return (f"#{follow_up.id} {follow_up.agent_id} · \"{follow_up.goal}\" · every {every} · "
            f"{next_text(follow_up, now)} · until {follow_up.until} · {follow_up.tally()} · "
            f"check: {follow_up.check.text()}")


def closed_line(follow_up: FollowUp) -> str:
    """`#12 reward-travel · "goal" · closed Nov 3 · you cancelled it · 6 checks, 1 find`"""
    moment = local(follow_up.closed_at) if follow_up.closed_at else None
    day = f"{moment:%b} {moment.day}" if moment else "?"
    return (f"#{follow_up.id} {follow_up.agent_id} · \"{follow_up.goal}\" · closed {day} · "
            f"{follow_up.closed_why(follow_up.agent_id)} · {follow_up.tally()}")


def open_text(store: FollowUpStore, now: float) -> str:
    follow_ups = sorted(store.list(status=Status.OPEN), key=lambda f: (f.next_check_at, f.id))
    if not follow_ups:
        return "No open follow-ups. An agent opens one when you agree it should keep working on something."
    lines = [] if Config().proactive_delivery else [BANNER]
    lines.append(f"**{len(follow_ups)} open follow-up(s)**, soonest check first")
    lines.extend(open_line(follow_up, now) for follow_up in follow_ups)
    return "\n".join(lines)


def closed_text(store: FollowUpStore) -> str:
    follow_ups = store.closed(RECENT)
    if not follow_ups:
        return "No follow-up has closed yet."
    return "\n".join([f"**Last {len(follow_ups)} closed**, newest first"] + [closed_line(f) for f in follow_ups])


def cancel_text(store: FollowUpStore, args: str) -> str:
    words = (args or "").split(maxsplit=1)
    head = words[0].lstrip("#") if words else ""
    if not head.isdigit():
        return "Usage: `!followups cancel <id> [why]`"
    why = " ".join(words[1].split()) if len(words) > 1 else ""
    closed = store.close(int(head), reason=why, by=Closer.YOU)
    if closed is None:
        return f"No open follow-up #{head}. `!followups` lists the open ones."
    return f"Cancelled follow-up #{closed.id} ({closed.agent_id}): \"{closed.goal}\"."


def followups_text(args: str, store: Optional[FollowUpStore] = None, now: Optional[float] = None) -> str:
    """The reply to `!followups <args>` (module docstring)."""
    store = store or FollowUpStore()
    now = time.time() if now is None else now
    words = (args or "").split(maxsplit=1)
    if not words:
        return open_text(store, now)
    head = words[0].lower()
    if head == "closed" and len(words) == 1:
        return closed_text(store)
    if head == "cancel":
        return cancel_text(store, words[1] if len(words) > 1 else "")
    return f"Usage: `!followups {FollowUpsCommand.usage}`"


class FollowUpsCommand(ChannelCommand):
    name = "followups"
    usage = "[closed | cancel <id> [why]]"
    summary = "The agents' follow-ups: the open ones, the last closed, or cancel one."

    async def run(self, ctx: TypedContext, args: str) -> None:
        # memory.db is a file: read and written off the event loop.
        await ctx.reply(await asyncio.to_thread(followups_text, args))
