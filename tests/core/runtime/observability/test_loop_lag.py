"""The event-loop lag probe (core/runtime/observability/loop_lag.py)."""
import asyncio
import contextlib
import io
import time

import pytest

from core.runtime.observability.loop_lag import LOG_MS, STALL_MS, LoopLag


@pytest.mark.asyncio
async def test_a_blocked_loop_is_a_stall_in_its_window():
    probe = LoopLag(interval=0.01)
    task = asyncio.create_task(probe.watch())
    await asyncio.sleep(0.03)
    started = time.monotonic()
    time.sleep(0.15)  # blocks the loop, as a sync database call would
    await asyncio.sleep(0.03)
    ended = time.monotonic()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert not probe.running
    probe.running = True  # read it back as a running probe would
    lag = probe.between(started, ended)
    assert lag["stalls"] == 1
    assert 120 <= lag["max_ms"] <= lag["total_ms"]


def test_short_delays_are_scheduling_noise_not_stalls():
    probe = LoopLag()
    probe.running = True
    probe.record(10.0, STALL_MS - 1)
    assert probe.between(0, 20) == {"stalls": 0, "total_ms": 0, "max_ms": 0}


def test_only_the_stalls_that_ended_in_the_window_count():
    probe = LoopLag()
    probe.running = True
    for at, ms in ((1.0, 80), (2.0, 200), (3.0, 60)):
        probe.record(at, ms)
    assert probe.between(1.5, 3.0) == {"stalls": 2, "total_ms": 260, "max_ms": 200}


def test_without_a_probe_running_there_is_nothing_to_say():
    probe = LoopLag()
    probe.record(1.0, 300)
    assert probe.between(0, 2) is None


def test_a_long_stall_is_logged_when_it_ends():
    probe = LoopLag()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        probe.record(1.0, LOG_MS - 1)
        probe.record(2.0, 1234)
    assert out.getvalue() == "[LoopLag] The event loop was blocked for 1.2 s\n"
