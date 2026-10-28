"""The hand-off replay (scripts/handoff_replay.py): its pure parts and its guard.

The replay itself runs real agents against your sessions, so it isn't run
here; these pin what it decides without them.
"""
from datetime import datetime

import pytest

from core.loaders.tools_loader import ToolsLoader
from core.util.local_time import get_local_timezone
from scripts import handoff_replay as replay
from scripts.handoff_replay import (
    CASES_PATH,
    REFUSED_ACTIONS,
    WITHHELD_TOOLS,
    CaseResult,
    _UntilStore,
    matches,
    render,
    until_epoch,
    withhold_side_effects,
)


def test_the_cases_stay_out_of_the_repo():
    """They quote the user's conversations (tests/test_repo_privacy.py)."""
    assert CASES_PATH.endswith("sessions/handoff_replay_cases.json")


def test_until_is_the_whole_minute_in_local_time():
    end = until_epoch("2026-09-09 19:46")
    minute = datetime(2026, 9, 9, 19, 46, tzinfo=get_local_timezone()).timestamp()
    assert minute < end < minute + 60
    assert until_epoch(None) is None


def test_the_store_answers_as_of_until():
    class Store:
        def recent_history(self, session_id, limit, max_bytes=None):
            return [{"from": "user", "message": f"m{ts}", "ts": ts} for ts in (10, 20, 30)][-limit:]

    assert [r["ts"] for r in _UntilStore(Store(), 20).recent_history("s", limit=5)] == [10, 20]
    assert [r["ts"] for r in _UntilStore(Store(), None).recent_history("s", limit=2)] == [20, 30]


def test_a_reply_matches_any_pattern_in_any_case():
    assert matches(["qmd", "search engine"], "Starting a syllabus on QMD.")
    assert not matches(["qmd"], "Starting a syllabus on caching.")


def test_the_report_counts_passes_against_the_target():
    cases = [{"id": i, "from": "pkm-wiki", "line": "Ted, go",
              "expect": {"agent": "topic-researcher", "channel": "topic-research", "referent": "x",
                         "reply_matches": ["x"]}} for i in ("a", "b")]
    results = [CaseResult("a", "Ted, go", routed_to="topic-researcher", routed_channel="topic-research",
                          reply_matches=True),
               CaseResult("b", "Ted, go", reply_matches=False, failures=["reply doesn't name 'x'"])]
    report = render(cases, results, dry=False, show_capsule=False)
    assert "Passed 1 of 2 (target: 9 of 10)." in report
    assert "❌ reply doesn't name 'x'" in report


def test_the_guard_withholds_tools_that_act_elsewhere(monkeypatch):
    def tool(name):
        def run():
            return name
        run.__name__ = name
        return run

    monkeypatch.setattr(ToolsLoader, "get_tools", lambda self, ctx: [tool("filesystem"), tool("graph_call"),
                                                                      tool("nlm"), tool("git")])
    monkeypatch.setattr(ToolsLoader, "check_permission", lambda self, ctx, tool_id, action_name=None, path=None,
                        **kwargs: True)
    monkeypatch.setattr(replay, "WITHHELD_TOOLS", WITHHELD_TOOLS)
    withhold_side_effects()

    loader = ToolsLoader()
    assert [t.__name__ for t in loader.get_tools(None)] == ["filesystem"]
    assert not loader.check_permission(None, "graph_call", "start")
    assert all(not loader.check_permission(None, tool_id, action) for tool_id, action in REFUSED_ACTIONS)
    assert loader.check_permission(None, "task_query", "search")


QUOTA = "[429] Resource has been exhausted (e.g. check quota)."
QMD_CASE = {"id": "wiki-qmd-syllabus", "from": "pkm-wiki", "line": "Ted, create a syllabus on THIS topic",
            "expect": {"agent": "topic-researcher", "channel": "topic-research", "referent": "qmd",
                       "reply_matches": ["qmd"]}}


def _flaky_agent(monkeypatch, errors):
    """Stands in for the named agent: the first runs fail with `errors`, one each; then it replies."""
    from core.loaders.agents_loader import AgentsLoader
    from core.runtime.foundation import events
    from core.runtime.foundation.sessions import SessionManager
    from scripts import voice_dryrun

    pending, runs = list(errors), []

    class Agent:
        async def execute_stream(self, prompt, session):
            runs.append(prompt)
            if pending:
                yield {"type": events.ERROR, "content": pending.pop(0)}
                return
            yield {"type": events.FINAL_RESPONSE, "text": "Starting a syllabus on qmd."}

    monkeypatch.setattr(voice_dryrun, "reset_history", lambda db_path: None)
    monkeypatch.setattr(AgentsLoader, "get_agent", lambda self, agent_id: Agent())
    monkeypatch.setattr(SessionManager, "get_session", lambda **kwargs: object())
    return runs


@pytest.mark.asyncio
async def test_a_quota_error_runs_the_turn_again(monkeypatch):
    """A 429 is the provider's, not the hand-off's: the case is judged on the run after it."""
    runs = _flaky_agent(monkeypatch, [QUOTA])
    result = CaseResult(QMD_CASE["id"], QMD_CASE["line"], capsule="<handoff/>")

    await replay.answer(QMD_CASE, result, db_path="unused", pause=0)

    assert len(runs) == 2 and result.retries == 1
    assert result.passed and result.failures == []
    report = render([QMD_CASE], [result], dry=False, show_capsule=False)
    assert "run again after a provider quota error: 1×" in report


@pytest.mark.asyncio
async def test_any_other_error_fails_the_case_at_once(monkeypatch):
    runs = _flaky_agent(monkeypatch, ["boom"])
    result = CaseResult(QMD_CASE["id"], QMD_CASE["line"], capsule="<handoff/>")

    await replay.answer(QMD_CASE, result, db_path="unused", pause=0)

    assert len(runs) == 1 and result.retries == 0
    assert not result.passed
    assert result.failures[0] == "agent error: boom"


@pytest.mark.asyncio
async def test_quota_retries_are_bounded(monkeypatch):
    runs = _flaky_agent(monkeypatch, [QUOTA] * (replay.QUOTA_RETRIES + 1))
    result = CaseResult(QMD_CASE["id"], QMD_CASE["line"], capsule="<handoff/>")

    await replay.answer(QMD_CASE, result, db_path="unused", pause=0)

    assert len(runs) == replay.QUOTA_RETRIES + 1 and result.retries == replay.QUOTA_RETRIES
    assert not result.passed
    assert result.failures[0] == f"agent error: {QUOTA}"
