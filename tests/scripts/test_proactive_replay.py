"""The memory replay (scripts/proactive_replay.py): its scoring, its memory switch, and its flow, without a model."""
import pytest

from core.proactive.switchboard import ModelRun, parse_reply
from core.storage.signals import SignalStore
from scripts import proactive_replay as replay
from scripts.proactive_replay import Labeled, load_labeled, memory_off, render, score

ROUTED = """<switchboard_response><signal kind="artifact" tags="food"><gist>g</gist><evidence>e</evidence>
<route to="{to}" offer="o" urgency="now"/></signal></switchboard_response>"""


def _labeled_decision(store, labels, prompt="the prompt", labeled_at=100.0):
    decision_id = store.start_decision(tap="effect", session_id="s", first_turn_at=1.0, mode="shadow", at=2.0)
    signal_id = store.add_signal({"decision_id": decision_id, "at": 2.0, "kind": "artifact", "gist": "g"})
    for to, label in labels.items():
        route_id = store.add_route({"signal_id": signal_id, "decision_id": decision_id, "to_agent": to, "asked": "now",
                                    "urgency": "now", "verdict": "drop" if label == "dropped" else "deliver"})
        if label in ("good", "bad"):
            store.label_routes([route_id], label, at=labeled_at)
    store.finish_decision(decision_id, prompt=prompt)
    return decision_id


def test_the_four_outcomes():
    labels = {"meal-planner": "good", "reward-travel": "good", "home-steward": "bad", "excursion-planner": "bad"}
    result = score(labels, {"meal-planner", "home-steward", "fitness-coach"})
    assert (result.kept, result.lost, result.repeated, result.avoided) == (1, 1, 1, 1)
    assert result.precision == 0.5
    assert score({"meal-planner": "bad"}, set()).precision is None


def test_only_labeled_offered_routes_with_a_prompt_are_loaded(tmp_path):
    store = SignalStore(str(tmp_path / "memory.db"))
    first = _labeled_decision(store, {"meal-planner": "good", "stranger": "dropped"})
    _labeled_decision(store, {"meal-planner": "good"}, prompt="")           # no prompt: can't replay
    _labeled_decision(store, {"meal-planner": "unlabeled"})                 # no label
    (case,) = load_labeled(store)
    assert (case.decision_id, case.labels) == (first, {"meal-planner": "good"})
    assert load_labeled(store, only=first)[0].decision_id == first


def test_memory_off_empties_only_the_switchboards_memory(monkeypatch):
    from core.knowledge.memory import inject
    monkeypatch.setattr(inject, "render_memory", lambda agent, *a, **k: f"memory of {agent}")
    monkeypatch.setattr(inject, "render_feedback", lambda agent, *a, **k: f"feedback of {agent}")
    with memory_off():
        assert inject.render_memory("main") == "" and inject.render_feedback("main") == ""
        assert inject.render_memory("day-planner") == "memory of day-planner"
    assert inject.render_memory("main") == "memory of main"


@pytest.mark.asyncio
async def test_each_case_runs_with_and_without_memory(monkeypatch):
    from core.knowledge.memory import inject
    monkeypatch.setattr(inject, "render_feedback", lambda agent, *a, **k: "never route receipts to Miles")

    async def run(prompt):
        # The stand-in Switchboard heeds its feedback when it has it.
        to = "meal-planner" if inject.render_feedback("main") else "reward-travel"
        return ModelRun(reply=ROUTED.format(to=to))

    case = Labeled(decision_id=7, prompt="p", labeled_at=200.0, labels={"meal-planner": "good", "reward-travel": "bad"})
    (result,) = await replay.replay([case], run, parse_reply)
    assert result.proposed == {"with": {"meal-planner"}, "without": {"reward-travel"}}
    assert (result.scores["with"].precision, result.scores["without"].precision) == (1.0, 0.0)

    report = render([result], memory_at=100.0)
    assert "| labeled since main's memory last changed (fair) | 1 | 1.00" in report
    assert render([result], memory_at=300.0).count("| 0 |") == 1  # nothing labeled since: no fair test yet


@pytest.mark.asyncio
async def test_a_failed_run_is_reported_not_scored_as_avoided_silently():
    async def run(prompt):
        return ModelRun(error="no reply within 90s")

    case = Labeled(decision_id=8, prompt="p", labeled_at=1.0, labels={"meal-planner": "bad"})
    (result,) = await replay.replay([case], run, parse_reply)
    assert result.errors == {"with": "no reply within 90s", "without": "no reply within 90s"}
    assert "no reply within 90s" in render([result], memory_at=0.0)
