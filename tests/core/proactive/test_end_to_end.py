"""The whole shadow path, wired as install() wires it: a tool call to a team radio line.

The receipt is saved (the transcript announces the call; CallLog notes it) → the
turn ends → SignalHook taps it → the service records it and, ledger_append being
a trigger in the effects table, runs a decision at once → the Switchboard (stood
in for) names Ramsay → the Governor lets it through → team radio posts the
line; a ✅ labels it.

With the switch on, the same receipt goes on: the `now` route is approved for
delivery and the schedule runner is woken → its tick claims the delivery → the
runner runs Ramsay's proactive turn (stood in for) in #meal-planning → it posts
there, or stays silent.
"""
import asyncio
import random
import time
from unittest.mock import MagicMock, patch

import pytest

from core.channel.response_parser import AgentResponse
from core.config.settings import Config
from core.proactive import radio as radio_module
from core.proactive import service as service_module
from core.proactive.cards import PeerCard
from core.proactive.delivery import DeliverySchedule, DueDeliveries
from core.proactive.pipeline import Pipeline
from core.proactive.radio import GOOD, TeamRadio
from core.proactive.service import ProactiveService
from core.proactive.switchboard import ModelRun
from core.proactive.taps import CallLog, SignalHook
from core.runtime.foundation import events
from core.runtime.foundation.sessions import SessionManager
from core.runtime.hooks import Turn
from core.runtime.observability.transcript import LoggingHandler
from core.scheduler import schedule_runner as runner_mod
from core.scheduler.schedule_runner import ScheduleRunner
from core.storage.schedule_state import ScheduleState
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


def _receipt_turn():
    """The receipt processor's turn that saved it, as it ends."""
    ctx = SessionManager.get_session(agent_id="receipt-processor", source="tool", channel="receipts")
    ctx.turn_hints.clear()
    _save_receipt(ctx)
    return Turn(agent_id="receipt-processor", agent_config={"channels": ["receipts", "general"]}, ctx=ctx,
                prompt="<caller>main</caller>\nhere's my receipt",
                response=AgentResponse.from_string("Saved: 99 Ranch, $86.40."))


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

    hook.ended(_receipt_turn())
    await asyncio.wait_for(channel.posted.wait(), 2.0)

    assert "here's my receipt" in prompts[0]
    assert channel.posts == ["📻 #1 🧾→🧑‍🍳 now · Groceries from 99 Ranch · would deliver at once"]
    assert await radio.reacted("5001", GOOD)
    route = store.decision(1)["routes"][0]
    assert (route["verdict"], route["label"]) == ("deliver", "good")
    assert store.pending_turns() == []


class MealPlanning:
    """#meal-planning on Discord: what was posted in it."""

    name = "meal-planning"

    def __init__(self):
        self.sent = []

    async def send(self, text, **kwargs):
        self.sent.append(text)


class Ramsay:
    """Ramsay's agent, stood in for: it replies `reply`, and hands the reply to the
    turn's output, as Agent.execute does."""

    def __init__(self, reply):
        self.reply, self.prompts, self.sessions = reply, [], []

    async def execute(self, prompt, session=None, role="user"):
        self.prompts.append(prompt)
        self.sessions.append(session)
        response = AgentResponse.from_string(self.reply)
        session.turn_hints[events.RESPONSE_HINT] = response
        await session.output.deliver(session, response)
        return response.text


def _runner(tmp_path, ramsay, channel):
    """The schedule runner, with the team's configs, Ramsay and #meal-planning stood in for."""
    configs = {"main": {"channels": ["*"], "channel_hosts": ["meal-planning", "receipts"]},
               "meal-planner": {"channels": ["meal-planning", "general"]}}
    loader = MagicMock()
    loader.list_agent_ids.side_effect = lambda: list(configs)
    loader.get_agent_config.side_effect = configs.get
    loader.get_agent.return_value = ramsay
    bots = MagicMock()
    bots.get_channel.return_value = channel
    with patch.object(runner_mod, "AgentsLoader", return_value=loader), \
         patch.object(runner_mod, "BotsLoader", return_value=bots):
        return ScheduleRunner(state=ScheduleState(str(tmp_path / "state.db")))


@pytest.mark.asyncio
@pytest.mark.parametrize("reply, status", [
    ("Saw the 99 Ranch run. Want me to add it all to the kitchen inventory?", "posted"),
    ("<silent/>", "silent"),
])
async def test_with_the_switch_on_the_receipt_becomes_ramsays_post(tmp_path, monkeypatch, call_log, reply, status):
    monkeypatch.setattr(radio_module, "record_outcome", lambda line: None)
    store = SignalStore(str(tmp_path / "memory.db"))

    async def switchboard(prompt):
        return ModelRun(reply=REPLY, took_ms=10, tokens_in=1, tokens_out=1)

    radio_channel, woken = Channel(), asyncio.Event()
    pipeline = Pipeline(store, run=switchboard, team=lambda: TEAM, radio=TeamRadio(radio_channel, store, team=lambda: TEAM),
                        owns=lambda agent_id: True, rng=Draw(0), tags="- food: Diet.",
                        home={"meal-planner": "meal-planning"}.get, wake=woken.set)
    service = ProactiveService(decide=pipeline, store=store, sweep=pipeline.resolve)
    ramsay, meal_planning = Ramsay(reply), MealPlanning()
    runner = _runner(tmp_path, ramsay, meal_planning)

    with Config().overriding(proactive_delivery=True), \
         patch.object(runner_mod, "reset_conversation", return_value=0), \
         patch.object(runner_mod, "record_channel_post") as record_post, \
         patch.object(runner_mod, "record_push") as record_push:
        SignalHook(service.turn_ended).ended(_receipt_turn())
        await asyncio.wait_for(woken.wait(), 2.0)  # a `now` delivery was approved
        (spec,) = await DueDeliveries(store)(time.time())  # the woken tick claims it
        assert await runner.run_schedule(spec)
        await asyncio.wait_for(radio_channel.posted.wait(), 2.0)

    decision = store.decision(1)
    route = decision["routes"][0]
    assert decision["mode"] == "live"
    assert radio_channel.posts == ["📻 #1 🧾→🧑‍🍳 now · Groceries from 99 Ranch · delivering now"]
    assert isinstance(spec, DeliverySchedule) and (spec.agent_id, spec.channel) == ("meal-planner", "meal-planning")
    (prompt,) = ramsay.prompts
    assert "Groceries from 99 Ranch" in prompt and "receipt-processor" not in prompt
    assert ramsay.sessions[0].source == "proactive" and ramsay.sessions[0].get_surface() == "scheduled"
    assert ramsay.sessions[0].thinking_level == "low"  # the spec's, through _run_prompt
    delivery = store.delivery(route["id"])
    assert delivery["status"] == status
    if status == "posted":
        assert meal_planning.sent == [reply]
        assert store.route(route["id"])["delivered_at"] is not None
        record_post.assert_called_once_with(meal_planning, reply, origin="proactive:meal-planner", loader=runner.loader)
        assert record_push.call_args.kwargs["label"] == "a suggestion"
    else:
        assert meal_planning.sent == []
        record_post.assert_not_called()
        record_push.assert_not_called()
