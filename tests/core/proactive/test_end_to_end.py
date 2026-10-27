"""The whole shadow path, wired as install() wires it: a tool call to a team radio line.

The receipt is saved (the transcript announces the call; CallLog notes it) → the
turn ends → SignalHook taps it → the service records it and, ledger_append being
a trigger in the effects table, runs a decision at once → the Switchboard (stood
in for) names Ramsay → the Governor lets it through → team radio posts the
line; a ✅ labels it.
"""
import asyncio
import random

import pytest

from core.channel.response_parser import AgentResponse
from core.proactive import radio as radio_module
from core.proactive import service as service_module
from core.proactive.cards import PeerCard
from core.proactive.pipeline import Pipeline
from core.proactive.radio import GOOD, TeamRadio
from core.proactive.service import ProactiveService
from core.proactive.switchboard import ModelRun
from core.proactive.taps import CallLog, SignalHook
from core.runtime.foundation.sessions import SessionManager
from core.runtime.hooks import Turn
from core.runtime.observability.transcript import LoggingHandler
from core.storage.signals import SignalStore
from core.tooling.hooks import set_tool_hooks, tool_hooks

TEAM = {
    "receipt-processor": PeerCard("receipt-processor", "Receipt Processor", "🧾", "receipts", "", (), (), (), True),
    "meal-planner": PeerCard("meal-planner", "Ramsay", "🧑‍🍳", "meal-planning", "", ("groceries bought",),
                             ("update the kitchen inventory",), ("food",), False),
}
REPLY = """<switchboard_response>
  <signal kind="artifact" tags="food">
    <gist>Groceries from 99 Ranch</gist>
    <evidence>Saved: 99 Ranch, $86.40.</evidence>
    <route to="meal-planner" offer="update the kitchen inventory" urgency="now">Add them.</route>
  </signal>
  <errors>None</errors>
</switchboard_response>"""


class Draw(random.Random):
    def betavariate(self, alpha, beta):
        return 0.9


class Channel:
    def __init__(self):
        self.posts, self.posted = [], asyncio.Event()

    async def post(self, text, reactions=()):
        self.posts.append(text)
        self.posted.set()
        return "5001"

    async def poll(self, reply_to, question, answers):
        return None


@pytest.fixture
def call_log():
    saved = tool_hooks()
    set_tool_hooks([CallLog()])
    yield
    set_tool_hooks(saved)


def _save_receipt(ctx):
    """The tool call as the runtime reports it: LangChain's callbacks, to the transcript handler."""
    handler, mine = LoggingHandler(session=ctx), {"metadata": {"agent_id": "receipt-processor"}}
    handler.on_tool_start({"name": "ledger_append"}, "", run_id="r1", inputs={"vendor": "99 Ranch", "total": 86.4},
                          **mine)
    handler.on_tool_end("<status>ok</status>Saved.<errors>None</errors>", run_id="r1", **mine)


@pytest.mark.asyncio
async def test_a_saved_receipt_reaches_team_radio_and_takes_a_label(tmp_path, monkeypatch, call_log):
    monkeypatch.setattr(radio_module, "record_outcome", lambda line: None)
    store = SignalStore(str(tmp_path / "memory.db"))
    prompts = []

    async def switchboard(prompt):
        prompts.append(prompt)
        return ModelRun(reply=REPLY, took_ms=10, tokens_in=1, tokens_out=1)

    channel = Channel()
    radio = TeamRadio(channel, store, team=lambda: TEAM)
    pipeline = Pipeline(store, run=switchboard, team=lambda: TEAM, radio=radio, owns=lambda agent_id: True,
                        rng=Draw(0), tags="- food: Diet.")
    service = ProactiveService(decide=pipeline, store=store, sweep=pipeline.resolve)
    hook = SignalHook(service.turn_ended)

    ctx = SessionManager.get_session(agent_id="receipt-processor", source="tool", channel="receipts")
    ctx.turn_hints.clear()
    _save_receipt(ctx)
    turn = Turn(agent_id="receipt-processor", agent_config={"channels": ["receipts", "general"]}, ctx=ctx,
                prompt="<caller>main</caller>\nhere's my receipt",
                response=AgentResponse.from_string("Saved: 99 Ranch, $86.40."))

    hook.ended(turn)
    await asyncio.wait_for(channel.posted.wait(), 2.0)

    assert "here's my receipt" in prompts[0]
    assert channel.posts == ["📻 #1 🧾→🧑‍🍳 now · Groceries from 99 Ranch · would deliver at once"]
    assert await radio.reacted("5001", GOOD)
    route = store.decision(1)["routes"][0]
    assert (route["verdict"], route["label"]) == ("deliver", "good")
    assert store.pending_turns() == []
