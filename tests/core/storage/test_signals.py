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


# -- Live Context ------------------------------------------------------------------

def _situation(store, gist, *, at=1000.0, until="2026-10-27", tags="travel", **overrides):
    values = dict(kind="situation", gist=gist, at=at, until=until, tags=tags, dedupe_key=f"situation:{gist}")
    values.update(overrides)
    return _signal(store, _decision(store), **values)


def _live(store, topics, since=100.0, **options):
    return [s["gist"] for s in store.live_situations(topics, today="2026-10-24", since=since, **options)]


def test_live_situations_are_the_ones_an_agents_topics_cover_newest_first(store):
    _situation(store, "In Tahoe", at=1000.0, tags="travel,family")
    _situation(store, "Guests on Saturday", at=2000.0, tags="family")
    _situation(store, "Kitchen remodel", at=3000.0, tags="home")
    assert _live(store, ["family", "food"]) == ["Guests on Saturday", "In Tahoe"]
    assert _live(store, []) == []


def test_live_situations_skip_what_isnt_live(store):
    _situation(store, "True through today", until="2026-10-24")
    _situation(store, "Over yesterday", until="2026-10-23")
    _situation(store, "No until", until=None)
    _situation(store, "Evidence not found", evidence_found=0)
    _situation(store, "Learned too long ago", at=50.0)
    _situation(store, "An artifact", kind="artifact")
    assert _live(store, ["travel"]) == ["True through today"]


def test_live_situations_stop_at_the_limit(store):
    for i in range(10):
        _situation(store, f"Fact {i}", at=1000.0 + i)
    assert _live(store, ["travel"], limit=8) == [f"Fact {i}" for i in range(9, 1, -1)]


def test_a_health_finance_or_sensitive_fact_shows_only_to_its_readers(store):
    _situation(store, "Flu until Friday", tags="health,family", at=1000.0)
    _situation(store, "A tight month", tags="family", sensitivity="sensitive", at=2000.0)
    _situation(store, "Guests on Saturday", tags="family", at=3000.0)
    assert _live(store, ["family"]) == ["Guests on Saturday"]
    assert _live(store, ["family", "health"]) == ["Guests on Saturday", "A tight month", "Flu until Friday"]


def test_a_fact_emitted_twice_shows_once_in_its_newest_wording(store):
    _situation(store, "In Tahoe with the family", at=1000.0, dedupe_key="situation:tahoe")
    _situation(store, "The family is up in Tahoe", at=2000.0, dedupe_key="situation:tahoe")
    assert _live(store, ["travel"]) == ["The family is up in Tahoe"]


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


def test_a_route_knows_its_decisions_mode(store):
    decision_id = _decision(store, mode="live")
    assert store.route(_route(store, _signal(store, decision_id), decision_id))["mode"] == "live"


# -- deliveries (live mode) ------------------------------------------------------

def _approved(store, deliver_at=200.0, **route):
    decision_id = _decision(store, mode="live")
    route_id = _route(store, _signal(store, decision_id), decision_id, **route)
    store.approve_delivery(route_id, to_agent="meal-planner", channel="meal-planning", deliver_at=deliver_at, at=110.0)
    return route_id


def test_a_delivery_is_approved_once(store):
    decision_id = _decision(store, mode="live")
    route_id = _route(store, _signal(store, decision_id), decision_id)
    assert store.approve_delivery(route_id, to_agent="meal-planner", channel="meal-planning", deliver_at=200.0,
                                  at=110.0)
    # A second approval changes nothing: not the channel, not the time.
    assert not store.approve_delivery(route_id, to_agent="meal-planner", channel="general", deliver_at=999.0)
    delivery = store.delivery(route_id)
    assert (delivery["status"], delivery["channel"], delivery["deliver_at"]) == ("approved", "meal-planning", 200.0)
    assert delivery["approved_at"] == 110.0


def test_a_due_delivery_comes_with_what_its_turn_needs(store):
    route_id = _approved(store, note="Add them.", window_start="2026-10-24", window_end="2026-10-25")
    (due,) = store.due_deliveries(now=200.0)
    assert (due["route_id"], due["to_agent"], due["channel"]) == (route_id, "meal-planner", "meal-planning")
    assert (due["offer"], due["note"], due["urgency"]) == ("update kitchen inventory", "Add them.", "now")
    assert (due["window_start"], due["window_end"], due["until"]) == ("2026-10-24", "2026-10-25", None)
    assert (due["kind"], due["gist"], due["evidence"], due["tags"]) == ("artifact", "Groceries from Costco", "Costco",
                                                                      "food")
    assert due["payload"] == {"vendor": "Costco"}
    assert (due["source_agent"], due["source_channel"]) == ("receipt-processor", "receipts")


def test_due_skips_deliveries_not_due_yet_and_taken_ones(store):
    due, later, taken = _approved(store, deliver_at=200.0), _approved(store, deliver_at=500.0), _approved(store)
    assert store.claim_delivery(taken, now=200.0)
    assert [d["route_id"] for d in store.due_deliveries(now=300.0)] == [due]
    assert [d["route_id"] for d in store.due_deliveries(now=500.0)] == [due, later]


def test_a_delivery_is_claimed_once(store):
    route_id = _approved(store, deliver_at=200.0)
    assert not store.claim_delivery(route_id, now=199.0)  # not due yet
    assert store.claim_delivery(route_id, now=200.0)
    assert not store.claim_delivery(route_id, now=201.0)  # another tick gets nothing
    delivery = store.delivery(route_id)
    assert (delivery["status"], delivery["claimed_at"]) == ("running", 200.0)


def test_a_stuck_delivery_is_failed_not_retried(store):
    stuck, recent, waiting = _approved(store), _approved(store), _approved(store, deliver_at=900.0)
    store.claim_delivery(stuck, now=200.0)
    store.claim_delivery(recent, now=700.0)
    assert store.fail_stuck(now=900.0, older_than_s=600.0) == [stuck]
    assert store.fail_stuck(now=900.0, older_than_s=600.0) == []
    failed = store.delivery(stuck)
    assert (failed["status"], failed["error"], failed["finished_at"]) == ("failed", "stuck; not retried", 900.0)
    assert (store.delivery(recent)["status"], store.delivery(waiting)["status"]) == ("running", "approved")
    assert not store.claim_delivery(stuck, now=1000.0)


def test_an_approved_delivery_due_too_long_ago_expires_unrun(store):
    old, fresh, running = _approved(store, deliver_at=100.0), _approved(store, deliver_at=500.0), _approved(store)
    store.claim_delivery(running, now=200.0)
    assert store.expire_deliveries(now=1000.0, older_than_s=600.0) == 1  # due before 400
    expired = store.delivery(old)
    assert (expired["status"], expired["error"], expired["finished_at"]) == ("failed", "expired; not posted late",
                                                                            1000.0)
    # A fresh row waits; a run in flight is fail_stuck's to judge.
    assert (store.delivery(fresh)["status"], store.delivery(running)["status"]) == ("approved", "running")
    assert store.expire_deliveries(now=1000.0, older_than_s=600.0) == 0
    assert not store.claim_delivery(old, now=1000.0)


def test_a_posted_delivery_marks_its_route_delivered(store):
    posted, silent, failed = _approved(store), _approved(store), _approved(store)
    store.finish_delivery(posted, "posted", at=250.0)
    store.finish_delivery(silent, "silent", at=260.0)
    store.finish_delivery(failed, "failed", error="the turn failed", at=270.0)
    assert (store.delivery(posted)["status"], store.route(posted)["delivered_at"]) == ("posted", 250.0)
    assert (store.delivery(silent)["status"], store.route(silent)["delivered_at"]) == ("silent", None)
    assert (store.delivery(failed)["error"], store.route(failed)["delivered_at"]) == ("the turn failed", None)
    with pytest.raises(ValueError):
        store.finish_delivery(posted, "running")
