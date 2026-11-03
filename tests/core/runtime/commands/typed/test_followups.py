"""`!followups` (core/runtime/commands/typed/followups.py): the agents' follow-ups, and your cancel or extend."""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from core.config.settings import Config
from core.runtime.commands.base import TypedContext
from core.runtime.commands.registry import TYPED
from core.runtime.commands.typed.followups import BANNER, followups_text, next_text
from core.storage.follow_ups import RETRY_S, Check, Closer, FollowUpStore, Result, Status
from core.util.local_time import get_local_timezone
from tests.helpers import make_follow_up

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=get_local_timezone()).timestamp()  # a Sunday
HOUR, DAY = 3600, 86400
AWARD = Check(type="award_watch", args={"origin": "SFO", "destination": "MAD"})


@pytest.fixture
def store(tmp_path):
    return FollowUpStore(str(tmp_path / "memory.db"))


@pytest.fixture
def live():
    with Config().overriding(proactive_delivery=True):
        yield


def _open(store, goal, agent_id="reward-travel", check=None, every_s=2 * DAY, at=NOW - DAY, **values):
    opened = store.open(agent_id=agent_id, goal=goal, check=check or Check(type="model", what=goal), every_s=every_s,
                        until="2026-12-15", channel="lifestyle-perk", at=at)
    if values:
        store.update("follow_ups", values, {"id": opened.id})
    return store.get(opened.id)


def test_the_open_ones_soonest_check_first(store, live):
    award = _open(store, "Business award seats SFO→MAD", check=AWARD, interval_s=4 * DAY, next_check_at=NOW + 4 * DAY,
                  checks=6, finds=1)
    cabin = _open(store, "Tahoe cabin under $400", agent_id="excursion-planner", at=NOW - 2 * DAY + HOUR)
    snow = _open(store, "Snow report for Kirkwood", agent_id="excursion-planner", every_s=12 * HOUR,
                 at=NOW - 13 * HOUR)
    store.claim(snow.id, NOW)
    store.close(_open(store, "Closed already").id)
    lines = followups_text("", store, NOW).splitlines()
    assert lines == [
        "**3 open follow-up(s)**, soonest check first",
        f'#{snow.id} excursion-planner · "Snow report for Kirkwood" · every 12h · checking now · until 2026-12-15 · '
        f'0 checks, 0 finds · check: model, "Snow report for Kirkwood"',
        f'#{cabin.id} excursion-planner · "Tahoe cabin under $400" · every 2d · next Sun 10:00 · until 2026-12-15 · '
        f'0 checks, 0 finds · check: model, "Tahoe cabin under $400"',
        f'#{award.id} reward-travel · "Business award seats SFO→MAD" · every 4d (asked 2d) · next Thu 09:00 · '
        f'until 2026-12-15 · 6 checks, 1 find · check: award_watch',
    ]


def test_when_the_next_check_is():
    assert next_text(make_follow_up(claimed_at=NOW, next_check_at=NOW - 1), NOW) == "checking now"
    assert next_text(make_follow_up(next_check_at=NOW - 1), NOW) == "due now"
    assert next_text(make_follow_up(next_check_at=NOW + HOUR), NOW) == "next Sun 10:00"
    assert next_text(make_follow_up(next_check_at=NOW + 4 * DAY), NOW) == "next Thu 09:00"
    assert next_text(make_follow_up(next_check_at=NOW + 9 * DAY), NOW) == "next Nov 10 09:00"  # a weekday a week out would mislead


def test_with_the_switch_off_a_banner_says_nothing_is_checked(store):
    assert followups_text("", store, NOW).startswith("No open follow-ups.")  # nothing open: no banner
    _open(store, "Tahoe cabin under $400")
    lines = followups_text("", store, NOW).splitlines()
    assert lines[0] == BANNER == "⏸ PROACTIVE_DELIVERY is off: nothing is checked."
    assert lines[1] == "**1 open follow-up(s)**, soonest check first"


def test_the_last_closed_newest_first_with_why(store):
    first, second, third = (_open(store, goal) for goal in ("Award seats to MAD", "Tahoe cabin", "Snow report"))
    store.close(first.id, reason="booked it myself", by=Closer.YOU, at=NOW)
    store.close(second.id, agent_id="reward-travel", at=NOW + 60)
    store.close(third.id, reason="until passed", by=Closer.UNTIL, at=NOW + 120)
    assert followups_text("closed", store, NOW).splitlines() == [
        "**Last 3 closed**, newest first",
        f'#{third.id} reward-travel · "Snow report" · closed Nov 1 · until passed · 0 checks, 0 finds',
        f'#{second.id} reward-travel · "Tahoe cabin" · closed Nov 1 · reward-travel closed it · 0 checks, 0 finds',
        f'#{first.id} reward-travel · "Award seats to MAD" · closed Nov 1 · you cancelled it: booked it myself · '
        f"0 checks, 0 finds",
    ]
    assert followups_text("closed", FollowUpStore(str(store.path) + ".empty"), NOW) == "No follow-up has closed yet."


def test_cancel_closes_any_agents_follow_up_as_yours(store):
    cabin = _open(store, "Tahoe cabin under $400", agent_id="excursion-planner")
    assert followups_text(f"cancel #{cabin.id}  found one myself ", store, NOW) == (
        f'Cancelled follow-up #{cabin.id} (excursion-planner): "Tahoe cabin under $400".')
    closed = store.get(cabin.id)
    assert (closed.status, closed.closed_by, closed.close_reason) == (Status.CLOSED, Closer.YOU, "found one myself")
    assert followups_text(f"cancel {cabin.id}", store, NOW) == (
        f"No open follow-up #{cabin.id}. `!followups` lists the open ones.")  # already closed
    assert followups_text("cancel 99", store, NOW) == "No open follow-up #99. `!followups` lists the open ones."
    for args in ("cancel", "cancel the cabin one"):
        assert followups_text(args, store, NOW) == "Usage: `!followups cancel <id> [why]`"
    assert followups_text("everything", store, NOW) == (
        "Usage: `!followups [closed | cancel <id> [why] | extend <id> <YYYY-MM-DD>]`")


def test_extend_moves_any_agents_until_later(store):
    cabin = _open(store, "Tahoe cabin under $400", agent_id="excursion-planner", checks=4, finds=1)
    assert followups_text(f"extend #{cabin.id} 2026-12-31", store, NOW) == (
        f'Extended follow-up #{cabin.id} (excursion-planner): "Tahoe cabin under $400", until 2026-12-31.')
    extended = store.get(cabin.id)
    assert (extended.until, extended.checks, extended.finds) == ("2026-12-31", 4, 1)  # its history stays
    assert followups_text(f"extend {cabin.id} 2026-12-20", store, NOW) == (
        f"Can't extend #{cabin.id}: it already runs until 2026-12-31. Give a later date.")
    assert followups_text(f"extend {cabin.id} 2027-01-31", store, NOW) == (
        f"Can't extend #{cabin.id}: until is at most 90 days out: 2027-01-30 or sooner.")
    assert followups_text(f"extend {cabin.id} 2026-10-31", store, NOW) == (
        f"Can't extend #{cabin.id}: until 2026-10-31 has passed: it's 2026-11-01 today.")
    assert followups_text("extend 99 2026-12-31", store, NOW) == (
        "No open follow-up #99. `!followups` lists the open ones.")
    store.close(cabin.id)
    assert followups_text(f"extend {cabin.id} 2027-01-15", store, NOW) == (
        f"No open follow-up #{cabin.id}. `!followups` lists the open ones.")  # closed
    assert store.get(cabin.id).until == "2026-12-31"
    for args in ("extend", f"extend {cabin.id}", "extend the cabin to 2027-01-15", f"extend {cabin.id} 2027-01-15 ok"):
        assert followups_text(args, store, NOW) == "Usage: `!followups extend <id> <YYYY-MM-DD>`"


@pytest.mark.asyncio
async def test_it_is_typed_in_a_channel_the_bot_answers():
    channel = AsyncMock()
    assert await TYPED.answer("!followups", TypedContext(channel=channel, agent_id="main", answers_here=True))
    assert channel.send.await_args.args[0].startswith("No open follow-ups.")
    assert await TYPED.answer("!followups", TypedContext(channel=AsyncMock(), agent_id="main",
                                                         answers_here=False)) is None


# -- one-offs ---------------------------------------------------------------------------

def _once(store, goal, due_at, agent_id="agent-designer", **values):
    opened = store.open_once(agent_id=agent_id, goal=goal, due_at=due_at, channel="agent-management", at=NOW - DAY)
    if values:
        store.update("follow_ups", values, {"id": opened.id})
    return store.get(opened.id)


def test_a_one_off_is_listed_by_its_time_among_the_watches(store, live):
    logo = _once(store, "The logo redesign", NOW + 2 * DAY)                           # Tuesday 09:00
    agenda = _once(store, "The offsite agenda", NOW - 60, claimed_at=NOW - 30)        # its run holds it
    font = _once(store, "Pick a font", NOW - 60, agent_id="content-creator")          # its time has come
    budget = _once(store, "Q4 budget", NOW - 600, checks=1, next_check_at=NOW + 300)  # failed once
    award = _open(store, "Business award seats SFO→MAD", check=AWARD)                 # a watch, next check Monday
    assert followups_text("", store, NOW).splitlines() == [
        "**5 open follow-up(s)**, soonest check first",
        f'#{agenda.id} agent-designer · "The offsite agenda" · once at Sun 08:59 · bringing it up now',
        f'#{font.id} content-creator · "Pick a font" · once at Sun 08:59 · due now',
        f'#{budget.id} agent-designer · "Q4 budget" · once at Sun 08:50 · 1 failed attempt, again Sun 09:05',
        f'#{award.id} reward-travel · "Business award seats SFO→MAD" · every 2d · next Mon 09:00 · until 2026-12-15 · '
        f"0 checks, 0 finds · check: award_watch",
        f'#{logo.id} agent-designer · "The logo redesign" · once at Tue 09:00',
    ]


def test_closed_one_offs_say_when_they_were_for_and_how_they_ended(store):
    logo = _once(store, "The logo redesign", NOW + 2 * DAY)
    store.claim(logo.id, NOW + 2 * DAY)
    store.finish_check(logo.id, Result.FOUND, NOW + 2 * DAY + 60)
    font = _once(store, "Pick a font", NOW + DAY)
    for attempt in range(3):
        store.claim(font.id, NOW + DAY + attempt * RETRY_S)
        store.finish_check(font.id, Result.FAILED, NOW + DAY + attempt * RETRY_S)
    budget = _once(store, "Q4 budget", NOW + 3 * DAY)
    store.close(budget.id, reason="we talked already", by=Closer.YOU, at=NOW + 3 * DAY + 120)
    assert followups_text("closed", store, NOW).splitlines() == [
        "**Last 3 closed**, newest first",
        f'#{budget.id} agent-designer · "Q4 budget" · once at Nov 4 09:00 · closed Nov 4 · you cancelled it: we '
        f"talked already",
        f'#{logo.id} agent-designer · "The logo redesign" · once at Nov 3 09:00 · closed Nov 3 · brought up',
        f'#{font.id} agent-designer · "Pick a font" · once at Nov 2 09:00 · closed Nov 2 · couldn\'t bring it up · '
        f"3 attempts",
    ]


def test_extend_on_a_one_off_says_what_to_do_instead_and_cancel_works_as_for_any(store):
    logo = _once(store, "The logo redesign", NOW + 2 * DAY)
    assert followups_text(f"extend {logo.id} 2026-12-31", store, NOW) == (
        f"Can't extend #{logo.id}: it's a one-off, brought up once, at the time you asked for. Cancel it "
        f"(`!followups cancel {logo.id}`) and ask agent-designer for another.")
    assert store.get(logo.id).until == "2026-11-04"
    assert followups_text(f"cancel #{logo.id} not needed", store, NOW) == (
        f'Cancelled follow-up #{logo.id} (agent-designer): "The logo redesign".')
    cancelled = store.get(logo.id)
    assert (cancelled.status, cancelled.closed_by, cancelled.close_reason) == (Status.CLOSED, Closer.YOU, "not needed")
