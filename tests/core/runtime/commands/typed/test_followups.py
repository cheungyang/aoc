"""`!followups` (core/runtime/commands/typed/followups.py): the agents' follow-ups, and your cancel or extend."""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from core.config.settings import Config
from core.runtime.commands.base import TypedContext
from core.runtime.commands.registry import TYPED
from core.runtime.commands.typed.followups import BANNER, followups_text, next_text
from core.storage.follow_ups import Check, Closer, FollowUpStore, Status
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
