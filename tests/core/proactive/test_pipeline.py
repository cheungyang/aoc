"""One decision end to end (core/proactive/pipeline.py), with the model stood in for."""
import random
from datetime import datetime

import pytest

from core.proactive.cards import PeerCard
from core.proactive.pipeline import Pipeline, dedupe_key, payload_from
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


def _pipeline(store, run=None, radio=None):
    return Pipeline(store, run=run or FakeRun(), team=lambda: TEAM, radio=radio, owns=lambda agent_id: True,
                    rng=Draw(0), clock=lambda: NOW, tags="- food: Diet.")


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
