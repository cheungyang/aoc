"""The voice replay benchmark (scripts/voice_dryrun.py)."""
import json
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts import voice_dryrun as replay
from scripts.voice_dryrun import (
    CASES_PATH,
    CaseResult,
    check_expectations,
    isolate_sessions,
    load_cases,
    percentile,
    render_report,
    replay_tokens,
    reset_history,
    run_case,
    stage_table,
)


def _cases_file(tmp_path, cases):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases))
    return str(path)


CASES = [
    {"id": "read", "room": "home-automation", "utterance": "Lights?", "expect": {"agent": "home-steward"}},
    {"id": "write", "room": "home-automation", "utterance": "Lights off.", "mutates": True, "expect": {}},
]


class TestLoadCases:

    def test_the_checked_in_cases_load(self):
        cases = load_cases(CASES_PATH, allow_mutations=True)
        assert len(cases) >= 10
        assert len({c["id"] for c in cases}) == len(cases)
        assert all(c["utterance"] and c["room"] for c in cases)

    def test_mutating_cases_are_skipped_unless_allowed(self, tmp_path):
        path = _cases_file(tmp_path, CASES)
        assert [c["id"] for c in load_cases(path)] == ["read"]
        assert [c["id"] for c in load_cases(path, allow_mutations=True)] == ["read", "write"]

    def test_selecting_cases_and_typos(self, tmp_path):
        path = _cases_file(tmp_path, CASES)
        assert [c["id"] for c in load_cases(path, only=["read"])] == ["read"]
        with pytest.raises(ValueError, match="unknown case id"):
            load_cases(path, only=["raed"])
        bad = _cases_file(tmp_path, [{"id": "x", "room": "r", "utterance": "u", "expect": {"tool_any": []}}])
        with pytest.raises(ValueError, match="unknown expectation"):
            load_cases(bad)


class TestExpectations:

    def result(self, **kw):
        base = dict(case_id="c", run=1, marks={}, reply="The kitchen light is on.", spoken=["The kitchen light is on."],
                    tools=["home_assistant"], agents=["home-steward"], model_calls=[{"thinking": "low"}] * 2)
        base.update(kw)
        return CaseResult(**base)

    def test_a_good_turn_passes(self):
        case = {"expect": {"agent": "home-steward", "tools_any": ["home_assistant"], "reply_matches": ["light"],
                           "spoken_excludes": ["Butler:"], "max_model_calls": 3}}
        assert check_expectations(case, self.result()) == []

    def test_each_kind_of_failure(self):
        case = {"expect": {
            "agent": "day-planner", "tools_any": ["task_query"], "tools_all": ["filesystem"],
            "tools_none": ["home_assistant"], "reply_matches": ["thermostat"], "reply_excludes": ["kitchen"],
            "spoken_excludes": ["light"], "max_model_calls": 1,
        }}
        failures = check_expectations(case, self.result())
        assert len(failures) == 8, failures


class TestReport:

    def test_percentiles(self):
        assert percentile([], 50) is None
        assert percentile([300, 100, 200], 50) == 200
        assert percentile(list(range(1, 11)), 90) == 9

    def test_stage_table_and_report(self):
        results = [
            CaseResult(case_id="a", run=1, marks={"agent_start": 5, "first_token": 1200, "tts_first_byte": 1900}),
            CaseResult(case_id="a", run=2, marks={"agent_start": 6, "first_token": 1600}, failures=["reply lacks /x/"]),
        ]
        table = dict((stage, (p50, p90, n)) for stage, p50, p90, n in stage_table(results))
        assert table["first_token"] == (1200, 1600, 2)
        assert table["tts_first_byte"] == (1900, 1900, 1)
        report = render_report(results)
        assert "| a | 1 | PASS |" in report and "| a | 2 | FAIL |" in report
        assert "Quality: 1/2 runs passed." in report


class TestIsolation:

    def test_stores_point_at_the_throwaway_db(self, tmp_path):
        from core.knowledge.memory import sqlite_checkpointer, sqlite_session_store
        db = isolate_sessions(str(tmp_path / "replay"))
        assert sqlite_session_store.SqliteSessionStore().db_path == db
        assert sqlite_checkpointer.SqliteCheckpointer().db_path == db

    def test_history_reset_and_token_count(self, tmp_path):
        from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
        db = str(tmp_path / "memory.db")
        store = SqliteSessionStore(db_path=db)
        store.append_token_usage("home-steward:tool:home-automation", "m", 100, 20, 0.0)
        store.append_message("home-steward:tool:home-automation", "user", "hi")
        conn = sqlite3.connect(db)
        conn.execute('INSERT INTO "ctx_home_steward_tool_home_automation" (entry_type, created_at) VALUES (\'checkpoint\', 0)')
        conn.commit()
        conn.close()

        assert replay_tokens(0, db) == {"input": 100, "output": 20}
        reset_history(db)
        conn = sqlite3.connect(db)
        kinds = [r[0] for r in conn.execute('SELECT entry_type FROM "ctx_home_steward_tool_home_automation"')]
        conn.close()
        assert sorted(kinds) == ["message", "token"]


@pytest.mark.asyncio
async def test_run_case_traces_and_checks_a_turn(tmp_path):
    case = {"id": "lights", "room": "home-automation", "utterance": "Are the lights on?",
            "expect": {"agent": "home-steward", "tools_all": ["home_assistant"], "spoken_excludes": ["Butler"]}}

    async def fake_stream(prompt, session):
        assert session.get_surface() == "voice" and session.record_memory is False
        yield {"type": "tool_start", "tool_name": "home_assistant", "tool_args": {}}
        yield {"type": "tool_end", "tool_name": "home_assistant", "output": "on"}
        yield {"type": "token", "content": "🛎️ Butler: ", "agent_id": "home-steward", "is_header": True}
        yield {"type": "token", "content": "The kitchen light is on.", "agent_id": "home-steward"}
        yield {"type": "final_response", "text": "The kitchen light is on.", "response": None}

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)
    tts = MagicMock()
    tts.synthesize_to_file = AsyncMock(return_value=str(tmp_path / "a.mp3"))
    db = isolate_sessions(str(tmp_path / "replay"))

    with patch("core.loaders.agents_loader.AgentsLoader") as loader:
        loader.return_value.get_agent.return_value = agent
        result = await run_case(case, 1, db, tts=tts)

    assert result.passed, result.failures
    assert result.agents == ["home-steward"]
    assert result.tools == ["home_assistant"]
    assert all("Butler" not in s for s in result.spoken)
    for stage in ("agent_start", "first_token", "first_chunk", "tts_first_byte", "turn_end"):
        assert stage in result.marks
    assert "stt_done" not in result.marks, "no STT ran, so none may be reported"
    tts.synthesize_to_file.assert_awaited_once_with("The kitchen light is on.")


@pytest.mark.asyncio
async def test_run_case_with_live_hearing_anchors_the_trace_at_speech_end(tmp_path):
    import time as _time

    case = {"id": "lights", "room": "home-automation", "utterance": "Are the lights on?", "expect": {}}
    prompts = []

    async def fake_stream(prompt, session):
        prompts.append(prompt)
        yield {"type": "final_response", "text": "Yes.", "response": None}

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)
    now = _time.monotonic()

    async def hear(text):
        return replay.HeardSpeech("Are the lights on?", speech_end_at=now - 0.8, vad_final_at=now - 0.3,
                                  stt_done_at=now - 0.25)

    db = isolate_sessions(str(tmp_path / "replay"))
    with patch("core.loaders.agents_loader.AgentsLoader") as loader:
        loader.return_value.get_agent.return_value = agent
        result = await run_case(case, 1, db, hear=hear, measure_tts=False)

    assert prompts == ["Are the lights on?"]
    assert 450 <= result.marks["vad_final"] <= 550
    assert 500 <= result.marks["stt_done"] <= 600
    assert result.marks["agent_start"] >= result.marks["stt_done"]


@pytest.mark.asyncio
async def test_run_case_fails_when_nothing_is_heard(tmp_path):
    case = {"id": "lights", "room": "home-automation", "utterance": "Are the lights on?", "expect": {}}

    async def hear(text):
        return None

    agent = MagicMock()
    db = isolate_sessions(str(tmp_path / "replay"))
    with patch("core.loaders.agents_loader.AgentsLoader") as loader:
        loader.return_value.get_agent.return_value = agent
        result = await run_case(case, 1, db, hear=hear, measure_tts=False)

    assert not result.passed
    assert result.failures == ["STT heard nothing"]
    agent.execute_stream.assert_not_called()


@pytest.mark.asyncio
async def test_main_exit_code_and_json(tmp_path):
    path = _cases_file(tmp_path, CASES)
    out = tmp_path / "out.json"
    failing = CaseResult(case_id="read", run=1, marks={}, failures=["answered by nobody"])
    with patch.object(replay, "run_case", AsyncMock(return_value=failing)), \
         patch.object(replay, "guard_writes") as guard, \
         patch("core.voice.engines.tts_engine.TTSEngine"):
        code = await replay.main(["--cases", path, "--state-dir", str(tmp_path / "s"), "--out", str(out)])
    assert code == 2
    guard.assert_called_once()
    data = json.loads(out.read_text())
    assert data[0]["case_id"] == "read" and data[0]["passed"] is False


def test_write_guard_refuses_writes_and_lets_reads_through(monkeypatch):
    """The smoke run's 'read-only' dinner question appended to the meal log; the guard stops that."""
    import tools.gog
    from core.loaders.tools_loader import ToolsLoader

    original = MagicMock(return_value=True)
    monkeypatch.setattr(ToolsLoader, "check_permission", original)
    monkeypatch.setattr(tools.gog, "subprocess", tools.gog.subprocess)
    replay.BLOCKED_WRITES.clear()
    replay.guard_writes()
    loader, ctx = ToolsLoader(), MagicMock()

    assert loader.check_permission(ctx, "filesystem", "read", "pkm/wallet/kitchen/MEAL_LOGS.md") is True
    assert loader.check_permission(ctx, "filesystem", "append", "pkm/wallet/kitchen/MEAL_LOGS.md") is False
    assert loader.check_permission(ctx, "home_assistant", "call_service", "light.kitchen") is False
    assert loader.check_permission(ctx, "yfinance", "quote") is True
    assert original.call_count == 2, "only reads and unguarded tools reach the real check"

    refused = tools.gog.subprocess.run(["gog", "calendar", "create", "primary", "--summary", "x"])
    assert refused.returncode == 1 and "read-only" in refused.stderr
    assert [b[0] for b in replay.BLOCKED_WRITES] == ["filesystem", "home_assistant", "gog"]
    replay.BLOCKED_WRITES.clear()
