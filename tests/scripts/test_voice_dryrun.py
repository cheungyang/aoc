"""The voice replay benchmark (scripts/voice_dryrun.py)."""
import json
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts import voice_dryrun as replay
from core.channel.response_parser import AgentResponse
from scripts.voice_dryrun import (
    CASES_PATH,
    CaseResult,
    check_expectations,
    check_speech,
    isolate_sessions,
    load_cases,
    percentile,
    render_report,
    replay_tokens,
    reset_history,
    run_case,
    speech_coverage,
    spoken_numbers,
    stage_table,
)
from tests.helpers import GOLDEN_INPUT, FakeTTS


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
        results[1].speech_missing = True
        report = render_report(results)
        assert "| a | 1 | PASS |" in report and "| a | 2 | FAIL |" in report
        assert "Quality: 1/2 runs passed." in report
        assert "Speech: 1/2 runs came with a <speech> block." in report
        results.append(CaseResult(case_id="b", run=1, marks={}, errored=True, failures=["agent error: 503"]))
        assert "Speech: 1/2 runs came with a <speech> block. (1 failed runs aren't counted.)" in render_report(results)


GOLDEN_SPEECH = (
    "There are five options. Point Reyes is a two hour drive and free to enter, best in the morning fog. "
    "Mount Tam is forty-five minutes with eight dollar parking. Muir Woods is an hour away, fifteen dollars "
    "plus a nine dollar reservation. Stinson Beach is an hour fifteen, free, windy after noon. "
    "Tennessee Valley is forty minutes, free, and stroller friendly."
)


class TestSpeechContract:

    def test_numbers_are_heard_in_digits_or_words(self):
        said = spoken_numbers("Seven thirty on October sixteenth, two and a half hours, forty-five minutes, "
                              "one hundred and five, two thousand five hundred, 1,200 steps, an hour, free")
        assert {"7", "30", "10", "16", "2.5", "45", "105", "2500", "1200", "1", "0"} <= said

    def test_speech_that_keeps_everything_covers_the_reply(self):
        assert speech_coverage(GOLDEN_INPUT, GOLDEN_SPEECH) == []

    def test_a_dropped_item_is_named(self):
        dropped = GOLDEN_SPEECH.replace(
            "Muir Woods is an hour away, fifteen dollars plus a nine dollar reservation. ", "")
        assert speech_coverage(GOLDEN_INPUT, dropped) == ["$9", "Muir Woods"]

    def test_labels_headings_and_greetings_are_not_names(self):
        reply = ("## Chores this week\n"
                 "Good morning Alva! Here they are:\n"
                 "- **Pay the Comcast bill**\n"
                 "  - **Due Date**: Oct 21\n"
                 "  - **Context**: Scheduled for next week, with Sarah.\n"
                 "- **Renew the passport**\n"
                 "  - **Due Date**: Nov 2\n"
                 "  - **Context**: Takes six weeks *(Added last month)*.\n")
        assert replay._required_names(reply) == ["Pay the Comcast bill", "Sarah", "Renew the passport"]

    def test_a_one_off_label_names_its_item(self):
        reply = "* **Amex Gold Dining Credit:** **$10** (Grubhub, Five Guys, Goldbelly)"
        assert replay._required_names(reply) == ["Amex Gold Dining Credit", "Five Guys", "Goldbelly"]

    def test_a_name_said_in_another_form_counts(self):
        reply = "- 🌼 Daisy (Day Planner) and 🚀 Grace (Project Coach)"
        assert speech_coverage(reply, "Daisy does day planning and Grace does project coaching.") == []

    def test_links_ids_years_and_list_numbers_are_not_required(self):
        reply = ("1. Order 1234567 shipped in 2026, see https://x.io/track/42.\n"
                 "2. It arrives Friday at 14:30.")
        assert speech_coverage(reply, "Your order shipped and arrives Friday at two thirty.") == []

    def result(self, **kw):
        base = dict(case_id="c", run=1, marks={}, reply="Mount Tam is 45 minutes away.",
                    speech="Mount Tam is forty-five minutes away.")
        base.update(kw)
        return CaseResult(**base)

    def test_a_spoken_reply_passes(self):
        assert check_speech(self.result()) == ([], [])

    def test_missing_speech_fails(self):
        failures, _ = check_speech(self.result(speech="", speech_missing=True))
        assert failures == ["no <speech> block (the fallback was spoken)"]

    def test_speech_in_the_text_fails(self):
        failures, _ = check_speech(self.result(shown="<speech>Mount Tam</speech> is 45 minutes away."))
        assert failures == ["<speech> reached the text"]

    def test_a_failed_run_is_not_held_to_speech(self):
        assert check_speech(self.result(speech="", errored=True, reply="<speech>Cut off</spee")) == ([], [])
        failures, _ = check_speech(self.result(errored=True, shown="<speech>Cut off"))
        assert failures == ["<speech> reached the text"]

    def test_speech_an_english_voice_cant_say_fails(self):
        failures, _ = check_speech(self.result(reply="Use the **金針菇 (enoki)** first.", speech="Use the 金針菇 first."))
        assert failures == ["speech isn't in English: 金針菇"]

    def test_a_possibly_missed_name_is_a_note_not_a_failure(self):
        failures, notes = check_speech(self.result(speech="It is forty-five minutes away."))
        assert failures == [] and notes == ["speech may have missed: Mount Tam"]

    def test_a_miss_fails_unless_the_speech_defers_to_the_channel(self):
        failures, notes = check_speech(self.result(speech="Mount Tam is close."))
        assert failures == ["speech missed: 45"] and notes == []
        failures, notes = check_speech(self.result(speech="Mount Tam is close; the details are in the channel."))
        assert failures == [] and notes == ["speech missed: 45 (it defers to the channel)"]


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

    raw = "<speech>The kitchen light is on.</speech>The kitchen light is **on**."

    async def fake_stream(prompt, session):
        assert session.get_surface() == "voice" and session.record_memory is False
        yield {"type": "tool_start", "tool_name": "home_assistant", "tool_args": {}}
        yield {"type": "tool_end", "tool_name": "home_assistant", "output": "on"}
        yield {"type": "token", "content": "🛎️ Butler: ", "agent_id": "home-steward", "is_header": True}
        yield {"type": "token", "content": raw[:20], "agent_id": "home-steward"}
        yield {"type": "token", "content": raw[20:], "agent_id": "home-steward"}
        response = AgentResponse.from_string(raw)
        yield {"type": "final_response", "text": response.text, "response": response}

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)
    tts = FakeTTS()
    db = isolate_sessions(str(tmp_path / "replay"))

    with patch("core.loaders.agents_loader.AgentsLoader") as loader:
        loader.return_value.get_agent.return_value = agent
        loader.return_value.get_agent_config.side_effect = lambda agent_id: {
            "home-steward": {"voice": {"name": "en-US-Chirp3-HD-Schedar"}}}.get(agent_id)
        result = await run_case(case, 1, db, tts=tts)

    assert result.passed, result.failures
    assert result.agents == ["home-steward"]
    assert result.tools == ["home_assistant"]
    assert all("Butler" not in s for s in result.spoken)
    for stage in ("agent_start", "first_token", "first_chunk", "tts_first_byte", "turn_end"):
        assert stage in result.marks
    assert "stt_done" not in result.marks, "no STT ran, so none may be reported"
    assert tts.said() == ["The kitchen light is on."]
    assert tts.streams[0].voice.name == "en-US-Chirp3-HD-Schedar", "in Butler's own voice"
    assert result.speech == "The kitchen light is on." and not result.speech_missing
    assert result.shown == "🛎️ Butler: The kitchen light is **on**."


@pytest.mark.asyncio
async def test_run_case_fails_a_reply_without_speech_and_speaks_the_fallback(tmp_path):
    case = {"id": "lights", "room": "home-automation", "utterance": "Are the lights on?", "expect": {}}

    async def fake_stream(prompt, session):
        yield {"type": "token", "content": "The kitchen light is on. The rest are off."}
        response = AgentResponse.from_string("The kitchen light is on. The rest are off.")
        yield {"type": "final_response", "text": response.text, "response": response}

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)
    db = isolate_sessions(str(tmp_path / "replay"))
    with patch("core.loaders.agents_loader.AgentsLoader") as loader:
        loader.return_value.get_agent.return_value = agent
        result = await run_case(case, 1, db, measure_tts=False)

    assert result.speech_missing
    assert result.failures == ["no <speech> block (the fallback was spoken)"]
    assert result.spoken == ["The kitchen light is on. The rest is in the channel."]


@pytest.mark.asyncio
async def test_run_case_shows_a_delegates_error_and_speaks_the_error_line(tmp_path):
    from core.voice.speech.reply import ERROR_LINE

    case = {"id": "thermostat", "room": "home-automation", "utterance": "Thermostat?", "expect": {}}

    async def fake_stream(prompt, session):
        yield {"type": "token", "content": "🛎️ Butler: ", "agent_id": "home-steward", "is_header": True}
        yield {"type": "token", "content": "<speech>Checking the thermostat.", "agent_id": "home-steward"}
        yield {"type": "token", "content": "\n[Error: 503 UNAVAILABLE]", "agent_id": "home-steward", "is_error": True}
        response = AgentResponse.from_string("Checking the thermostat.")
        yield {"type": "final_response", "text": response.text, "response": response}

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)
    db = isolate_sessions(str(tmp_path / "replay"))
    with patch("core.loaders.agents_loader.AgentsLoader") as loader:
        loader.return_value.get_agent.return_value = agent
        result = await run_case(case, 1, db, measure_tts=False)

    assert result.errored and not result.speech_missing
    assert result.failures == ["agent error: [Error: 503 UNAVAILABLE]"]
    assert result.spoken == ["Checking the thermostat.", ERROR_LINE]
    assert "[Error: 503 UNAVAILABLE]" in result.shown


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
         patch("core.voice.engines.tts.TTSEngine"):
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
