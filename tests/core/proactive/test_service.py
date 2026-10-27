"""The proactive service (core/proactive/service.py): recording turns and timing Switchboard runs."""
import asyncio

import pytest

from core.proactive import service as service_module
from core.proactive.service import ProactiveService
from core.proactive.taps import TAP_CHAT, TAP_EFFECT, TAP_SCHEDULED, LedgerEntry
from core.storage.signals import SignalStore


def _entry(tap=TAP_CHAT, session_id="s1", at=None, text="hi"):
    return LedgerEntry(at=at if at is not None else 0.0, agent_id="day-planner", channel="day-planning",
                       session_id=session_id, source="tool", surface="tool", caller="main", tap=tap, user_text=text)


class Recorder:
    """Stands in for the Switchboard pipeline: what it was asked to decide, and when."""

    def __init__(self):
        self.calls = []
        self.called = asyncio.Event()

    async def __call__(self, session_id, turns):
        self.calls.append((session_id, [t["user_text"] for t in turns]))
        self.called.set()


@pytest.fixture
def store(tmp_path):
    return SignalStore(str(tmp_path / "memory.db"))


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(service_module, "QUIET_S", 0.2)
    monkeypatch.setattr(service_module, "MAX_WAIT_S", 0.5)
    monkeypatch.setattr(service_module, "CATCH_UP_S", 0.0)


async def _settle(seconds=0.05):
    await asyncio.sleep(seconds)


@pytest.mark.asyncio
async def test_a_turn_is_recorded_without_waiting(store):
    service = ProactiveService(store=store)
    service.turn_ended(_entry())
    assert store.pending_turns() == []  # turn_ended returned before the write
    await _settle()
    assert [t["user_text"] for t in store.pending_turns()] == ["hi"]


@pytest.mark.asyncio
async def test_without_a_pipeline_turns_are_only_recorded(store, fast):
    service = ProactiveService(store=store)
    service.turn_ended(_entry(tap=TAP_EFFECT))
    await _settle(0.3)
    assert len(store.pending_turns()) == 1 and service._timers == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("tap", [TAP_EFFECT, TAP_SCHEDULED])
async def test_an_effect_or_a_schedule_is_read_at_once(store, fast, tap):
    decide = Recorder()
    service = ProactiveService(decide=decide, store=store, clock=lambda: 0.0)
    service.turn_ended(_entry(tap=tap))
    await asyncio.wait_for(decide.called.wait(), 0.15)  # well before the quiet would end
    assert decide.calls == [("s1", ["hi"])]


@pytest.mark.asyncio
async def test_a_conversation_is_read_after_it_goes_quiet(store, fast):
    decide = Recorder()
    service = ProactiveService(decide=decide, store=store, clock=lambda: 0.0)
    service.turn_ended(_entry(text="we're in Tahoe"))
    await _settle(0.1)
    service.turn_ended(_entry(text="until Tuesday"))  # restarts the quiet
    await _settle(0.15)
    assert decide.calls == []
    await asyncio.wait_for(decide.called.wait(), 0.3)
    assert decide.calls == [("s1", ["we're in Tahoe", "until Tuesday"])]


@pytest.mark.asyncio
async def test_a_long_conversation_is_read_by_the_cap(store, fast):
    now = [0.0]
    service = ProactiveService(decide=Recorder(), store=store, clock=lambda: now[0])
    service._first_unread["s1"] = 0.0
    now[0] = 0.45  # 0.05 s left of MAX_WAIT_S
    service.schedule("s1", TAP_CHAT)
    timer = service._timers["s1"]
    assert timer.when() - asyncio.get_running_loop().time() <= 0.06
    timer.cancel()


@pytest.mark.asyncio
async def test_sessions_are_read_separately(store, fast):
    decide = Recorder()
    service = ProactiveService(decide=decide, store=store, clock=lambda: 0.0)
    service.turn_ended(_entry(tap=TAP_EFFECT, session_id="s1", text="receipt"))
    service.turn_ended(_entry(tap=TAP_CHAT, session_id="s2", text="chat"))
    await asyncio.wait_for(decide.called.wait(), 0.15)
    await _settle()
    assert decide.calls == [("s1", ["receipt"])]


@pytest.mark.asyncio
async def test_a_failed_run_is_logged(store, fast, capsys):
    async def broken(session_id, turns):
        raise RuntimeError("model down")

    store.record_turn(_entry().row())
    await ProactiveService(decide=broken, store=store).run("s1")
    assert "model down" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_turns_left_unread_by_a_restart_are_picked_up(store, fast):
    store.record_turn(_entry(session_id="s1", text="before the restart").row())
    decide = Recorder()
    await ProactiveService(decide=decide, store=store).start()
    await asyncio.wait_for(decide.called.wait(), 0.2)
    assert decide.calls == [("s1", ["before the restart"])]


class TestInstall:
    """main.py's install: every agent turn and tool call is tapped. Delivery waits on
    the one switch, PROACTIVE_DELIVERY."""

    @pytest.fixture(autouse=True)
    def hooks(self):
        from core.channel.discord.radio import clear_listeners
        from core.runtime.hooks import set_turn_hooks, turn_hooks
        from core.tooling.hooks import set_tool_hooks, tool_hooks
        saved = (list(turn_hooks()), list(tool_hooks()))
        yield saved
        set_turn_hooks(saved[0])
        set_tool_hooks(saved[1])
        clear_listeners()

    def test_it_adds_the_taps_after_the_hooks_already_there(self, hooks):
        from core.channel.discord import radio as discord_radio
        from core.proactive.pipeline import Pipeline
        from core.proactive.taps import CallLog, SignalHook
        from core.runtime.hooks import turn_hooks
        from core.tooling.hooks import tool_hooks
        service = service_module.install()
        assert isinstance(service, ProactiveService)
        assert turn_hooks()[:-1] == hooks[0] and isinstance(turn_hooks()[-1], SignalHook)
        assert list(tool_hooks()[:-1]) == hooks[1] and isinstance(tool_hooks()[-1], CallLog)
        # With the switch off (the default), each decision is recorded in shadow.
        assert isinstance(service.decide, Pipeline) and service.decide.current_mode() == "shadow"
        assert service.decide.radio is not None
        # Team radio hears ✅/❌ and poll votes from the Discord runner.
        assert len(discord_radio._reaction_listeners) == 1 and len(discord_radio._vote_listeners) == 1

    def test_the_schedule_runner_runs_the_deliveries_and_is_woken_for_them(self, hooks):
        from core.channel.discord.ack import DiscordAck
        from core.proactive.delivery import DueDeliveries, SignalsForMe

        def wake():
            pass

        service = service_module.install(wake=wake)
        assert isinstance(service.due_work, DueDeliveries)
        assert service.decide.wake is wake
        # A `now` approval also reacts on your message, and a `next` route rides along on
        # the recipient's scheduled run.
        assert isinstance(service.decide.ack, DiscordAck)
        assert isinstance(service.briefing, SignalsForMe)
