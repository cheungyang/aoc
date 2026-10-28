"""`!signals` (core/runtime/commands/typed/signals.py): traces, labels and stats from the Board."""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from core.runtime.commands.base import TypedContext
from core.runtime.commands.registry import TYPED
from core.runtime.commands.typed import signals as signals_module
from core.runtime.commands.typed.signals import p95, parse_why, part_of_day, signals_text, span, stats_text, when
from core.storage.signals import DecidedClose, SignalStore
from core.util.local_time import get_local_timezone


@pytest.fixture
def store(tmp_path):
    return SignalStore(str(tmp_path / "memory.db"))


@pytest.fixture(autouse=True)
def outcomes(monkeypatch):
    lines = []
    monkeypatch.setattr(signals_module, "record_outcome", lines.append)
    return lines


def _decision(store, verdicts=("deliver", "drop"), errors="", at=1_793_000_000.0, mode="shadow"):
    store.record_turn({"at": at - 10, "agent_id": "receipt-processor", "channel": "receipts",
                       "session_id": "receipt-processor:tool:receipts", "caller": "main", "tap": "effect",
                       "user_text": "here's my receipt", "reply_text": "Saved: 99 Ranch, $86.40.",
                       "effects": [{"tool": "ledger_append", "role": "trigger"}]})
    decision_id = store.start_decision(tap="effect", session_id="receipt-processor:tool:receipts",
                                       first_turn_at=at - 10, mode=mode, at=at)
    store.claim_turns([t["id"] for t in store.pending_turns()], decision_id)
    signal_id = store.add_signal({"decision_id": decision_id, "at": at, "kind": "artifact",
                                  "gist": "Groceries from 99 Ranch", "evidence_found": 1, "tags": "food",
                                  "source_agent": "receipt-processor"})
    for verdict in verdicts:
        store.add_route({"signal_id": signal_id, "decision_id": decision_id,
                         "to_agent": "meal-planner" if verdict != "drop" else "stranger",
                         "offer": "update the kitchen inventory", "asked": "now", "urgency": "now", "verdict": verdict,
                         "reason": "stranger is not a teammate with a card" if verdict == "drop" else "",
                         "kind_key": "meal-planner/artifact/food"})
    store.finish_decision(decision_id, took_ms=4500, tokens_in=4500, tokens_out=650, errors=errors)
    return decision_id


def _local(day, hour, minute=0):
    """A moment on your wall clock, in October 2026 (no clock change near it)."""
    return datetime(2026, 10, day, hour, minute, tzinfo=get_local_timezone()).timestamp()


def _deliver(store, decision_id, urgency, deliver_at, to_agent="meal-planner"):
    signal_id = store.decision(decision_id)["signals"][0]["id"]
    return store.add_route({"signal_id": signal_id, "decision_id": decision_id, "to_agent": to_agent,
                            "offer": "update the kitchen inventory", "asked": urgency, "urgency": urgency,
                            "verdict": "deliver", "kind_key": f"{to_agent}/artifact/food", "deliver_at": deliver_at})


def test_the_latest_decisions(store):
    assert signals_text("", store).startswith("No decisions yet.")
    decision_id = _decision(store)
    text = signals_text("", store)
    assert f"`#{decision_id}`" in text
    assert "effect in #receipts (receipt-processor) · meal-planner now, stranger dropped" in text


def test_a_trace(store):
    decision_id = _decision(store)
    text = signals_text(str(decision_id), store)
    assert f"**#{decision_id}**" in text and "10 s after the turn" in text and "Switchboard 4.5 s" in text
    assert "asked: here's my receipt" in text and "did: ledger_append (trigger)" in text
    assert 'Signal: artifact · "Groceries from 99 Ranch" · evidence ✓ · tags food' in text
    assert '→ meal-planner · "update the kitchen inventory" · deliver now' in text
    assert "→ stranger" in text and "drop: stranger is not a teammate with a card" in text
    assert signals_text("#999", store) == "No decision #999."


def test_a_trace_shows_how_each_delivery_went_and_what_came_of_it(store):
    at = _local(14, 21)
    decision_id = _decision(store, verdicts=("deliver",), at=at, mode="live")
    posted = store.decision(decision_id)["routes"][0]["id"]
    waiting = _deliver(store, decision_id, "today", _local(14, 21, 40))
    handed = _deliver(store, decision_id, "next", None)
    failed = _deliver(store, decision_id, "now", at)
    for route_id, due in ((posted, at), (waiting, _local(14, 21, 40)), (failed, at)):
        store.approve_delivery(route_id, to_agent="meal-planner", channel="meal-planning", deliver_at=due, at=at)
    store.finish_delivery(posted, "posted", at=at + 31, message_ids=["1234567890123456789"])
    store.set_outcome(posted, "engaged", at=at + 300)
    store.label_routes([posted], "good")
    store.hand_over([handed], to_agent="meal-planner", channel="meal-planning", now=_local(15, 8, 30))
    store.finish_delivery(handed, "posted", at=_local(15, 8, 31))
    store.finish_delivery(failed, "failed", error="the turn failed", at=at + 45)

    text = signals_text(str(decision_id), store)
    assert '→ meal-planner · "update the kitchen inventory" · deliver now · posted +31 s · engaged · you: ✅' in text
    assert "· deliver today · approved, due 10-14 21:40" in text
    assert "· deliver next · posted on a scheduled run +11.5 h" in text
    assert "· deliver now · failed +45 s (the turn failed)" in text


AWARD = DecidedClose(12, "I booked UA 990", "booked UA 990", closed=True, agent="Miles", goal="Award seats SFO→MAD")


def test_a_trace_shows_each_follow_up_close_and_what_came_of_it(store):
    live = _decision(store, mode="live")
    store.record_closes(live, [
        AWARD,
        DecidedClose(13, "Miles found seats", problem="evidence not found in your own words", agent="Miles",
                     goal="Seats to Lisbon"),
        DecidedClose(14, "stop the cabin watch", problem="not open"),
    ])
    text = signals_text(str(live), store)
    assert ('Close: closed follow-up #12 (Miles): "Award seats SFO→MAD" · booked UA 990\n'
            '  evidence: "I booked UA 990"') in text
    assert "Close: didn't close follow-up #13 (Miles): evidence not found in your own words" in text
    assert "Close: didn't close follow-up #14: not open" in text

    shadow = _decision(store)
    store.record_closes(shadow, [DecidedClose(12, "I booked UA 990", "booked UA 990", agent="Miles",
                                              goal="Award seats SFO→MAD"),
                                 DecidedClose(14, "stop the cabin watch", problem="not open")])
    text = signals_text(str(shadow), store)
    assert 'Close: would close follow-up #12 (Miles): "Award seats SFO→MAD" · booked UA 990' in text
    assert "Close: wouldn't close follow-up #14: not open" in text


def test_the_latest_decisions_name_their_closes(store):
    live = _decision(store, mode="live")
    store.record_closes(live, [AWARD, DecidedClose(14, "stop the cabin watch", problem="not open")])
    shadow = _decision(store, verdicts=(), errors="a close with no follow-up number was left out")
    store.record_closes(shadow, [DecidedClose(12, "I booked UA 990", agent="Miles", goal="Award seats SFO→MAD")])
    text = signals_text("", store)
    assert f"`#{live}`" in text and "meal-planner now, stranger dropped, closed #12, didn't close #14" in text
    assert "· would close #12" in text and "failed" not in text  # a close went through: not a failed decision


def test_labels_take_the_offered_routes_and_a_reason(store, outcomes):
    decision_id = _decision(store)
    reply = signals_text(f"{decision_id} bad wrong timing — he already knew", store)
    assert reply.startswith(f"❌ #{decision_id}: 1 route(s) labeled bad.")
    route = store.decision(decision_id)["routes"][0]
    assert (route["label"], route["label_reason"], route["label_note"]) == ("bad", "wrong timing", "he already knew")
    assert store.decision(decision_id)["routes"][1]["label"] is None  # the dropped one
    assert outcomes[0].startswith("[FEEDBACK]") and "Why: wrong timing." in outcomes[0]
    assert "Why? Add one of" in signals_text(f"{decision_id} bad", store)
    assert signals_text(f"{decision_id} good", store).startswith(f"✅ #{decision_id}")


def test_nothing_to_label_when_every_route_was_dropped(store):
    decision_id = _decision(store, verdicts=("drop",))
    assert "nothing to label" in signals_text(f"{decision_id} good", store)


def test_parse_why():
    assert parse_why("Wrong agent: Eric, not Daisy") == ("wrong agent", "Eric, not Daisy")
    assert parse_why("not worth it") == ("not worth it", None)
    assert parse_why("she cooked already") == (None, "she cooked already")


def test_a_missed_route(store, outcomes):
    assert "Say what should have been routed" in signals_text("missed", store)
    assert signals_text("missed Ramsay should have known I ate out", store).startswith("Noted as a missed route.")
    assert store.missed()[0]["text"] == "Ramsay should have known I ate out"
    assert outcomes == ['[FEEDBACK] The Switchboard missed a route, the user said: "Ramsay should have known I ate out".']


def test_missed_alone_lists_the_ones_you_reported(store):
    assert signals_text("missed", store).startswith("No missed routes yet.")
    store.add_missed("Ramsay should have known I ate out", at=_local(14, 20))
    store.add_missed("Eric should have known about Tahoe", at=_local(14, 21, 5))
    assert signals_text("missed", store).splitlines() == [
        "**Missed routes you reported**, latest 2",
        "10-14 21:05 · Eric should have known about Tahoe",
        "10-14 20:00 · Ramsay should have known I ate out",
    ]


def test_stats(store, monkeypatch):
    import time
    decision_id = _decision(store, at=time.time() - 60)
    signals_text(f"{decision_id} good", store)
    text = signals_text("stats", store)
    assert "Decisions: 1" in text and "Routes: 1 deliver · 0 absorb · 1 dropped" in text
    assert "Your labels: 1 ✅ · 0 ❌ → precision 1.00" in text
    assert "effect 10 s to decide, run 4.5 s" in text
    assert "Would deliver" not in text  # no delivery time known yet
    assert "Outcomes" not in text and "`now` routes" not in text  # nothing posted: shadow
    assert "Duplicate posts" not in text


def test_stats_show_when_deliveries_would_land(store):
    evening = _decision(store, verdicts=(), at=_local(14, 21))
    _deliver(store, evening, "next", _local(15, 8, 30))
    _deliver(store, evening, "now", _local(14, 21))
    _deliver(store, evening, "today", _local(14, 21, 40))
    late = _decision(store, verdicts=(), at=_local(14, 23, 30))
    _deliver(store, late, "now", _local(14, 23, 30))
    lines = stats_text(store, now=_local(15, 12)).splitlines()
    assert ("Would deliver: now 2 (median 0 s after the decision) · today 1 (median 40 min after the decision)"
            " · next 1 (median 11.5 h after the decision)") in lines
    assert "  by time of day: night 1 · morning 1 · afternoon 0 · evening 2 (night is 22:00-07:00)" in lines


def test_stats_show_what_came_of_the_posts_and_how_fast_they_came(store):
    _decision(store, verdicts=(), at=_local(1, 12))  # shadow, before the switch went on
    at = _local(5, 12)
    live = _decision(store, verdicts=(), at=at, mode="live")  # its source turn ended at `at - 10`
    for acked, posted in ((2, 40), (3, 50), (12, None)):
        route_id = _deliver(store, live, "now", at)
        store.approve_delivery(route_id, to_agent="meal-planner", channel="meal-planning", deliver_at=at)
        store.ack_delivery(route_id, at=at - 10 + acked)
        if posted is not None:
            store.finish_delivery(route_id, "posted", at=at - 10 + posted)
    for to_agent, outcome in [("meal-planner", "engaged")] * 3 + [("meal-planner", "ignored"),
                                                                  ("excursion-planner", "engaged"),
                                                                  ("excursion-planner", "declined"),
                                                                  ("excursion-planner", "declined")]:
        store.set_outcome(_deliver(store, live, "today", None, to_agent=to_agent), outcome)

    # Two weeks since the first live decision: the declines are counted over those, not the shadow days before.
    lines = stats_text(store, now=_local(19, 12), days=30).splitlines()
    assert ("Outcomes of the posts: 4 engaged · 1 ignored · 2 declined → 57% engaged (target ≥ 50% engaged, "
            "and ≤ 1 decline per agent a week)") in lines
    assert "  excursion-planner: 1 engaged · 0 ignored · 2 declined → 33% engaged · 1.0 declines a week" in lines
    assert "  meal-planner: 3 engaged · 1 ignored · 0 declined → 75% engaged · 0.0 declines a week" in lines
    assert ("`now` routes: reaction p95 12 s after the turn (target ≤ 10 s; 3 so far) · post p95 50 s "
            "(target ≤ 60 s; 2 so far)") in lines
    assert "Duplicate posts: 0 (target 0: the same fact posted to one agent twice within 72 h)" in lines
    # In the first week, a decline is one decline a week: a pilot's first days aren't scaled up.
    assert "→ 33% engaged · 2.0 declines a week" in stats_text(store, now=_local(7, 12), days=30)


def test_p95_is_the_nearest_rank():
    assert (p95([5.0]), p95([3.0, 1.0, 2.0]), p95(list(range(1, 11))), p95(list(range(1, 21)))) == (5.0, 3.0, 10, 19)


def test_tokens_a_day_count_the_days_there_were_decisions(store):
    _decision(store, verdicts=(), at=_local(10, 12))
    _decision(store, verdicts=(), at=_local(13, 12))
    text = stats_text(store, now=_local(14, 0))
    assert "Decisions: 2 · tokens 9,000 in / 1,300 out (about 2,571 / 371 a day, over 3.5 days)" in text
    text = stats_text(store, now=_local(13, 18), days=2)  # less than a day: counted as one
    assert "Decisions: 1 · tokens 4,500 in / 650 out (about 4,500 / 650 a day, over 1 day)" in text


def test_times_are_on_your_wall_clock():
    assert when(_local(14, 21, 5)) == "10-14 21:05" and when(None) == "?"
    assert [part_of_day(_local(14, hour)) for hour in (6, 7, 12, 17, 22)] == [
        "night", "morning", "afternoon", "evening", "night"]
    assert (span(40), span(35 * 60), span(2.5 * 3600)) == ("40 s", "35 min", "2.5 h")


def test_why_finds_a_radio_lines_decision(store):
    decision_id = _decision(store)
    route_id = store.decision(decision_id)["routes"][0]["id"]
    store.set_radio_message(route_id, "1234567890123456789")
    text = signals_text("why https://discord.com/channels/1/2/1234567890123456789", store)
    assert f"**#{decision_id}**" in text
    assert "isn't a team radio line" in signals_text("why https://discord.com/channels/1/2/3", store)


def test_why_finds_the_decision_behind_a_proactive_post(store):
    _decision(store)
    decision_id = _decision(store, mode="live")
    route_id = store.decision(decision_id)["routes"][0]["id"]
    store.approve_delivery(route_id, to_agent="meal-planner", channel="meal-planning", deliver_at=1.0)
    store.finish_delivery(route_id, "posted", message_ids=["1111111111111111111", "2222222222222222222"])
    text = signals_text("why https://discord.com/channels/1/2/2222222222222222222", store)  # its second message
    assert text.startswith(f"**#{decision_id}**")
    assert signals_text("why https://discord.com/channels/1/2/3333333333333333333", store).startswith(
        "That isn't a team radio line or a proactive post I know.")


def test_anything_else_shows_the_usage(store):
    assert signals_text("loud", store).startswith("Usage: `!signals")


@pytest.mark.asyncio
async def test_the_command_answers_where_the_bot_does(store, monkeypatch):
    monkeypatch.setattr(signals_module, "SignalStore", lambda: store)
    channel = AsyncMock()
    assert await TYPED.answer("!signals", TypedContext(channel=channel, agent_id="main", answers_here=True))
    assert channel.send.await_args.args[0].startswith("No decisions yet.")
    assert await TYPED.answer("!signals", TypedContext(channel=AsyncMock(), agent_id="main",
                                                       answers_here=False)) is None
