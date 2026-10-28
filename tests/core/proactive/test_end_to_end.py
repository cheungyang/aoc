"""The whole shadow path, wired as install() wires it: a tool call to a team radio line.

The receipt is saved (the transcript announces the call; CallLog notes it) → the
turn ends → SignalHook taps it → the service records it and, ledger_append being
a trigger in the effects table, runs a decision at once → the Switchboard (stood
in for) names Ramsay → the Governor lets it through → team radio posts the
line; a ✅ labels it.

With the switch on, the same receipt goes on: the `now` route is approved for
delivery, your receipt message gets Ramsay's emoji, and the schedule runner is
woken → its tick claims the delivery → the runner runs Ramsay's proactive turn
(stood in for) in #meal-planning → it posts there, or stays silent → your ✅
on the post is its outcome. A `next` route instead rides along on Ramsay's
next scheduled run, in its prompt, and gets no outcome.

A follow-up of Ramsay's runs the same way: the tick claims its due check → the
runner runs his check in #meal-planning → it posts or stays silent, and the
store counts it. A scan check that can't read posts the problem instead.
"""
import asyncio
import random
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.channel.discord.output import DiscordOutput
from core.channel.response_parser import AgentResponse
from core.config.settings import Config
from core.proactive import follow_ups as follow_ups_module
from core.proactive import radio as radio_module
from core.proactive import service as service_module
from core.proactive.cards import PeerCard
from core.proactive.delivery import DeliverySchedule, DueDeliveries, SignalsForMe
from core.proactive.follow_ups import DueFollowUps, FollowUpSchedule
from core.proactive.outcomes import Outcomes
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
from core.scheduler.spec import Problems, PromptSchedule, WorkDecision
from core.storage.follow_ups import Check, FollowUpStore
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
# Your receipt message in #receipts, as the Discord runner's output names it.
RECEIPT_MESSAGE = "111/222"


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


class Ack:
    """The `now` reaction's Discord side (core/channel/discord/ack.py), stood in for."""

    def __init__(self):
        self.reactions = []

    async def react(self, message, emoji, agent_id):
        self.reactions.append((message, emoji, agent_id))
        return True


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


def _receipt_turn(output=None):
    """The receipt processor's turn that saved it, as it ends. With `output`, the turn
    answers your message in #receipts, as one the Discord runner started does."""
    ctx = SessionManager.get_session(agent_id="receipt-processor", source="tool", channel="receipts", output=output)
    ctx.turn_hints.clear()
    _save_receipt(ctx)
    return Turn(agent_id="receipt-processor", agent_config={"channels": ["receipts", "general"]}, ctx=ctx,
                prompt="<caller>main</caller>\nhere's my receipt",
                response=AgentResponse.from_string("Saved: 99 Ranch, $86.40."))


def _from_your_message():
    return DiscordOutput(SimpleNamespace(id=111), message_id=222)


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
    """#meal-planning on Discord: what was posted in it, as message 9001 and on."""

    name = "meal-planning"

    def __init__(self):
        self.sent = []

    async def send(self, text, **kwargs):
        self.sent.append(text)
        return SimpleNamespace(id=9000 + len(self.sent))


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

    radio_channel, woken, ack = Channel(), asyncio.Event(), Ack()
    pipeline = Pipeline(store, run=switchboard, team=lambda: TEAM, radio=TeamRadio(radio_channel, store, team=lambda: TEAM),
                        owns=lambda agent_id: True, rng=Draw(0), tags="- food: Diet.",
                        home={"meal-planner": "meal-planning"}.get, wake=woken.set, ack=ack)
    service = ProactiveService(decide=pipeline, store=store, sweep=pipeline.resolve)
    ramsay, meal_planning = Ramsay(reply), MealPlanning()
    runner = _runner(tmp_path, ramsay, meal_planning)

    with Config().overriding(proactive_delivery=True), \
         patch.object(runner_mod, "reset_conversation", return_value=0), \
         patch.object(runner_mod, "record_channel_post") as record_post, \
         patch.object(runner_mod, "record_push") as record_push:
        SignalHook(service.turn_ended).ended(_receipt_turn(_from_your_message()))
        await asyncio.wait_for(woken.wait(), 2.0)  # a `now` delivery was approved
        (spec,) = await DueDeliveries(store)(time.time())  # the woken tick claims it
        assert await runner.run_schedule(spec)
        await asyncio.wait_for(radio_channel.posted.wait(), 2.0)

    decision = store.decision(1)
    route = decision["routes"][0]
    assert decision["mode"] == "live"
    assert radio_channel.posts == ["📻 #1 🧾→🧑‍🍳 now · Groceries from 99 Ranch · delivering now"]
    # Your receipt got Ramsay's emoji at once, and keeps it if he then stays silent: he did take note.
    assert ack.reactions == [(RECEIPT_MESSAGE, "🧑‍🍳", "meal-planner")]
    assert isinstance(spec, DeliverySchedule) and (spec.agent_id, spec.channel) == ("meal-planner", "meal-planning")
    (prompt,) = ramsay.prompts
    assert "Groceries from 99 Ranch" in prompt and "receipt-processor" not in prompt
    assert ramsay.sessions[0].source == "proactive" and ramsay.sessions[0].get_surface() == "scheduled"
    assert ramsay.sessions[0].thinking_level == "low"  # the spec's, through _run_prompt
    delivery = store.delivery(route["id"])
    assert delivery["status"] == status and delivery["acked_at"] is not None
    if status == "posted":
        assert meal_planning.sent == [reply]
        assert store.route(route["id"])["delivered_at"] is not None
        record_post.assert_called_once_with(meal_planning, reply, origin="proactive:meal-planner", loader=runner.loader)
        assert record_push.call_args.kwargs["label"] == "a suggestion"
        # Your ✅ on his post is its outcome, and the runner still goes on to its inbox row.
        assert delivery["message_ids"] == ["9001"]
        assert not await Outcomes(store, record=lambda line: None).reacted("9001", GOOD)
        assert store.route(route["id"])["outcome"] == "engaged"
    else:
        assert meal_planning.sent == [] and delivery["message_ids"] == []
        record_post.assert_not_called()
        record_push.assert_not_called()


@pytest.mark.asyncio
async def test_with_the_switch_on_a_next_route_rides_along_on_ramsays_scheduled_run(tmp_path, monkeypatch, call_log):
    monkeypatch.setattr(radio_module, "record_outcome", lambda line: None)
    store = SignalStore(str(tmp_path / "memory.db"))

    async def switchboard(prompt):
        return ModelRun(reply=REPLY.replace('urgency="now"', 'urgency="next"'), took_ms=10, tokens_in=1, tokens_out=1)

    radio_channel, ack = Channel(), Ack()
    pipeline = Pipeline(store, run=switchboard, team=lambda: TEAM, radio=TeamRadio(radio_channel, store, team=lambda: TEAM),
                        owns=lambda agent_id: True, rng=Draw(0), tags="- food: Diet.",
                        home={"meal-planner": "meal-planning"}.get, ack=ack)
    service = ProactiveService(decide=pipeline, store=store, sweep=pipeline.resolve)
    post = "Tonight: the 99 Ranch tofu goes in the freezer."
    ramsay, meal_planning = Ramsay(post), MealPlanning()
    runner = _runner(tmp_path, ramsay, meal_planning)
    runner.add_briefing(SignalsForMe(store))
    evening = PromptSchedule(prompt="Plan tomorrow's meals.", schedule_id="meal-planner:evening",
                             agent_id="meal-planner", cron="0 21 * * *", channel="meal-planning")

    with Config().overriding(proactive_delivery=True), \
         patch.object(runner_mod, "reset_conversation", return_value=0), \
         patch.object(runner_mod, "record_channel_post"), patch.object(runner_mod, "record_push"):
        SignalHook(service.turn_ended).ended(_receipt_turn(_from_your_message()))
        await asyncio.wait_for(radio_channel.posted.wait(), 2.0)  # decided
        assert await DueDeliveries(store)(time.time()) == []  # it isn't a proactive turn
        assert await runner.run_schedule(evening)

    route = store.decision(1)["routes"][0]
    assert (route["verdict"], route["urgency"]) == ("deliver", "next")
    assert ack.reactions == []  # only a `now` route reacts
    (prompt,) = ramsay.prompts
    assert prompt.startswith("Plan tomorrow's meals.\n\n<findings source=\"your team\">")
    assert "Groceries from 99 Ranch" in prompt and "receipt-processor" not in prompt and "ledger_append" not in prompt
    assert ramsay.sessions[0].source == "scheduled" and meal_planning.sent == [post]
    assert store.delivery(route["id"])["status"] == "posted"
    assert store.unsettled_posts() == []  # the run's post says nothing about the item: no outcome


def _ramsays_follow_up(tmp_path, monkeypatch, check):
    """Ramsay's follow-up, due now: his home channel is #meal-planning."""
    monkeypatch.setattr(follow_ups_module, "home_channel", lambda agent_id, loader: "meal-planning")
    store = FollowUpStore(str(tmp_path / "memory.db"))
    follow_up = store.open(agent_id="meal-planner", goal="Wagyu on sale at 99 Ranch, for the anniversary dinner",
                           check=check, every_s=12 * 3600, until="2099-12-31", channel="meal-planning",
                           at=time.time() - 12 * 3600 - 60)
    return store, follow_up


@pytest.mark.asyncio
@pytest.mark.parametrize("reply, finds", [
    ("99 Ranch has A5 Wagyu at $89/lb through Sunday. Want me to plan the dinner around it?", 1),
    ("<silent/>", 0),
])
async def test_with_the_switch_on_a_follow_ups_check_runs_as_its_agents_turn(tmp_path, monkeypatch, reply, finds):
    store, follow_up = _ramsays_follow_up(tmp_path, monkeypatch, Check(type="model", what="Wagyu sales at 99 Ranch"))
    ramsay, meal_planning, briefed = Ramsay(reply), MealPlanning(), []
    runner = _runner(tmp_path, ramsay, meal_planning)
    runner.add_briefing(lambda spec, ctx: briefed.append(spec))

    with Config().overriding(proactive_delivery=True), \
         patch.object(runner_mod, "reset_conversation", return_value=0), \
         patch.object(runner_mod, "record_channel_post") as record_post, \
         patch.object(runner_mod, "record_push") as record_push:
        (spec,) = await DueFollowUps(store)(time.time())  # the tick claims it
        assert await DueFollowUps(store)(time.time()) == []  # once
        assert await runner.run_schedule(spec)

    assert isinstance(spec, FollowUpSchedule) and (spec.agent_id, spec.channel) == ("meal-planner", "meal-planning")
    (prompt,) = ramsay.prompts
    assert "This is your follow-up #1" in prompt and "Check now: Wagyu sales at 99 Ranch" in prompt
    session = ramsay.sessions[0]
    assert (session.source, session.get_surface(), session.is_stateless()) == ("followup", "scheduled", False)
    assert briefed == []  # a check never carries the team's `next` routes
    checked = store.get(follow_up.id)
    assert (checked.checks, checked.finds, checked.claimed_at) == (1, finds, None)
    if finds:
        assert meal_planning.sent == [reply] and checked.last_report == reply
        record_post.assert_called_once_with(meal_planning, reply, origin="followup:1", loader=runner.loader)
        assert record_push.call_args.kwargs["label"] == "a follow-up"
    else:
        assert meal_planning.sent == [] and checked.last_report == ""
        record_post.assert_not_called()


@pytest.mark.asyncio
async def test_a_scan_check_that_cant_read_is_posted_once_as_the_follow_ups(tmp_path, monkeypatch):
    problems = Problems("price_watch", ("99 Ranch's weekly ad didn't load",))
    scan = SimpleNamespace(evaluate=lambda ctx: WorkDecision(False, "price_watch can't run", detail=problems))
    monkeypatch.setattr(follow_ups_module, "build_precondition", lambda config, agent_id=None: scan)
    store, follow_up = _ramsays_follow_up(tmp_path, monkeypatch, Check(type="price_watch", args={"item": "wagyu"}))
    ramsay, meal_planning = Ramsay("never asked"), MealPlanning()
    runner = _runner(tmp_path, ramsay, meal_planning)

    with Config().overriding(proactive_delivery=True), \
         patch.object(runner_mod, "record_channel_post"), patch.object(runner_mod, "record_push"):
        (spec,) = await DueFollowUps(store)(time.time())
        assert not await runner.run_schedule(spec)

    assert ramsay.prompts == []  # no turn
    assert meal_planning.sent == ["⚠️ The scheduled check `follow-up #1 (price_watch)` couldn't run normally:\n"
                                  "- 99 Ranch's weekly ad didn't load\nReply here and I'll help fix it."]
    checked = store.get(follow_up.id)
    assert (checked.checks, checked.claimed_at) == (0, None)  # a problem counts nothing
