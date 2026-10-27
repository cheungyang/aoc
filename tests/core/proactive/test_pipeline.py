"""One decision end to end (core/proactive/pipeline.py), with the model stood in for."""
import random
from datetime import datetime

import pytest

from core.proactive.cards import PeerCard
from core.proactive.pipeline import Pipeline, dedupe_key, payload_from, source_message
from core.proactive.switchboard import ModelRun, ProposedSignal
from core.storage.signals import SignalStore
from core.util.local_time import get_local_timezone

NOW = datetime(2026, 10, 24, 9, 31, tzinfo=get_local_timezone()).timestamp()
TEAM = {
    "receipt-processor": PeerCard("receipt-processor", "Receipt Processor", "🧾", "receipts", "", (), (), (), True),
    "meal-planner": PeerCard("meal-planner", "Ramsay", "🧑‍🍳", "meal-planning", "", ("groceries bought",),
                             ("update the kitchen inventory",), ("food",), False),
}
REPLY = """<switchboard_response>
  <signal kind="artifact" tags="food">
    <gist>Groceries from 99 Ranch, Oct 24</gist>
    <evidence>Saved: 99 Ranch, $86.40.</evidence>
    <route to="meal-planner" offer="update the kitchen inventory" urgency="now">Add them.</route>
    <route to="stranger" offer="x" urgency="now">?</route>
  </signal>
  <errors>None</errors>
</switchboard_response>"""


def _turn(store, **overrides):
    row = {"at": NOW - 5, "agent_id": "receipt-processor", "channel": "receipts", "session_id": "s1",
           "source": "tool", "surface": "tool", "caller": "main", "tap": "effect", "user_text": "here's my receipt",
           "reply_text": "Saved: 99 Ranch, $86.40.",
           "effects": [{"tool": "ledger_append", "role": "trigger", "args": {"vendor": "99 Ranch", "total": 86.4}}]}
    row.update(overrides)
    store.record_turn(row)
    return store.pending_turns(row["session_id"])


class FakeRun:
    def __init__(self, reply=REPLY, error=""):
        self.reply, self.error, self.prompts = reply, error, []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        return ModelRun(reply=self.reply, took_ms=1800, tokens_in=3200, tokens_out=150, error=self.error)


class Draw(random.Random):
    """Acceptance draws that always clear "worth posting?"."""

    def betavariate(self, alpha, beta):
        return 0.9


class FakeRadio:
    def __init__(self):
        self.decisions = []

    async def decided(self, decision):
        self.decisions.append(decision)


@pytest.fixture
def store(tmp_path):
    return SignalStore(str(tmp_path / "memory.db"))


# Each recipient's home channel, where its deliveries run and post.
HOMES = {"meal-planner": "meal-planning", "excursion-planner": "weekend-planning"}


def _pipeline(store, run=None, radio=None, **options):
    options.setdefault("clock", lambda: NOW)
    return Pipeline(store, run=run or FakeRun(), team=lambda: TEAM, radio=radio, owns=lambda agent_id: True,
                    rng=Draw(0), tags="- food: Diet.", **options)


@pytest.mark.asyncio
async def test_a_receipt_becomes_a_route_to_ramsay(store):
    radio, run = FakeRadio(), FakeRun()
    decision_id = await _pipeline(store, run, radio)("s1", _turn(store))

    decision = store.decision(decision_id)
    assert (decision["tap"], decision["mode"], decision["took_ms"]) == ("effect", "shadow", 1800)
    assert (decision["tokens_in"], decision["tokens_out"], decision["errors"]) == (3200, 150, "")
    assert decision["prompt"] == run.prompts[0] and "here's my receipt" in decision["prompt"]
    (signal,) = decision["signals"]
    assert signal["evidence_found"] == 1 and signal["source_agent"] == "receipt-processor"
    assert signal["payload"] == {"tool": "ledger_append", "vendor": "99 Ranch", "total": 86.4}
    ramsay, stranger = decision["routes"]
    assert (ramsay["verdict"], ramsay["urgency"], ramsay["deliver_at"]) == ("deliver", "now", NOW)
    assert (stranger["verdict"], stranger["reason"]) == ("drop", "stranger is not a teammate with a card")
    assert store.pending_turns("s1") == []
    assert [d["id"] for d in radio.decisions] == [decision_id]


@pytest.mark.asyncio
async def test_the_switch_decides_the_mode(store):
    from core.config.settings import Config
    with Config().overriding(proactive_delivery=True):
        live = await _pipeline(store)("s1", _turn(store))
    shadow = await _pipeline(store)("s2", _turn(store, session_id="s2"))
    assert (store.decision(live)["mode"], store.decision(shadow)["mode"]) == ("live", "shadow")


@pytest.mark.asyncio
async def test_live_a_now_route_is_approved_for_its_home_channel_and_wakes_the_runner(store):
    woken = []
    decision_id = await _pipeline(store, mode="live", home=HOMES.get, wake=lambda: woken.append(True))(
        "s1", _turn(store))
    ramsay, stranger = store.decision(decision_id)["routes"]
    delivery = store.delivery(ramsay["id"])
    assert (delivery["status"], delivery["to_agent"], delivery["channel"]) == ("approved", "meal-planner",
                                                                              "meal-planning")
    assert delivery["deliver_at"] == NOW
    assert store.delivery(stranger["id"]) is None  # dropped: nothing to deliver
    assert woken == [True]


@pytest.mark.asyncio
async def test_shadow_approves_nothing(store):
    woken = []
    decision_id = await _pipeline(store, mode="shadow", home=HOMES.get, wake=lambda: woken.append(True))(
        "s1", _turn(store))
    assert store.decision(decision_id)["mode"] == "shadow"
    assert [store.delivery(r["id"]) for r in store.decision(decision_id)["routes"]] == [None, None]
    assert woken == []


@pytest.mark.asyncio
async def test_live_a_recipient_with_no_home_channel_gets_no_delivery(store, capsys):
    woken = []
    pipeline = _pipeline(store, mode="live", home=lambda agent_id: None, wake=lambda: woken.append(True))
    decision_id = await pipeline("s1", _turn(store))
    assert store.delivery(store.decision(decision_id)["routes"][0]["id"]) is None
    assert woken == []
    assert "meal-planner has no home channel" in capsys.readouterr().out


# -- the `now` reaction -----------------------------------------------------------

class FakeAck:
    """The `now` reaction, stood in for: where it reacted, noted in `events` too.
    `works` is what react returns, or an exception it raises."""

    def __init__(self, events=None, works=True):
        self.calls, self.events, self.works = [], events if events is not None else [], works

    async def react(self, message, emoji, agent_id):
        self.calls.append((message, emoji, agent_id))
        self.events.append("reaction")
        if isinstance(self.works, Exception):
            raise self.works
        return self.works


class Holds:
    """Ownership, stood in for: the claims are known, and this host holds `held`."""

    def __init__(self, held=()):
        self.role, self.held = "prod", set(held)

    def is_ready(self):
        return True

    def is_mine(self, agent_id):
        return agent_id in self.held


@pytest.mark.asyncio
async def test_live_a_now_approval_puts_the_recipients_emoji_on_your_message_at_once(store):
    events = []
    ack = FakeAck(events)

    class Radio:
        async def decided(self, decision):
            events.append("radio")

    _turn(store, at=NOW - 60, context={"message": "111/221"})
    turns = _turn(store, context={"message": "111/222"})  # the receipt processor's latest turn
    decision_id = await _pipeline(store, mode="live", home=HOMES.get, ack=ack, radio=Radio())("s1", turns)

    ramsay = store.decision(decision_id)["routes"][0]
    assert ack.calls == [("111/222", "🧑‍🍳", "meal-planner")]
    assert store.delivery(ramsay["id"])["acked_at"] == NOW
    assert events == ["reaction", "radio"]  # before team radio hears of the decision


def test_the_reaction_goes_on_the_message_that_started_the_source_agents_latest_turn():
    turns = [{"id": 1, "at": 1.0, "agent_id": "receipt-processor", "context": {"message": "1/10"}},
             {"id": 2, "at": 2.0, "agent_id": "receipt-processor", "context": {"message": "1/20"}},
             {"id": 3, "at": 3.0, "agent_id": "receipt-processor", "context": {}},
             {"id": 4, "at": 4.0, "agent_id": "main", "context": {"message": "1/40"}}]
    assert source_message(turns, "receipt-processor") == "1/20"
    assert source_message(turns, "meal-planner") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("why, options, context", [
    ("in shadow", {"mode": "shadow"}, {"message": "111/222"}),
    ("with no message of yours", {}, {}),
    ("for a recipient another host holds: its post may never come", {"ownership": Holds()}, {"message": "111/222"}),
    ("with nothing approved", {"home": lambda agent_id: None}, {"message": "111/222"}),
])
async def test_no_reaction(store, why, options, context):
    ack = FakeAck()
    decision_id = await _pipeline(store, ack=ack, **{"mode": "live", "home": HOMES.get, **options})(
        "s1", _turn(store, context=context))
    assert ack.calls == [], why
    delivery = store.delivery(store.decision(decision_id)["routes"][0]["id"])
    assert delivery is None or delivery["acked_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("works", [False, RuntimeError("discord down")])
async def test_a_reaction_that_fails_leaves_the_decision_whole(store, works):
    radio, ack = FakeRadio(), FakeAck(works=works)
    decision_id = await _pipeline(store, mode="live", home=HOMES.get, ack=ack, radio=radio)(
        "s1", _turn(store, context={"message": "111/222"}))
    decision = store.decision(decision_id)
    assert len(ack.calls) == 1 and decision["errors"] == ""
    delivery = store.delivery(decision["routes"][0]["id"])
    assert (delivery["status"], delivery["acked_at"]) == ("approved", None)  # it's still delivered
    assert [d["id"] for d in radio.decisions] == [decision_id]


def _waiting_route(store, mode, urgency="today", to="excursion-planner"):
    decision_id = store.start_decision(tap="chat", session_id="s", first_turn_at=NOW, mode=mode, at=NOW)
    signal_id = store.add_signal({"decision_id": decision_id, "at": NOW, "kind": "situation", "gist": "In Tahoe"})
    return store.add_route({"signal_id": signal_id, "decision_id": decision_id, "to_agent": to, "asked": urgency,
                            "urgency": urgency, "verdict": "deliver"})


@pytest.mark.asyncio
async def test_resolve_approves_a_today_route_only_when_its_decision_is_live(store):
    live, shadow, nxt = _waiting_route(store, "live"), _waiting_route(store, "shadow"), _waiting_route(
        store, "live", urgency="next")
    # The recipient's scheduled run: the `next` route's touchpoint.
    store.record_turn({"at": NOW + 60, "agent_id": "excursion-planner", "caller": "", "tap": "scheduled"})
    await _pipeline(store, home=HOMES.get, clock=lambda: NOW + 3600).resolve()

    # No turn of yours since: each `today` route's quiet stretch began when it was decided.
    assert (store.route(live)["deliver_at"], store.route(shadow)["deliver_at"]) == (NOW, NOW)
    delivery = store.delivery(live)
    assert (delivery["status"], delivery["channel"], delivery["deliver_at"]) == ("approved", "weekend-planning", NOW)
    assert store.delivery(shadow) is None
    # A `next` route is timed, but rides along on the recipient's next scheduled run instead.
    assert store.route(nxt)["deliver_at"] == NOW + 60 and store.delivery(nxt) is None


@pytest.mark.asyncio
async def test_turns_another_decision_took_are_not_read_twice(store):
    turns = _turn(store)
    store.claim_turns([t["id"] for t in turns], decision_id=999)
    run = FakeRun()
    decision_id = await _pipeline(store, run)("s1", turns)
    assert run.prompts == []
    assert store.decision(decision_id)["errors"] == "another decision read these turns"


@pytest.mark.asyncio
async def test_a_failed_run_is_recorded_with_no_signal(store):
    decision_id = await _pipeline(store, FakeRun(reply="", error="no reply within 90s"))("s1", _turn(store))
    decision = store.decision(decision_id)
    assert decision["errors"] == "no reply within 90s" and decision["signals"] == []


@pytest.mark.asyncio
async def test_a_failure_before_the_run_finishes_the_decision_as_failed(store):
    def unreadable():
        raise RuntimeError("cards unreadable")

    run = FakeRun()
    pipeline = Pipeline(store, run=run, team=unreadable, owns=lambda agent_id: True, rng=Draw(0), clock=lambda: NOW)
    decision_id = await pipeline("s1", _turn(store))
    decision = store.decision(decision_id)
    assert decision["errors"] == "RuntimeError: cards unreadable" and decision["signals"] == []
    assert (decision["took_ms"], decision["tokens_in"], decision["tokens_out"]) == (None, None, None)
    assert run.prompts == [] and store.pending_turns("s1") == []  # read once: a failing step can't loop


@pytest.mark.asyncio
async def test_a_failure_after_the_run_keeps_what_it_cost(store, monkeypatch):
    def full(signal):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "add_signal", full)
    decision_id = await _pipeline(store)("s1", _turn(store))
    decision = store.decision(decision_id)
    assert decision["errors"] == "RuntimeError: disk full" and decision["reply"] == REPLY
    assert (decision["took_ms"], decision["tokens_in"], decision["tokens_out"]) == (1800, 3200, 150)


@pytest.mark.asyncio
async def test_a_radio_failure_doesnt_lose_the_decision(store, capsys):
    class Broken:
        async def decided(self, decision):
            raise RuntimeError("discord down")

    decision_id = await _pipeline(store, radio=Broken())("s1", _turn(store))
    assert store.decision(decision_id)["routes"]
    assert "Team radio missed decision" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_board_fact_is_shown_to_the_next_run(store):
    await _pipeline(store)("s1", _turn(store))
    run = FakeRun(reply="<switchboard_response><errors>None</errors></switchboard_response>")
    await _pipeline(store, run)("s2", _turn(store, session_id="s2", user_text="anything else?"))
    assert "- artifact: Groceries from 99 Ranch, Oct 24 → meal-planner now" in run.prompts[0]


def _signal(gist="Groceries from 99 Ranch", payload=None, kind="artifact"):
    return ProposedSignal(kind=kind, gist=gist, evidence="", tags=[], until=None, sensitivity="normal",
                          payload=payload or {})


def test_the_same_fact_has_the_same_key_however_it_is_worded():
    assert dedupe_key(_signal("Groceries from 99 Ranch")) == dedupe_key(_signal("groceries FROM 99 ranch"))
    assert dedupe_key(_signal("In Tahoe", kind="situation")) != dedupe_key(_signal("In Tahoe"))
    by_payload = dedupe_key(_signal("A", payload={"vendor": "99 Ranch", "date": "2026-10-24"}))
    assert by_payload == dedupe_key(_signal("B", payload={"vendor": "99 ranch", "date": "2026-10-24"}))


def test_an_artifact_payload_comes_from_its_one_trigger_effect():
    turns = [{"effects": [{"tool": "ledger_append", "role": "trigger", "args": {"vendor": "99 Ranch"}},
                          {"tool": "graph_call", "role": "context", "args": {}}]}]
    assert payload_from(turns, _signal()) == {"tool": "ledger_append", "vendor": "99 Ranch"}
    assert payload_from(turns, _signal(kind="situation")) == {}
    assert payload_from(turns + turns, _signal()) == {}  # two triggers: which one is ambiguous
