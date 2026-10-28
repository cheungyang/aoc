"""Live Context (core/prompts/live_context.py): what's true right now, from teammates' signals."""
import re
from contextlib import contextmanager
from datetime import datetime

import pytest

from core.config.settings import Config
from core.prompts import live_context
from core.prompts.live_context import CACHE_S, MAX_AGE_S, get_live_context_prompt
from core.runtime.foundation.context import current_execution_context
from core.runtime.foundation.sessions import SessionManager
from core.storage.follow_ups import Check, FollowUpStore
from core.storage.signals import SignalStore
from core.util.local_time import get_local_timezone

NOW = datetime(2026, 10, 24, 9, 31, tzinfo=get_local_timezone()).timestamp()
ERIC = {"channels": ["weekend-planning"], "memory_topics": ["family", "travel"]}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """A fresh cache, and the topics the config names: no TAGS.md from the vault."""
    monkeypatch.setattr(live_context, "_cache", {})
    monkeypatch.setattr(live_context, "memory_topics",
                        lambda config, agent_id="": sorted(config.get("memory_topics") or []))


@pytest.fixture
def live():
    with Config().overriding(proactive_delivery=True):
        yield


@pytest.fixture
def store():
    return SignalStore()  # memory.db, which tests/conftest.py points into the test's tmp dir


def _situation(store, gist, *, at=NOW - 3600, until="2026-10-27", tags="travel,family", **extra):
    decision_id = store.start_decision(tap="chat", session_id="s", first_turn_at=at, mode="live", at=at)
    store.add_signal({"decision_id": decision_id, "at": at, "kind": "situation", "gist": gist,
                      "evidence": "We're in Tahoe", "evidence_found": 1, "tags": tags, "until": until,
                      "source_agent": "day-planner", "source_channel": "day-planning",
                      "dedupe_key": f"situation:{gist}", **extra})


def _lines(block):
    return re.findall(r"- (.+?) \(until ", block)


def test_it_shows_the_live_situations_an_agent_reads(store, live):
    _situation(store, "The user and family are in Lake Tahoe Oct 23-27")
    _situation(store, "The garage door sensor is out", tags="home")
    block = get_live_context_prompt("excursion-planner", ERIC, now=NOW)
    assert block.startswith("<LIVE_CONTEXT>\n<description>") and block.endswith("</content>\n</LIVE_CONTEXT>")
    assert "<content>- The user and family are in Lake Tahoe Oct 23-27 (until 2026-10-27)</content>" in block
    assert "day-planner" not in block and "day-planning" not in block  # no source named


def test_newest_first_and_at_most_8(store, live):
    for i in range(10):
        _situation(store, f"Fact {i}", at=NOW - 3600 + i)
    assert _lines(get_live_context_prompt("excursion-planner", ERIC, now=NOW)) == [f"Fact {i}" for i in range(9, 1, -1)]


def test_it_shows_only_what_is_live(store, live):
    _situation(store, "True through today", until="2026-10-24")
    _situation(store, "Over yesterday", until="2026-10-23")
    _situation(store, "Learned 15 days ago", at=NOW - MAX_AGE_S - 86400, until="2026-12-31")
    _situation(store, "Evidence not found", evidence_found=0)
    _situation(store, "An event", kind="event")
    assert _lines(get_live_context_prompt("excursion-planner", ERIC, now=NOW)) == ["True through today"]


def test_with_the_switch_off_it_is_empty(store):
    _situation(store, "In Tahoe")
    assert get_live_context_prompt("excursion-planner", ERIC, now=NOW) == ""


@pytest.mark.parametrize("config", [
    {**ERIC, "stateless": True},
    {"channels": ["weekend-planning"]},
    {**ERIC, "memory_topics": []},
], ids=["stateless agent", "no topics", "empty topics"])
def test_a_stateless_agent_or_one_without_topics_gets_none(store, live, config):
    _situation(store, "In Tahoe")
    assert get_live_context_prompt("excursion-planner", config, now=NOW) == ""


@pytest.mark.parametrize("run, shown", [
    (dict(source="discord", channel="weekend-planning"), True),
    (dict(source="job"), False),
    (dict(source="tool", channel="weekend-planning", stateless=True), False),
    (dict(source="tool", channel="weekend-planning", record_memory=False), False),
], ids=["its own turn", "a job", "a stateless run", "a run that records no memory (a dream)"])
def test_only_a_run_that_keeps_memory_gets_it(store, live, run, shown):
    _situation(store, "In Tahoe")
    token = current_execution_context.set(SessionManager.get_session(agent_id="excursion-planner", **run))
    try:
        assert bool(get_live_context_prompt("excursion-planner", ERIC, now=NOW)) is shown
    finally:
        current_execution_context.reset(token)


def test_the_block_is_kept_for_its_window(store, live):
    _situation(store, "In Tahoe")
    first = get_live_context_prompt("excursion-planner", ERIC, now=NOW)
    _situation(store, "Guests on Saturday", at=NOW - 60)
    assert get_live_context_prompt("excursion-planner", ERIC, now=NOW + CACHE_S - 1) == first
    assert _lines(get_live_context_prompt("excursion-planner", ERIC, now=NOW + CACHE_S + 1)) == [
        "Guests on Saturday", "In Tahoe"]


def test_an_unreadable_board_leaves_it_out(live, monkeypatch, capsys):
    def locked(self, *args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(SignalStore, "live_situations", locked)
    assert get_live_context_prompt("excursion-planner", ERIC, now=NOW) == ""
    assert "No Live Context for excursion-planner: RuntimeError: database is locked" in capsys.readouterr().out


# -- the agent's own follow-ups --------------------------------------------------------

def _follow_up(goal="Tahoe cabin under $400 for MLK weekend", agent_id="excursion-planner", until="2026-11-30"):
    return FollowUpStore().open(agent_id=agent_id, goal=goal, check=Check(type="model", what=goal),
                                every_s=2 * 86400, until=until, channel="weekend-planning")


def _follow_up_lines(block):
    match = re.search(r"<your_follow_ups>\n<description>.+?</description>\n<content>(.*?)</content>\n"
                      r"</your_follow_ups>", block, re.DOTALL)
    return match.group(1).splitlines() if match else []


@contextmanager
def _run(**run):
    """A turn of excursion-planner's: a message in its own channel, unless `run` says otherwise.
    Its follow-ups show only to a run that has the follow_up tool."""
    values = dict(agent_id="excursion-planner", source="discord", channel="weekend-planning")
    values.update(run)
    token = current_execution_context.set(SessionManager.get_session(**values))
    try:
        yield
    finally:
        current_execution_context.reset(token)


def test_it_shows_the_agents_own_open_follow_ups_soonest_until_first(store, live):
    _situation(store, "In Tahoe")
    later = _follow_up("Ski rental deal", until="2026-12-20")
    sooner = _follow_up()
    _follow_up("Award seats to MAD", agent_id="reward-travel")  # another agent's
    FollowUpStore().close(_follow_up("Done already", until="2026-11-02").id)
    with _run():
        block = get_live_context_prompt("excursion-planner", ERIC, now=NOW)
    assert _lines(block) == ["In Tahoe"]  # the situations, as before
    assert _follow_up_lines(block) == [
        f"- #{sooner.id} Tahoe cabin under $400 for MLK weekend · every 2d until 2026-11-30",
        f"- #{later.id} Ski rental deal · every 2d until 2026-12-20",
    ]
    assert "<description>What you said you'd keep working on; close one with follow_up when the user says " \
           "it's done or no longer wanted.</description>" in block


def test_at_most_5_follow_ups(store, live):
    for day in (16, 15, 14, 13, 12, 11, 10):
        _follow_up(f"Watch {day}", until=f"2026-11-{day}")
    with _run():
        lines = _follow_up_lines(get_live_context_prompt("excursion-planner", ERIC, now=NOW))
    assert [line.split(" · ")[0].split(" ", 2)[2] for line in lines] == [f"Watch {d}" for d in (10, 11, 12, 13, 14)]


def test_follow_ups_show_even_for_an_agent_without_memory_topics(store, live):
    _situation(store, "In Tahoe")
    _follow_up()
    with _run():
        block = get_live_context_prompt("excursion-planner", {"channels": ["weekend-planning"]}, now=NOW)
    assert block.startswith("<LIVE_CONTEXT>\n<your_follow_ups>\n") and block.endswith("</your_follow_ups>\n</LIVE_CONTEXT>")
    assert _lines(block) == [] and "teammates learned" not in block


def test_follow_ups_show_only_to_a_run_with_the_tool_and_each_block_is_kept(store, live):
    _situation(store, "In Tahoe")
    _follow_up()
    with _run(source="tool"):  # the router's hand-over: the agent you're talking to
        routed = get_live_context_prompt("excursion-planner", ERIC, now=NOW)
    with _run(source="tool", called_by_agent=True):  # agent_call: another agent asked
        called = get_live_context_prompt("excursion-planner", ERIC, now=NOW + 1)
    assert _lines(routed) == _lines(called) == ["In Tahoe"]
    assert len(_follow_up_lines(routed)) == 1 and "<your_follow_ups>" not in called
    # Within the window each run gets its own block back.
    with _run(source="tool"):
        assert get_live_context_prompt("excursion-planner", ERIC, now=NOW + 2) == routed
    with _run(source="tool", called_by_agent=True):
        assert get_live_context_prompt("excursion-planner", ERIC, now=NOW + 3) == called
    assert set(live_context._cache) == {("excursion-planner", True), ("excursion-planner", False)}
    # With no context, no run has the tool: no follow-ups.
    assert "<your_follow_ups>" not in get_live_context_prompt("excursion-planner", ERIC, now=NOW + 4)


def test_with_the_switch_off_there_are_no_follow_ups_either(store):
    _follow_up()
    with _run():
        assert get_live_context_prompt("excursion-planner", ERIC, now=NOW) == ""
