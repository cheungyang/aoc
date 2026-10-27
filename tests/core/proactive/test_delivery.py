"""Delivery (core/proactive/delivery.py): a live route's proactive turn, from a claimed row."""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from core.channel.discord.ownership import DEV, PROD
from core.channel.response_parser import AgentResponse
from core.config.settings import Config
from core.proactive import delivery as delivery_module
from core.proactive.delivery import (
    EXPIRE_S,
    NEXT_SINCE_S,
    STUCK_S,
    THINKING,
    DeliveryOutput,
    DeliverySchedule,
    DueDeliveries,
    SignalsForMe,
    is_silent,
    render_findings,
    render_prompt,
    window_text,
)
from core.scheduler.spec import PromptSchedule, ScheduleContext, SessionPolicy
from core.storage.signals import SignalStore
from core.util.local_time import get_local_timezone

NOW = datetime(2026, 10, 24, 9, 31, tzinfo=get_local_timezone()).timestamp()
TURN = SimpleNamespace(source="proactive")  # the turn's context, as the output sees it


@pytest.fixture
def store(tmp_path):
    return SignalStore(str(tmp_path / "memory.db"))


@pytest.fixture
def live():
    with Config().overriding(proactive_delivery=True):
        yield


def _approved(store, deliver_at=NOW, **signal):
    """A receipt's route to Ramsay, approved for delivery in #meal-planning."""
    decision_id = store.start_decision(tap="effect", session_id="s1", first_turn_at=NOW, mode="live", at=NOW)
    values = {"decision_id": decision_id, "at": NOW, "kind": "artifact", "gist": "Groceries from 99 Ranch, Oct 24",
              "evidence": "Saved: 99 Ranch, $86.40.", "tags": "food",
              "payload": {"tool": "ledger_append", "vendor": "99 Ranch", "total": 86.4},
              "source_agent": "receipt-processor", "source_channel": "receipts"}
    values.update(signal)
    route_id = store.add_route({"signal_id": store.add_signal(values), "decision_id": decision_id,
                                "to_agent": "meal-planner", "offer": "update the kitchen inventory", "asked": "now",
                                "urgency": "now", "verdict": "deliver", "note": "Add them.", "deliver_at": deliver_at})
    store.approve_delivery(route_id, to_agent="meal-planner", channel="meal-planning", deliver_at=deliver_at, at=NOW)
    return route_id


def _row(store, route_id):
    return next(r for r in store.due_deliveries(NOW) if r["route_id"] == route_id)


class Channel:
    """A Discord channel: what was posted in it."""

    def __init__(self):
        self.sent = []

    async def send(self, text, **kwargs):
        self.sent.append(text)


class Holds:
    """Ownership, stood in for: whether the claims are known yet, and which agents this host holds."""

    def __init__(self, role=PROD, ready=True, held=("meal-planner",)):
        self.role, self.ready, self.held = role, ready, set(held)

    def is_ready(self):
        return self.ready

    def is_mine(self, agent_id):
        return self.ready and agent_id in self.held


class TestThePrompt:

    def test_it_has_the_fact_the_offer_and_the_rules_and_never_the_source(self, store):
        prompt = render_prompt(_row(store, _approved(store, until="2026-10-27")), NOW)
        for part in ("It is Saturday 2026-10-24 09:31.", "Groceries from 99 Ranch, Oct 24",
                     'The words it came from: "Saved: 99 Ranch, $86.40."', '"vendor": "99 Ranch"', "True until 2026-10-27",
                     "What you could offer: update the kitchen inventory", "The idea: Add them.", "first person",
                     "Never mention another agent, the Switchboard, or how you learned this",
                     "at most 1,500 characters", "at most one question, or a poll",
                     "reply with exactly <silent/> and nothing else"):
            assert part in prompt, part
        # Who learned it, where, and with which tool stay out.
        for source in ("receipt-processor", "Receipt Processor", "receipts", "ledger_append"):
            assert source not in prompt, source

    def test_what_it_doesnt_have_is_left_out(self, store):
        prompt = render_prompt(_row(store, _approved(store, evidence="", payload={})), NOW)
        assert "The words it came from" not in prompt and "Details" not in prompt
        assert "True until" not in prompt and "When it matters" not in prompt

    def test_a_window_in_words(self):
        assert window_text("2026-10-24", "2026-10-27") == "2026-10-24 to 2026-10-27"
        assert window_text("2026-10-24", "2026-10-24") == "2026-10-24"
        assert (window_text("2026-10-24", None), window_text(None, "2026-10-27")) == ("from 2026-10-24",
                                                                                    "until 2026-10-27")
        assert window_text(None, None) == ""


@pytest.mark.parametrize("reply, silent", [
    ("<silent/>", True),
    ("  <silent />\n", True),
    ("<SILENT/>", True),
    ("", True),
    ("  \n", True),
    (None, True),
    ("Saw the 99 Ranch run. Want me to add it all to the kitchen inventory?", False),
    (AgentResponse.from_string("<speech>Nothing new.</speech>\n<silent/>"), True),
    (AgentResponse(text="", poll_data={"question": "Add them?", "options": [{"text": "Yes"}]}), False),
])
def test_is_silent(reply, silent):
    assert is_silent(reply) is silent


@pytest.mark.asyncio
async def test_a_silent_reply_posts_nothing():
    channel = Channel()
    silent, spoken = DeliveryOutput(channel), DeliveryOutput(channel)
    await silent.deliver(TURN, AgentResponse.from_string("<silent/>"))
    assert channel.sent == [] and silent.outcome == "silent"
    await spoken.deliver(TURN, AgentResponse.from_string("Want these in the kitchen inventory?"))
    assert channel.sent == ["Want these in the kitchen inventory?"] and spoken.outcome == "posted"


class TestTheTurn:

    def test_it_is_the_recipients_one_shot_turn_in_its_home_channel(self, store):
        row = _row(store, _approved(store))
        spec = DeliverySchedule(row, store=store, now=NOW)
        assert (spec.agent_id, spec.channel, spec.source) == ("meal-planner", "meal-planning", "proactive")
        assert spec.schedule_id == "proactive:meal-planner"  # one schedule-state row per agent
        assert spec.session_policy is SessionPolicy.STATELESS
        assert spec.thinking_level == THINKING == "low"
        ctx = ScheduleContext(schedule_id=spec.schedule_id, agent_id=spec.agent_id, now=NOW)
        assert spec.has_work(ctx)
        assert spec.build_prompt(ctx) == render_prompt(row, NOW)

    @pytest.mark.asyncio
    async def test_its_run_finishes_the_delivery(self, store):
        posted, silent, failed, nowhere = (_approved(store) for _ in range(4))
        ctx = ScheduleContext(schedule_id="proactive:meal-planner", agent_id="meal-planner", now=NOW)
        for route_id, reply in ((posted, "Want these in the kitchen inventory?"), (silent, "<silent/>")):
            spec = DeliverySchedule(_row(store, route_id), store=store, now=NOW)
            await spec.output_for(Channel()).deliver(TURN, AgentResponse.from_string(reply))
            spec.on_success(ctx)
        DeliverySchedule(_row(store, failed), store=store, now=NOW).on_failure(ctx)
        DeliverySchedule(_row(store, nowhere), store=store, now=NOW).on_success(ctx)  # its channel wasn't found

        statuses = [store.delivery(r)["status"] for r in (posted, silent, failed, nowhere)]
        assert statuses == ["posted", "silent", "failed", "failed"]
        assert store.route(posted)["delivered_at"] is not None and store.route(silent)["delivered_at"] is None
        assert store.delivery(failed)["error"] == "the turn failed"
        assert store.delivery(nowhere)["error"] == "nothing reached #meal-planning"


class TestTheProvider:

    @pytest.mark.asyncio
    async def test_two_calls_claim_a_delivery_once(self, store, live):
        route_id = _approved(store)
        provider = DueDeliveries(store)
        first, second = await provider(NOW), await provider(NOW + 1)
        assert [(type(s), s.route_id) for s in first] == [(DeliverySchedule, route_id)] and second == []
        assert store.delivery(route_id)["status"] == "running"

    @pytest.mark.asyncio
    async def test_calls_at_once_claim_it_once(self, store, live):
        _approved(store)
        provider = DueDeliveries(store)
        claimed = await asyncio.gather(*(provider(NOW) for _ in range(3)))
        assert sorted(len(specs) for specs in claimed) == [0, 0, 1]

    @pytest.mark.asyncio
    async def test_a_delivery_not_due_yet_waits(self, store, live):
        route_id = _approved(store, deliver_at=NOW + 60)
        assert await DueDeliveries(store)(NOW) == []
        assert store.delivery(route_id)["status"] == "approved"

    @pytest.mark.asyncio
    async def test_a_stuck_delivery_is_failed_not_run_again(self, store, live):
        route_id = _approved(store)
        provider = DueDeliveries(store)
        assert len(await provider(NOW)) == 1  # claimed, and its run never finishes
        assert await provider(NOW + STUCK_S - 1) == []
        assert store.delivery(route_id)["status"] == "running"
        assert await provider(NOW + STUCK_S + 1) == []
        delivery = store.delivery(route_id)
        assert (delivery["status"], delivery["error"]) == ("failed", "stuck; not retried")

    @pytest.mark.asyncio
    async def test_with_the_switch_off_nothing_is_due(self, store):
        route_id = _approved(store)
        assert await DueDeliveries(store)(NOW) == []
        assert store.delivery(route_id)["status"] == "approved"

    @pytest.mark.asyncio
    async def test_a_delivery_due_over_3_hours_ago_is_failed_not_posted_late(self, store, live):
        stale, late_but_fresh = _approved(store, deliver_at=NOW - EXPIRE_S - 1), _approved(store, deliver_at=NOW - 60)
        assert [s.route_id for s in await DueDeliveries(store)(NOW)] == [late_but_fresh]
        delivery = store.delivery(stale)
        assert (delivery["status"], delivery["error"]) == ("failed", "expired; not posted late")

    @pytest.mark.asyncio
    async def test_a_delivery_whose_agent_another_host_holds_waits_until_it_comes_back(self, store, live, capsys):
        route_id = _approved(store)
        holds = Holds(held=())
        provider = DueDeliveries(store, ownership=holds)
        assert await provider(NOW) == [] and await provider(NOW + 30) == []
        assert store.delivery(route_id)["status"] == "approved"  # never claimed: no stuck run to fail
        assert capsys.readouterr().out.count("waits") == 1  # said once, not every tick
        holds.held.add("meal-planner")
        assert [s.route_id for s in await provider(NOW + 60)] == [route_id]

    @pytest.mark.asyncio
    async def test_a_delivery_whose_agent_never_comes_back_expires(self, store, live):
        route_id = _approved(store)
        provider = DueDeliveries(store, ownership=Holds(held=()))
        assert await provider(NOW) == []
        assert await provider(NOW + EXPIRE_S + 1) == []
        assert store.delivery(route_id)["error"] == "expired; not posted late"

    @pytest.mark.asyncio
    async def test_before_the_claims_are_known_prod_delivers_and_dev_waits(self, store, live):
        route_id = _approved(store)
        assert await DueDeliveries(store, ownership=Holds(role=DEV, ready=False))(NOW) == []
        assert store.delivery(route_id)["status"] == "approved"
        prod = DueDeliveries(store, ownership=Holds(role=PROD, ready=False))
        assert [s.route_id for s in await prod(NOW)] == [route_id]

    @pytest.mark.asyncio
    async def test_a_turn_that_cant_be_made_is_failed_at_once(self, store, live, monkeypatch):
        route_id = _approved(store)

        def unreadable(delivery, now):
            raise KeyError("gist")

        monkeypatch.setattr(delivery_module, "render_prompt", unreadable)
        assert await DueDeliveries(store)(NOW) == []
        delivery = store.delivery(route_id)
        assert (delivery["status"], delivery["error"]) == ("failed", "KeyError: 'gist'")


def _next(store, at=NOW, **route):
    """A live `next` route to Ramsay: the groceries, for his next scheduled run."""
    decision_id = store.start_decision(tap="effect", session_id="s1", first_turn_at=at, mode="live", at=at)
    signal_id = store.add_signal({"decision_id": decision_id, "at": at, "kind": "artifact",
                                  "gist": "Groceries from 99 Ranch, Oct 24", "evidence": "Saved: 99 Ranch, $86.40.",
                                  "tags": "food", "payload": {"tool": "ledger_append", "vendor": "99 Ranch"},
                                  "source_agent": "receipt-processor", "source_channel": "receipts"})
    values = {"signal_id": signal_id, "decision_id": decision_id, "to_agent": "meal-planner",
              "offer": "update the kitchen inventory", "asked": "next", "urgency": "next", "verdict": "deliver",
              "note": "Check the freezer."}
    values.update(route)
    return store.add_route(values)


def _evening_run():
    """Ramsay's 21:00 run, as the schedule runner hands it to a briefing provider."""
    spec = PromptSchedule(prompt="Plan tomorrow's meals.", schedule_id="meal-planner:evening",
                          agent_id="meal-planner", cron="0 21 * * *", channel="meal-planning")
    return spec, ScheduleContext(schedule_id=spec.schedule_id, agent_id="meal-planner", now=NOW, channel="meal-planning")


class TestTheNextTouchpoint:

    @pytest.mark.asyncio
    async def test_a_scheduled_run_gets_its_next_routes_as_findings_from_the_team(self, store, live):
        route_id = _next(store, window_start="2026-10-24", window_end="2026-10-26")
        briefing = await SignalsForMe(store)(*_evening_run())
        for part in ('<findings source="your team">', "- Groceries from 99 Ranch, Oct 24",
                     'Details: {"vendor": "99 Ranch"}', "When it matters: 2026-10-24 to 2026-10-26.",
                     "What you could offer: update the kitchen inventory", "The idea: Check the freezer.",
                     "Use what fits this run, in your own voice", "Never name another agent, or say how you learned it",
                     "Leave out what doesn't fit"):
            assert part in briefing.block, part
        # Who learned it, where, and with which tool stay out.
        for source in ("receipt-processor", "Receipt Processor", "receipts", "ledger_append"):
            assert source not in briefing.block, source
        delivery = store.delivery(route_id)
        assert (delivery["status"], delivery["channel"], delivery["deliver_at"]) == ("handed", "meal-planning", NOW)

        await briefing.finish("posted")
        assert store.delivery(route_id)["status"] == "posted" and store.route(route_id)["delivered_at"] is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome, error", [("silent", ""), ("failed", "the scheduled run failed")])
    async def test_its_run_finishes_them_as_it_went(self, store, live, outcome, error):
        route_id = _next(store)
        briefing = await SignalsForMe(store)(*_evening_run())
        await briefing.finish(outcome)
        delivery = store.delivery(route_id)
        assert (delivery["status"], delivery["error"]) == (outcome, error)
        assert store.route(route_id)["delivered_at"] is None

    @pytest.mark.asyncio
    async def test_with_the_switch_off_nothing_rides_along(self, store):
        route_id = _next(store)
        assert await SignalsForMe(store)(*_evening_run()) is None
        assert store.delivery(route_id) is None

    @pytest.mark.asyncio
    async def test_a_run_with_nothing_for_its_agent_gets_no_block(self, store, live):
        _next(store, to_agent="excursion-planner")
        assert await SignalsForMe(store)(*_evening_run()) is None

    @pytest.mark.asyncio
    async def test_at_most_5_oldest_first_from_the_last_7_days(self, store, live):
        stale = _next(store, at=NOW - NEXT_SINCE_S - 60, note="stale")
        routes = [_next(store, at=NOW - 3600 * (6 - i), note=f"idea {i}") for i in range(6)]
        block = (await SignalsForMe(store)(*_evening_run())).block
        assert [f"The idea: idea {i}" in block for i in range(6)] == [True] * 5 + [False]
        assert "stale" not in block
        assert store.delivery(stale) is None and store.delivery(routes[5]) is None  # the newest waits for the next run

    @pytest.mark.asyncio
    async def test_two_runs_at_once_never_share_a_route(self, store, live):
        route_id = _next(store)
        provider = SignalsForMe(store)
        briefings = await asyncio.gather(provider(*_evening_run()), provider(*_evening_run()))
        assert sorted(b is not None for b in briefings) == [False, True]
        assert store.delivery(route_id)["status"] == "handed"

    @pytest.mark.asyncio
    async def test_a_handed_route_is_never_a_proactive_turn_nor_failed_as_stuck(self, store, live):
        route_id = _next(store)
        await SignalsForMe(store)(*_evening_run())
        due = DueDeliveries(store)
        assert await due(NOW) == [] and await due(NOW + STUCK_S + EXPIRE_S + 1) == []
        assert store.delivery(route_id)["status"] == "handed"  # its run finishes it, however long it takes

    def test_what_a_route_doesnt_have_is_left_out(self):
        block = render_findings([{"gist": "In Tahoe until Tue", "payload": {"tool": "calendar_add"}}])
        assert "- In Tahoe until Tue" in block
        for part in ("Details", "True until", "When it matters", "What you could offer", "The idea", "calendar_add"):
            assert part not in block, part
