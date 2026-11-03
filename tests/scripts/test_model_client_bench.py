"""scripts/model_client_bench.py: a fresh Gemini client against a reused one, with no model calls here."""
import asyncio

import pytest

from scripts import model_client_bench as bench


class FakeModel:
    def __init__(self, name):
        self.name = name


@pytest.mark.asyncio
async def test_the_arms_take_turns_and_the_warm_up_is_not_counted():
    built, calls, slept = [], [], []

    def build(model):
        built.append(FakeModel(model))
        return built[-1]

    async def call(llm):
        calls.append(llm)
        return (100.0, 200.0) if llm is built[0] else (300.0, 400.0)

    async def sleep(seconds):
        slept.append(seconds)

    results = await bench.measure("m", rounds=3, idle=15, build=build, call=call, sleep=sleep)

    assert len(calls) == 1 + 3 * 3, "one warm-up, then three arms a round"
    assert calls[0] is built[0]
    assert len(built) == 1 + 3, "the kept model, then one fresh per round"
    assert results["fresh"] == [(300.0, 400.0)] * 3
    assert results["reused"] == results["reused_idle"] == [(100.0, 200.0)] * 3
    assert slept == [15, 15, 15]
    assert len(results["builds"]) == 4


def test_the_report_names_the_saving_at_the_idle_spacing():
    results = {"builds": [900.0, 80.0, 80.0, 80.0], "fresh": [(600.0, 800.0)] * 3,
               "reused": [(400.0, 600.0)] * 3, "reused_idle": [(550.0, 750.0)] * 3}
    report = bench.render(results, "gemini-x", 3, 15, "nas")
    assert "Gemini client reuse: gemini-x, 3 rounds, on nas" in report
    assert "reused, after 15 s idle" in report
    assert "Reuse would save ~50 ms of a turn's first chunk at that spacing (~200 ms back to back)" in report
    assert "plus ~80 ms of building on the loop" in report, "the first build, imports and all, left out"


def test_it_only_calls_the_model():
    assert bench.READ_ONLY is True
