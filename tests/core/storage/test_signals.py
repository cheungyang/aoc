"""The proactive Board (core/storage/signals.py)."""
import sqlite3

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


def test_the_now_reaction_is_recorded_once(store):
    route_id = _approved(store)
    assert store.delivery(route_id)["acked_at"] is None
    store.ack_delivery(route_id, at=111.0)
    store.ack_delivery(route_id, at=150.0)  # the first one counts
    assert store.delivery(route_id)["acked_at"] == 111.0


def test_an_old_deliveries_table_gets_acked_at(tmp_path):
    """A dev database may hold the deliveries table as S4's first part made it."""
    path = str(tmp_path / "memory.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE deliveries (route_id INTEGER PRIMARY KEY, to_agent TEXT NOT NULL, "
                 "channel TEXT NOT NULL DEFAULT '', deliver_at REAL NOT NULL, status TEXT NOT NULL, "
                 "approved_at REAL, claimed_at REAL, finished_at REAL, error TEXT NOT NULL DEFAULT '')")
    conn.execute("INSERT INTO deliveries (route_id, to_agent, deliver_at, status) VALUES (7, 'meal-planner', 1, 'posted')")
    conn.commit()
    conn.close()

    store = SignalStore(path)
    assert store.delivery(7)["acked_at"] is None  # the row is kept, and the column added
    store.ack_delivery(7, at=2.0)
    assert SignalStore(path).delivery(7)["acked_at"] == 2.0  # the next connection finds it there


# -- outcomes of live posts ------------------------------------------------------

def test_a_post_keeps_its_message_ids_and_any_of_them_finds_it(store):
    route_id, waiting = _approved(store), _approved(store)
    store.finish_delivery(route_id, "posted", at=250.0, message_ids=[7001, "7002"])
    assert store.delivery(route_id)["message_ids"] == ["7001", "7002"]
    assert store.delivery(waiting)["message_ids"] == []
    post = store.post_by_message("7002")
    assert (post["route_id"], post["to_agent"], post["status"], post["finished_at"], post["outcome"]) == (
        route_id, "meal-planner", "posted", 250.0, None)
    assert post["decision_id"] == store.route(route_id)["decision_id"]
    assert store.post_by_message(7001)["route_id"] == route_id
    assert store.post_by_message("700") is None and store.post_by_message("70012") is None  # whole ids only


def test_unsettled_posts_are_the_proactive_posts_with_no_outcome_yet(store):
    later, sooner, today = _approved(store), _approved(store), _approved(store, urgency="today")
    silent, failed, settled, waiting = _approved(store), _approved(store), _approved(store), _approved(store)
    store.finish_delivery(later, "posted", at=300.0)
    store.finish_delivery(sooner, "posted", at=250.0)
    store.finish_delivery(today, "posted", at=260.0)
    store.finish_delivery(silent, "silent", at=250.0)
    store.finish_delivery(failed, "failed", at=250.0)
    store.finish_delivery(settled, "posted", at=250.0)
    store.set_outcome(settled, "engaged", at=255.0)
    handed = _next_route(store)  # its scheduled run posted: that says nothing about the item
    store.hand_over([handed], to_agent="meal-planner", channel="meal-planning", now=200.0)
    store.finish_delivery(handed, "posted", at=250.0)
    assert [(p["route_id"], p["finished_at"]) for p in store.unsettled_posts()] == [
        (sooner, 250.0), (today, 260.0), (later, 300.0)]


def test_your_first_turn_with_an_agent_in_a_stretch(store):
    store.record_turn(_turn(at=100.0, agent_id="meal-planner"))                    # before it
    store.record_turn(_turn(at=110.0, agent_id="day-planner"))                     # another agent
    store.record_turn(_turn(at=120.0, agent_id="meal-planner", caller=""))         # an agent asked
    store.record_turn(_turn(at=130.0, agent_id="meal-planner", tap="scheduled"))   # a schedule
    first = store.record_turn(_turn(at=140.0, agent_id="meal-planner", user_text="nope"))
    store.record_turn(_turn(at=150.0, agent_id="meal-planner"))
    turn = store.first_user_turn("meal-planner", after=100.0, until=200.0)
    assert (turn["id"], turn["user_text"], turn["effects"][0]["tool"]) == (first, "nope", "ledger_append")
    assert store.first_user_turn("meal-planner", after=100.0, until=139.0) is None
    assert store.first_user_turn("meal-planner", after=150.0, until=200.0) is None


def test_an_outcome_is_set_once_and_counted_once(store):
    route_id = _approved(store)
    assert store.set_outcome(route_id, "engaged", at=300.0)
    assert not store.set_outcome(route_id, "engaged", at=310.0)   # a repeat changes nothing
    assert not store.set_outcome(route_id, "ignored", at=320.0)   # the first sticks
    assert (store.route(route_id)["outcome"], store.route(route_id)["outcome_at"]) == ("engaged", 300.0)
    assert store.acceptance("meal-planner/artifact/food") == {"engaged": 1, "ignored": 0, "declined": 0}

    assert store.set_outcome(route_id, "declined", at=400.0, replaces=("engaged",))   # ❌ within the day
    assert not store.set_outcome(route_id, "declined", at=410.0, replaces=("engaged",))
    assert (store.route(route_id)["outcome"], store.route(route_id)["outcome_at"]) == ("declined", 400.0)
    assert store.acceptance("meal-planner/artifact/food") == {"engaged": 0, "ignored": 0, "declined": 1}
    for wrong in ({"outcome": "missed"}, {"outcome": "declined", "replaces": ("good",)}):
        with pytest.raises(ValueError):
            store.set_outcome(route_id, **wrong)


def test_a_label_wins_over_an_outcome_and_takes_its_count(store):
    outcome_first, label_first = _approved(store), _approved(store, kind_key="excursion-planner/situation/travel")
    store.set_outcome(outcome_first, "engaged", at=300.0)
    store.label_routes([outcome_first], "bad")
    assert store.acceptance("meal-planner/artifact/food") == {"engaged": 0, "ignored": 0, "declined": 1}
    store.label_routes([outcome_first], "good")  # and a relabel moves it again
    assert store.acceptance("meal-planner/artifact/food") == {"engaged": 1, "ignored": 0, "declined": 0}

    store.label_routes([label_first], "good")
    assert store.set_outcome(label_first, "declined", at=300.0)  # kept on the route, not counted
    assert store.route(label_first)["outcome"] == "declined"
    assert store.acceptance("excursion-planner/situation/travel") == {"engaged": 1, "ignored": 0, "declined": 0}


def test_older_tables_get_message_ids_and_outcome_at(tmp_path):
    """A dev database may hold the routes and deliveries tables as S4's third part left them."""
    path = str(tmp_path / "memory.db")
    route_id = _approved(SignalStore(path))
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE deliveries DROP COLUMN message_ids")
    conn.execute("ALTER TABLE routes DROP COLUMN outcome_at")
    conn.commit()
    conn.close()

    store = SignalStore(path)
    assert store.delivery(route_id)["message_ids"] == []  # the row is kept, and the columns added
    store.finish_delivery(route_id, "posted", at=250.0, message_ids=["7001"])
    assert store.set_outcome(route_id, "engaged", at=300.0)
    assert SignalStore(path).post_by_message("7001")["outcome"] == "engaged"
    assert store.route(route_id)["outcome_at"] == 300.0


def test_stats_have_the_outcomes_and_how_fast_now_routes_came(store):
    shadow = _decision(store, at=50.0)  # before the first live decision
    _route(store, _signal(store, shadow), shadow, verdict="absorb")
    reacted, quiet = _approved(store), _approved(store, to_agent="excursion-planner")
    for route_id in (reacted, quiet):
        # The source turn the decision read ended at 100.
        store.claim_turns([store.record_turn(_turn(at=100.0))], store.route(route_id)["decision_id"])
    store.ack_delivery(reacted, at=103.0)
    store.finish_delivery(reacted, "posted", at=141.0)
    store.set_outcome(reacted, "engaged")
    store.finish_delivery(quiet, "silent", at=130.0)
    store.set_outcome(_approved(store, urgency="today"), "ignored")  # a `today` post: no timing
    stats = store.stats(since=0.0)
    assert stats["outcomes"] == {"meal-planner": {"engaged": 1, "ignored": 1}}
    assert stats["first_live_at"] == 110.0
    assert (stats["acks"], stats["posts"]) == ([3.0], [41.0])


# -- the next touchpoint (live `next` routes) ---------------------------------------

TODAY = "2026-10-24"


def _next_route(store, mode="live", at=110.0, until=None, window_end=None, **route):
    decision_id = _decision(store, mode=mode)
    signal_id = _signal(store, decision_id, at=at, until=until)
    values = {"asked": "next", "urgency": "next", "window_end": window_end, **route}
    return _route(store, signal_id, decision_id, **values)


def test_pending_next_is_the_live_next_routes_no_run_has_taken(store):
    newer, older = _next_route(store, at=120.0), _next_route(store, until=TODAY, window_end=TODAY)
    _next_route(store, mode="shadow")                  # never delivered
    _next_route(store, verdict="absorb")               # nothing to deliver
    _next_route(store, urgency="today")                # a proactive turn's, not a run's
    _next_route(store, to_agent="day-planner")         # another agent's
    _next_route(store, at=50.0)                        # emitted before `since`
    _next_route(store, until="2026-10-23")             # no longer true
    _next_route(store, window_end="2026-10-23")        # its window has closed
    taken = _next_route(store)
    store.hand_over([taken], to_agent="meal-planner", channel="meal-planning", now=200.0)

    pending = store.pending_next("meal-planner", since=100.0, today=TODAY, limit=5)
    assert [r["route_id"] for r in pending] == [older, newer]  # oldest first
    assert (pending[0]["gist"], pending[0]["payload"], pending[0]["offer"]) == (
        "Groceries from Costco", {"vendor": "Costco"}, "update kitchen inventory")
    assert [r["route_id"] for r in store.pending_next("meal-planner", since=100.0, today=TODAY, limit=1)] == [older]


def test_a_route_is_handed_over_once(store):
    first, second = _next_route(store), _next_route(store)
    assert store.hand_over([first], to_agent="meal-planner", channel="meal-planning", now=200.0) == [first]
    # A second run asking for both gets only the one left.
    assert store.hand_over([first, second], to_agent="meal-planner", channel="meal-planning", now=210.0) == [second]
    handed = store.delivery(first)
    assert (handed["status"], handed["channel"], handed["deliver_at"], handed["claimed_at"]) == (
        "handed", "meal-planning", 200.0, 200.0)
    assert store.pending_next("meal-planner", since=100.0, today=TODAY, limit=5) == []


def test_a_handed_row_is_left_to_its_run(store):
    route_id = _next_route(store)
    store.hand_over([route_id], to_agent="meal-planner", channel="meal-planning", now=200.0)
    # However long the run takes: never stuck, never expired, never claimed for a proactive turn.
    assert store.fail_stuck(now=99_999.0, older_than_s=600.0) == []
    assert store.expire_deliveries(now=99_999.0, older_than_s=600.0) == 0
    assert store.due_deliveries(now=99_999.0) == [] and not store.claim_delivery(route_id, now=99_999.0)
    assert store.delivery(route_id)["status"] == "handed"
    store.finish_delivery(route_id, "posted", at=300.0)  # its run posted
    assert (store.delivery(route_id)["status"], store.route(route_id)["delivered_at"]) == ("posted", 300.0)
