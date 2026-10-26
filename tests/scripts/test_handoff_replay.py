"""The hand-off replay (scripts/handoff_replay.py): its pure parts and its guard.

The replay itself runs real agents against your sessions, so it isn't run
here; these pin what it decides without them.
"""
from datetime import datetime

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
    assert "Passed 1 of 2 (G2 target: 9 of 10)." in report
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
