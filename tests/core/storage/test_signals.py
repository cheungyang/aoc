"""The proactive Board (core/storage/signals.py)."""
import pytest

from core.storage.signals import SignalStore


def _turn(**overrides):
    row = {"at": 100.0, "agent_id": "receipt-processor", "channel": "receipts", "session_id": "s1",
           "source": "tool", "surface": "tool", "caller": "main", "tap": "effect",
           "user_text": "here's my receipt", "reply_text": "Saved.", "memory": ["[CONTEXT] Costco run"],
           "effects": [{"tool": "ledger_append", "args": {"vendor": "Costco"}}], "context": {"touched": []}}
    row.update(overrides)
    return row


def _decision(store, **overrides):
    values = dict(tap="effect", session_id="s1", first_turn_at=100.0, mode="shadow", at=110.0)
    values.update(overrides)
    return store.start_decision(**values)


def _signal(store, decision_id, **overrides):
    values = {"decision_id": decision_id, "at": 110.0, "kind": "artifact", "gist": "Groceries from Costco",
              "evidence": "Costco", "evidence_found": 1, "tags": "food", "payload": {"vendor": "Costco"},
              "source_agent": "receipt-processor", "source_channel": "receipts", "dedupe_key": "artifact:costco"}
    values.update(overrides)
    return store.add_signal(values)


def _route(store, signal_id, decision_id, **overrides):
    values = {"signal_id": signal_id, "decision_id": decision_id, "to_agent": "meal-planner",
              "offer": "update kitchen inventory", "asked": "now", "urgency": "now", "verdict": "deliver",
              "kind_key": "meal-planner/artifact/food"}
    values.update(overrides)
    return store.add_route(values)


@pytest.fixture
def store(tmp_path):
    return SignalStore(str(tmp_path / "memory.db"))


def test_a_turn_round_trips_with_its_json(store):
    turn_id = store.record_turn(_turn())
    (row,) = store.pending_turns("s1")
    assert row["id"] == turn_id
    assert row["memory"] == ["[CONTEXT] Costco run"]
    assert row["effects"][0]["args"] == {"vendor": "Costco"}
    assert row["decision_id"] is None


def test_unread_turns_are_per_session_oldest_first(store):
    store.record_turn(_turn(session_id="s1", at=1.0))
    store.record_turn(_turn(session_id="s2", at=2.0))
    store.record_turn(_turn(session_id="s1", at=3.0))
    assert [t["at"] for t in store.pending_turns("s1")] == [1.0, 3.0]
    assert len(store.pending_turns()) == 3


def test_a_turn_is_claimed_once(store):
    first, second = store.record_turn(_turn()), store.record_turn(_turn())
    assert store.claim_turns([first, second], decision_id=7) == [first, second]
    # A second run claiming the same rows gets none of them.
    assert store.claim_turns([first, second], decision_id=8) == []
    assert store.pending_turns("s1") == []
    assert [t["id"] for t in store.turns_of(7)] == [first, second]


def test_a_decision_comes_back_with_its_turns_signals_and_routes(store):
    turn_id = store.record_turn(_turn())
    decision_id = _decision(store)
    store.claim_turns([turn_id], decision_id)
    signal_id = _signal(store, decision_id)
    _route(store, signal_id, decision_id)
    store.finish_decision(decision_id, took_ms=2100, tokens_in=3000, tokens_out=200, reply="<switchboard_response/>")

    decision = store.decision(decision_id)
    assert decision["took_ms"] == 2100
    assert [t["id"] for t in decision["turns"]] == [turn_id]
    assert decision["signals"][0]["payload"] == {"vendor": "Costco"}
    assert decision["routes"][0]["gist"] == "Groceries from Costco"
    assert store.recent_decisions(5)[0]["routes"][0]["to_agent"] == "meal-planner"


def test_dedupe_finds_a_recent_route_of_the_same_fact_to_the_same_agent(store):
    decision_id = _decision(store)
    signal_id = _signal(store, decision_id, at=1000.0)
    _route(store, signal_id, decision_id)
    assert store.recent_route(to_agent="meal-planner", dedupe_key="artifact:costco", since=900.0) is not None
    assert store.recent_route(to_agent="meal-planner", dedupe_key="artifact:costco", since=1001.0) is None
    assert store.recent_route(to_agent="reward-travel", dedupe_key="artifact:costco", since=900.0) is None


def test_a_dropped_route_doesnt_count_for_dedupe(store):
    decision_id = _decision(store)
    _route(store, _signal(store, decision_id, at=1000.0), decision_id, verdict="drop")
    assert store.recent_route(to_agent="meal-planner", dedupe_key="artifact:costco", since=900.0) is None


def test_the_board_shows_signals_still_true(store):
    decision_id = _decision(store)
    _signal(store, decision_id, kind="situation", gist="In Tahoe", until="2026-10-27", at=50.0)
    _signal(store, decision_id, kind="situation", gist="Was away", until="2026-10-01", at=60.0)
    _signal(store, decision_id, gist="Old", at=1.0)
    gists = [s["gist"] for s in store.active_signals(since=10.0, today="2026-10-26")]
    assert gists == ["In Tahoe"]


def test_a_label_counts_toward_acceptance_and_a_relabel_moves_it(store):
    decision_id = _decision(store)
    route_id = _route(store, _signal(store, decision_id), decision_id)
    store.label_routes([route_id], "good")
    assert store.acceptance("meal-planner/artifact/food") == {"engaged": 1, "ignored": 0, "declined": 0}
    store.label_routes([route_id], "bad", reason="wrong timing", note="he knew")
    assert store.acceptance("meal-planner/artifact/food") == {"engaged": 0, "ignored": 0, "declined": 1}
    route = store.route(route_id)
    assert (route["label"], route["label_reason"], route["label_note"]) == ("bad", "wrong timing", "he knew")
    with pytest.raises(ValueError):
        store.label_routes([route_id], "meh")


def test_a_radio_line_or_poll_finds_its_route(store):
    decision_id = _decision(store)
    route_id = _route(store, _signal(store, decision_id), decision_id)
    store.set_radio_message(route_id, 123)
    store.set_poll_message(route_id, "456")
    assert store.route_by_radio_message("123")["id"] == route_id
    assert store.route_by_poll_message(456)["id"] == route_id
    assert store.route_by_radio_message("999") is None


def test_routes_waiting_for_a_time(store):
    decision_id = _decision(store)
    signal_id = _signal(store, decision_id)
    waiting = _route(store, signal_id, decision_id, urgency="today")
    _route(store, signal_id, decision_id, verdict="absorb")
    assert [r["id"] for r in store.undelivered()] == [waiting]
    store.set_route_timing(waiting, deliver_at=500.0)
    assert store.undelivered() == []


def test_when_you_were_last_active(store):
    store.record_turn(_turn(at=10.0, agent_id="day-planner"))
    store.record_turn(_turn(at=20.0, agent_id="meal-planner", caller=""))         # an agent asked
    store.record_turn(_turn(at=30.0, agent_id="meal-planner", tap="scheduled"))   # a schedule posted
    assert store.last_turn_at() == 10.0
    assert store.last_turn_at(agent_id="meal-planner") is None
    assert store.user_turn_times(since=0.0) == [10.0]


def test_stats_count_decisions_verdicts_labels_and_tap_delays(store):
    decision_id = _decision(store, at=110.0, first_turn_at=100.0)
    store.finish_decision(decision_id, took_ms=1500, tokens_in=3000, tokens_out=100)
    route_id = _route(store, _signal(store, decision_id), decision_id)
    _route(store, _signal(store, decision_id), decision_id, verdict="drop")
    store.label_routes([route_id], "good")
    store.add_missed("Ramsay should have known I ate out")

    stats = store.stats(since=0.0)
    assert stats["decisions"] == 1 and stats["tokens_in"] == 3000 and stats["first_at"] == 110.0
    assert stats["verdicts"] == {"deliver": 1, "drop": 1}
    assert stats["labels"] == {"good": 1}
    assert stats["taps"] == {"effect": [(10.0, 1500)]}
    assert stats["missed"] == 1


def test_would_deliver_lists_the_deliveries_whose_time_is_known(store):
    old = _decision(store, at=50.0)
    _route(store, _signal(store, old), old, deliver_at=60.0)                      # decided before `since`
    decision_id = _decision(store, at=110.0)
    signal_id = _signal(store, decision_id)
    _route(store, signal_id, decision_id, urgency="today", deliver_at=4000.0)
    _route(store, signal_id, decision_id, deliver_at=110.0)
    _route(store, signal_id, decision_id, urgency="next")                         # time not known yet
    _route(store, signal_id, decision_id, verdict="absorb", deliver_at=110.0)     # absorbed, not delivered
    assert store.would_deliver(since=100.0) == [
        {"urgency": "now", "decided_at": 110.0, "deliver_at": 110.0},
        {"urgency": "today", "decided_at": 110.0, "deliver_at": 4000.0},
    ]
