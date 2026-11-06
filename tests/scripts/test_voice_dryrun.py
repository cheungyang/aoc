"""The voice replay benchmark (scripts/voice_dryrun.py)."""
import asyncio
import json
import os
from contextlib import contextmanager
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts import voice_dryrun as replay
from core.channel.response_parser import AgentResponse
from core.config.models import FLASH, FLASH_LITE, PRO
from core.runtime.foundation.context import current_execution_context
from core.scheduler.script_runner import write_reason
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


@pytest.fixture(autouse=True)
def service(monkeypatch):
    """The service check's calls, answered at once and fast: no test reaches the
    model through it. A test sets `service.seconds` (per tier, as a dict) to a
    slow time, to an exception for a failing call, or to "stall"."""
    class Service:
        seconds = 0.1
        asked = []

    async def first_chunk(tier):
        Service.asked.append(tier)
        answer = Service.seconds.get(tier, 0.1) if isinstance(Service.seconds, dict) else Service.seconds
        if isinstance(answer, Exception):
            raise answer
        if answer == "stall":
            await asyncio.sleep(3600)
        return answer

    Service.asked = []
    monkeypatch.setattr(replay, "_first_chunk", first_chunk)
    return Service


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

    def test_the_light_first_call_isnt_a_round_trip_of_the_budget(self):
        """The light first call speaks while the agent's own calls run (graphs/voice)."""
        case = {"expect": {"max_model_calls": 2}}
        light = {"thinking": "minimal", "light": True}
        assert check_expectations(case, self.result(model_calls=[light, {"thinking": "low"}, {"thinking": "low"}])) == []
        assert check_expectations(case, self.result(model_calls=[light] + [{"thinking": "low"}] * 3)) == [
            "3 model calls, budget 2"]


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

    def test_thinking_picks_are_reported_with_their_caps(self):
        result = CaseResult(case_id="a", run=1, marks={}, thinking_picks=[
            {"kind": "thinking_pick", "level": "high", "applied": "medium"},
            {"kind": "thinking_pick", "level": "minimal", "applied": "minimal"},
        ])
        report = render_report([result])
        assert "picked medium (asked high); picked minimal" in report


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
                 "Good morning Sam! Here they are:\n"
                 "- **Pay the internet bill**\n"
                 "  - **Due Date**: Oct 21\n"
                 "  - **Context**: Scheduled for next week, with Pat.\n"
                 "- **Renew the passport**\n"
                 "  - **Due Date**: Nov 2\n"
                 "  - **Context**: Takes six weeks *(Added last month)*.\n")
        assert replay._required_names(reply) == ["Pay the internet bill", "Pat", "Renew the passport"]

    def test_a_one_off_label_names_its_item(self):
        reply = "* **Card A Dining Credit:** **$10** (Pizzeria, Sample Diner, Teahouse)"
        assert replay._required_names(reply) == ["Card A Dining Credit", "Sample Diner", "Teahouse"]

    def test_a_name_said_in_another_form_counts(self):
        reply = "- 🌼 Daisy (Day Planner) and 🚀 Grace (Project Coach)"
        assert speech_coverage(reply, "Daisy does day planning and Grace does project coaching.") == []

    def test_links_ids_years_and_list_numbers_are_not_required(self):
        reply = ("1. Order 1234567 shipped in 2026, see https://x.io/track/42.\n"
                 "2. It arrives Friday at 14:30.")
        assert speech_coverage(reply, "Your order shipped and arrives Friday at two thirty.") == []

    def test_a_thousands_separator_is_part_of_one_number(self):
        """market-movers on Nov 3: a price of $1,409.38 ended the matrix run."""
        reply = "NVDA is at $1,409.38, the S&P 500 at 6,712.15, and 1,250 shares are yours."
        speech = ("Nvidia is at fourteen hundred and nine dollars and thirty-eight cents, the S and P 500 at "
                  "6,712.15, and twelve hundred fifty shares are yours.")
        assert replay.missed_numbers(reply, speech) == []
        assert replay.missed_numbers(reply, "Nvidia is up, and so is the S and P.") == [
            "$1,409.38", "500", "6,712.15", "1,250"]
        assert check_speech(self.result(reply=reply, speech=speech)) == ([], [])

    def test_what_isnt_one_number_isnt_required_and_breaks_nothing(self):
        """A version, an address, a ratio: neither side of the check reads them as numbers."""
        assert replay.missed_numbers("Update to 1.2.3 on 10.0.0.1, and set the screen to 16:9.",
                                     "Update it and set the screen.") == []
        assert spoken_numbers("1.2.3, 10.0.0.1 and 1.5:30") == set()

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

    def test_a_reply_that_copies_the_message_stamp_fails(self):
        failures, _ = check_speech(self.result(reply="[Fri 06:43] Mount Tam is 45 minutes away."))
        assert "the reply copies a message time stamp" in failures
        failures, _ = check_speech(self.result(speech="[Fri 06:43] Mount Tam is forty-five minutes away."))
        assert "the reply copies a message time stamp" in failures

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

    def test_the_speech_rules_let_numbers_defer_in_words_the_check_takes(self):
        """The voice rules let speech leave a run of numbers to the channel: the
        words they ask for must be what this check takes as deferring."""
        from core.prompts.base import load_template
        rules = load_template("voice/_speech_rules").replace("\n  ", " ").split("\n")
        (numbers,) = [rule for rule in rules if rule.startswith("- Numbers")]
        assert replay.DEFERS_RE.search(numbers), numbers


class TestIsolation:

    def test_stores_point_at_the_throwaway_db(self, tmp_path):
        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
        from core.runtime.foundation.jobs import JobManager
        from core.storage.sessions import SessionStore
        from core.config.settings import Config
        db = isolate_sessions(str(tmp_path / "replay"))
        assert SessionStore().path == db
        assert SqliteCheckpointer().store.path == db
        assert Config().memory_db_path == db
        assert JobManager()._store.path == db

    def test_history_reset_and_token_count(self, tmp_path):
        from core.storage.sessions import SessionStore
        db = str(tmp_path / "memory.db")
        store = SessionStore(db)
        store.append_token_usage("home-steward:tool:home-automation", "m", 100, 20, 0.0)
        store.append_message("home-steward:tool:home-automation", "user", "hi")
        with store.connect() as conn:
            conn.execute('INSERT INTO "ctx_home_steward_tool_home_automation" (entry_type, created_at) VALUES (\'checkpoint\', 0)')

        assert replay_tokens(0, db) == {"input": 100, "output": 20}
        reset_history(db)
        with store.connect() as conn:
            kinds = [r[0] for r in conn.execute('SELECT entry_type FROM "ctx_home_steward_tool_home_automation"')]
        assert sorted(kinds) == ["message", "token"]

    def test_a_missing_db_is_left_missing(self, tmp_path):
        db = str(tmp_path / "replay" / "memory.db")
        assert replay_tokens(0, db) == {"input": 0, "output": 0}
        reset_history(db)
        assert not os.path.exists(os.path.dirname(db))

    def test_the_replay_runs_without_pruning_and_puts_it_back(self):
        from core.runtime.hooks import PruneHook, turn_hooks

        before = list(turn_hooks())
        with replay.without_pruning():
            assert not any(isinstance(hook, PruneHook) for hook in turn_hooks())
            assert len(turn_hooks()) == len(before) - 1
        assert turn_hooks() == before


THREAD = "agent-designer:tool:agent-management"


def _said(stamp, words):
    """A turn's words as the delegated agent stores them: stamped, with the caller, around main's stamped words."""
    return f"[Thu {stamp}] <caller>main</caller>\n[Thu {stamp}] {words}"


def _prod_db(tmp_path):
    """A production memory.db with one conversation: Aki's, in its home channel.

    Its newest checkpoint holds a summary, then turns: two spoken and answered,
    one typed, one cut off by the next, whose answer made a tool call first.
    The transcript rows say which came by voice, as the runtime logs them."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
    from core.storage.sessions import SessionStore

    path = str(tmp_path / "prod" / "memory.db")
    messages = [
        SystemMessage("Summary of the conversation so far."),
        HumanMessage(_said("09:00", "Morning, Aki.")),
        AIMessage("<speech>Morning!</speech>Good morning."),
        HumanMessage(_said("09:01", "Draft the plan for the new agent.")),
        AIMessage("<speech>Drafting it.</speech>Here's the plan."),
        HumanMessage(_said("09:02", "Which agent handles meals?")),
        AIMessage("", tool_calls=[{"name": "agent_list", "args": {}, "id": "t1"}]),
        ToolMessage("meal-planner", tool_call_id="t1"),
        AIMessage([{"type": "text", "text": "<speech>Mia does.</speech>Mia, the meal planner."
                                            "<system_memory_log>Asked about meals.</system_memory_log>"}]),
        HumanMessage(_said("09:03", "And who")),
        HumanMessage(_said("09:03", "And who plans my day?")),
        AIMessage("<speech>Daisy plans your day.</speech>Daisy."),
        HumanMessage(_said("09:04", "Is the plan saved?")),  # spoken, then cut off after a tool call
        AIMessage("", tool_calls=[{"name": "file_read", "args": {}, "id": "t2"}]),
        ToolMessage("saved", tool_call_id="t2"),
    ]
    checkpoint = {"v": 4, "id": "cp-1", "ts": "2026-11-05T17:03:00+00:00",
                  "channel_values": {"messages": messages, "route": "agent-designer"},
                  "channel_versions": {"messages": 9}, "versions_seen": {}, "updated_channels": ["messages"]}
    saver = SqliteCheckpointer(path)
    older = {**checkpoint, "id": "cp-0", "channel_values": {"messages": messages[:6]}}
    saver.put({"configurable": {"thread_id": THREAD}}, older, {"source": "loop", "step": 5, "parents": {}}, {})
    saver.put({"configurable": {"thread_id": THREAD}}, checkpoint,
              {"source": "loop", "step": 7, "parents": {}, "run_id": "r-7"}, {"messages": 9})
    store = SessionStore(path)
    for stamp, words, surface in (("09:00", "Morning, Aki.", "voice"),
                                  ("09:01", "Draft the plan for the new agent.", "text"),
                                  ("09:02", "Which agent handles meals?", "voice"),
                                  ("09:03", "And who", None),  # cut off before its call ended: no token row
                                  ("09:03", "And who plans my day?", "voice"),
                                  ("09:04", "Is the plan saved?", "voice")):
        store.append_message(THREAD, "user", f"<caller>main</caller>\n[Thu {stamp}] {words}")
        store.append_message(THREAD, "ai", "...")
        if surface:
            store.append_token_usage(THREAD, "gemini", 100, 10, 0.0, surface=surface)
            store.append_token_usage(THREAD, "gemini", 100, 10, 0.0, surface="tool")  # a later call of the turn
    return path, messages


@contextmanager
def _agents(home="agent-management"):
    """The loader as live_cases asks it: Aki, by id or name, at home in `home`."""
    configs = {"agent-designer": {"name": "Aki", "channels": ["agent-management"]}}
    with patch("core.loaders.agents_loader.AgentsLoader") as loader, \
         patch("core.runtime.foundation.routing.home_channel", return_value=home):
        loader.return_value.get_agent_config.side_effect = configs.get
        loader.return_value.list_agent_ids.return_value = list(configs)
        yield loader


class TestLiveCases:

    def test_the_turns_you_spoke_and_it_answered_become_cases(self, tmp_path):
        path, messages = _prod_db(tmp_path)
        with _agents():
            cases = replay.live_cases(["aki"], db_path=path)

        # The first spoken turn has no conversation before it, and the typed
        # and the cut-off ones weren't spoken and answered.
        assert [case["utterance"] for case in cases] == ["Which agent handles meals?", "And who plans my day?"]
        meals, day = cases
        assert meals["room"] == "agent-management" and meals["expect"] == {"agent": "agent-designer"}
        assert meals["id"].startswith("live-agent-designer-") and meals["id"] != day["id"]
        assert meals["context"] == ("User: Morning, Aki.\nAki: Morning! Good morning.\n"
                                    "User: Draft the plan for the new agent.\nAki: Drafting it. Here's the plan.")
        assert day["context"].splitlines() == [
            "User: Morning, Aki.", "Aki: Morning! Good morning.", "User: Draft the plan for the new agent.",
            "Aki: Drafting it. Here's the plan.", "User: Which agent handles meals?",
            "Aki: Mia does. Mia, the meal planner.", "User: And who"], \
            "no tool call, no memory log, and the history as the agent had it"

        history = meals["history"]
        assert history["thread_id"] == THREAD
        assert history["checkpoint"]["channel_values"]["messages"] == messages[:5], "cut just before the words"
        assert history["checkpoint"]["channel_values"]["route"] == "agent-designer"
        assert history["metadata"] == {"source": "loop", "step": 7, "parents": {}}
        assert history["new_versions"] == {"messages": 9}
        assert len(day["history"]["checkpoint"]["channel_values"]["messages"]) == 10

    def test_the_latest_turns_and_the_ids_asked_for(self, tmp_path):
        path, _ = _prod_db(tmp_path)
        with _agents():
            every = replay.live_cases(["agent-designer"], db_path=path)
            assert replay.live_cases(["Aki"], turns=1, db_path=path) == every[1:]
            assert replay.live_cases(["Aki"], db_path=path, only=[every[1]["id"]]) == every[1:]
            assert replay.live_cases(["Aki"], db_path=path) == every, "the same conversation, the same ids"
            with pytest.raises(ValueError, match="unknown case id"):
                replay.live_cases(["Aki"], db_path=path, only=["live-agent-designer-0000"])

    def test_production_is_only_read(self, tmp_path):
        import hashlib

        path, _ = _prod_db(tmp_path)
        before = hashlib.sha256(open(path, "rb").read()).hexdigest()
        with _agents(), patch("core.storage.sessions.SessionStore.connect",
                              side_effect=AssertionError("opened for writing")):
            assert len(replay.live_cases(["Aki"], db_path=path)) == 2
        assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before

    def test_names_and_places_it_cant_take_a_conversation_from(self, tmp_path, capsys):
        path, _ = _prod_db(tmp_path)
        with _agents():
            with pytest.raises(ValueError, match="no agent has the id or name 'Zed'"):
                replay.live_cases(["Zed"], db_path=path)
            with pytest.raises(ValueError, match="no memory.db at"):
                replay.live_cases(["Aki"], db_path=str(tmp_path / "nowhere" / "memory.db"))
        with _agents(home=None), pytest.raises(ValueError, match="answers no channel of its own"):
            replay.live_cases(["Aki"], db_path=path)
        with _agents(home="elsewhere"):
            assert replay.live_cases(["Aki"], db_path=path) == []
        assert "agent-designer: 0 turns you spoke, from agent-designer:tool:elsewhere" in capsys.readouterr().err

    def test_a_case_runs_on_its_conversation_and_none_on_none(self, tmp_path):
        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
        from core.storage.sessions import SessionStore

        path, messages = _prod_db(tmp_path)
        with _agents():
            (meals, _) = replay.live_cases(["Aki"], db_path=path)
        db = isolate_sessions(str(tmp_path / "replay"))
        reset_history(db)
        replay.seed_history(meals, db)
        stored = SqliteCheckpointer(db).get_tuple({"configurable": {"thread_id": THREAD, "checkpoint_ns": ""}})
        assert [m.content for m in stored.checkpoint["channel_values"]["messages"]] == [
            m.content for m in messages[:5]]
        assert stored.metadata["step"] == 7
        assert len(meals["history"]["checkpoint"]["channel_values"]["messages"]) == 5, "the case's own is kept"

        reset_history(db)
        replay.seed_history({"id": "read", "room": "home-automation", "utterance": "Lights?"}, db)
        assert SessionStore(db).checkpoint("ctx_agent_designer_tool_agent_management") == (None, [])

    def test_stored_words_read_as_said(self):
        from langchain_core.messages import AIMessage, HumanMessage

        assert replay.user_words(_said("09:03", "And who plans my day?")) == "And who plans my day?"
        assert replay.user_words([{"type": "text", "text": "[Thu 09:03] "}, "Lights?"]) == "Lights?"
        assert replay.agent_words("<speech>On it.</speech>Done.<system_memory_log>Noted.</system_memory_log>") \
            == "On it. Done."
        assert replay.agent_words("<speech>Add one.</speech>End each reply with a <system_memory_log> block.") \
            == "Add one. End each reply with a <system_memory_log> block.", "a tag named in passing is text"
        assert replay.agent_words("") == ""
        context = replay.render_context([HumanMessage("word " * 200)], "Aki")
        assert len(context) == len("User: ") + replay.LIVE_CONTEXT_CHARS and context.endswith("…")
        turns = [message for n in range(6) for message in (HumanMessage(f"Q{n}"), AIMessage(f"A{n}"))]
        lines = replay.render_context(turns, "Aki").splitlines()
        assert len(lines) == replay.LIVE_CONTEXT_MESSAGES and lines[-1] == "Aki: A5" and lines[0] == "User: Q2"


@pytest.mark.asyncio
async def test_run_case_starts_a_live_case_on_its_conversation(tmp_path):
    """The turn finds its history where the agent's graph reads it, and the
    result carries the conversation for the judge."""
    from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer

    path, _ = _prod_db(tmp_path)
    with _agents():
        (meals, _) = replay.live_cases(["Aki"], db_path=path)
    found = []
    raw = "<speech>Mia handles meals.</speech>Mia, the meal planner."

    async def fake_stream(prompt, session):
        stored = SqliteCheckpointer().get_tuple({"configurable": {"thread_id": THREAD, "checkpoint_ns": ""}})
        found.append(len(stored.checkpoint["channel_values"]["messages"]))
        yield {"type": "token", "content": raw, "agent_id": "agent-designer"}
        response = AgentResponse.from_string(raw)
        yield {"type": "final_response", "text": response.text, "response": response}

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)
    db = isolate_sessions(str(tmp_path / "replay"))
    with patch("core.loaders.agents_loader.AgentsLoader") as loader:
        loader.return_value.get_agent.return_value = agent
        loader.return_value.get_agent_config.return_value = {}
        result = await run_case(meals, 1, db, measure_tts=False)

    assert found == [5]
    assert result.passed, result.failures
    assert result.context == meals["context"] and result.transcript == "Which agent handles meals?"


class TestLiveReplay:

    @pytest.mark.asyncio
    async def test_production_is_read_before_the_stores_point_away_and_pruning_is_off(self, tmp_path, monkeypatch):
        from core.runtime.hooks import PruneHook, turn_hooks

        monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "prod" / "memory.db"))
        asked, hooks = [], []
        case = {"id": "live-agent-designer-1", "room": "agent-management", "utterance": "Who plans my day?",
                "expect": {"agent": "agent-designer"}, "context": "User: Hi.\nAki: Hi!"}

        def fake_live_cases(names, turns, only=None):
            asked.append((names, turns, only, os.environ["MEMORY_DB_PATH"]))
            return [case]

        async def fake_run_case(case, run, db_path, **kwargs):
            hooks.append([type(hook) for hook in turn_hooks()])
            return CaseResult(case_id=case["id"], run=run, marks={}, context=case["context"])

        out = tmp_path / "live.json"
        with patch.object(replay, "live_cases", fake_live_cases), patch.object(replay, "run_case", fake_run_case), \
             patch.object(replay, "guard_writes"), patch("core.channel.voice.engines.tts.TTSEngine"):
            code = await replay.main(["--live", "Ted, Aki", "--live-turns", "3", "--light", "off",
                                      "--state-dir", str(tmp_path / "s"), "--out", str(out)])

        assert code == 0
        assert asked == [(["Ted", "Aki"], 3, None, str(tmp_path / "prod" / "memory.db"))]
        assert hooks and PruneHook not in hooks[0]
        assert any(isinstance(hook, PruneHook) for hook in turn_hooks()), "put back once the replay is done"
        assert json.loads(out.read_text())[0]["context"] == "User: Hi.\nAki: Hi!"

    @pytest.mark.asyncio
    async def test_a_name_it_cant_take_or_no_turns_run_nothing(self, tmp_path):
        with patch.object(replay, "run_case") as run:
            with patch.object(replay, "live_cases", side_effect=ValueError("no agent has the id or name 'Zed'")), \
                 pytest.raises(SystemExit):
                await replay.main(["--live", "Zed"])
            with patch.object(replay, "live_cases") as live, pytest.raises(SystemExit):
                await replay.main(["--live", "Aki", "--live-turns", "0"])
            live.assert_not_called()
            with patch.object(replay, "live_cases", return_value=[]):
                assert await replay.main(["--live", "Aki"]) == 1
        run.assert_not_called()


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
async def test_a_gap_in_the_speech_checks_is_noted_and_the_benchmark_goes_on(tmp_path):
    """The checks read whatever the model wrote; one they can't read is noted
    on its run, instead of ending a benchmark hours in."""
    case = {"id": "market-movers", "room": "market-scout", "utterance": "How are my stocks?", "expect": {}}
    raw = "<speech>Up a little today.</speech>Up a little today."

    async def fake_stream(prompt, session):
        yield {"type": "token", "content": raw}
        response = AgentResponse.from_string(raw)
        yield {"type": "final_response", "text": response.text, "response": response}

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)
    db = isolate_sessions(str(tmp_path / "replay"))
    broken = ValueError("could not convert string to float: '1,409.38'")
    with patch("core.loaders.agents_loader.AgentsLoader") as loader, \
         patch.object(replay, "check_speech", side_effect=broken):
        loader.return_value.get_agent.return_value = agent
        result = await run_case(case, 1, db, measure_tts=False)

    assert result.passed, result.failures
    assert result.notes == ["speech check skipped: ValueError: could not convert string to float: '1,409.38'"]


@pytest.mark.asyncio
async def test_run_case_shows_a_delegates_error_and_speaks_the_error_line(tmp_path):
    from core.channel.voice.speech.reply import ERROR_LINE

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
         patch("core.channel.voice.engines.tts.TTSEngine"):
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
    assert loader.check_permission(ctx, "home_assistant", "call_service", "light.kitchen", probe=True) is True
    assert original.call_count == 3, "a probe (the entity index asking) passes through and isn't a write"

    refused = tools.gog.subprocess.run(["gog", "calendar", "create", "primary", "--summary", "x"])
    assert refused.returncode == 1 and "read-only" in refused.stderr
    assert [b[0] for b in replay.BLOCKED_WRITES] == ["filesystem", "home_assistant", "gog"]
    replay.BLOCKED_WRITES.clear()


def test_every_tool_that_declares_read_actions_is_guarded():
    """A new write tool, in tools/ or an agent's own tools/, declares READ_ACTIONS and is covered."""
    import importlib
    import importlib.util
    import re as regex

    from core.loaders.tools_loader import ToolsLoader

    loader = ToolsLoader()
    allowed = replay.read_only_actions()
    declared = []
    for tool in sorted(set(loader._discover_tools()) | set(loader._agent_tools())):
        origin = importlib.util.find_spec(loader.module_path(tool)).origin
        with open(origin, encoding="utf-8") as f:
            if regex.search(r"^READ_ACTIONS\b", f.read(), regex.MULTILINE):
                declared.append(tool)
    assert "google_health" in declared
    for tool in declared:
        assert allowed[tool] == set(importlib.import_module(loader.module_path(tool)).READ_ACTIONS), tool


# --- the model x thinking benchmark ---------------------------------------------------------

def test_python_runs_the_benchmark_without_yes():
    assert write_reason(replay.__file__, ["--matrix", "--runs", "3", "--rpm", "20", "--out", "results.json"]) == ""
    assert write_reason(replay.__file__, ["--combo", "FLASH:low", "--allow-mutations"]) != ""


class TestCombos:

    def test_parsing_and_labels(self):
        assert replay.parse_combo("flash_lite:MINIMAL") == replay.Combo("FLASH_LITE", "minimal")
        assert replay.parse_combo("FLASH-LITE:minimal").label == "FLASH_LITE:minimal"
        assert replay.parse_combo("FLASH").label == "FLASH:prod", "production's thinking"
        assert replay.parse_combo("prod:low").label == "prod:low", "each agent's own model"
        assert replay.parse_combo("prod") == replay.Combo() and replay.Combo().label == "prod"

    def test_only_the_repos_tiers_and_the_levels_each_tier_takes(self):
        with pytest.raises(ValueError, match="unknown tier 'TURBO'"):
            replay.parse_combo("TURBO:low")
        with pytest.raises(ValueError, match="unknown tier 'IMAGE'"):
            replay.parse_combo("IMAGE:low")
        with pytest.raises(ValueError, match="unknown thinking level 'max'"):
            replay.parse_combo("FLASH:max")
        with pytest.raises(ValueError, match="PRO doesn't take minimal thinking: use low, medium, high"):
            replay.parse_combo("PRO:minimal")

    def test_what_a_replay_asks_for(self):
        def labels(**kwargs):
            return [combo.label for combo in replay.combos_from(**kwargs)]

        assert labels() == ["prod"]
        assert labels(combos=["FLASH:low,PRO:low", "flash:low"]) == ["FLASH:low", "PRO:low"]
        assert labels(model="FLASH", thinking="low") == ["FLASH:low"]
        assert labels(thinking="medium") == ["prod:medium"]
        assert labels(use_matrix=True) == ["FLASH_LITE:minimal", "FLASH_LITE:low", "FLASH_LITE:medium",
                                           "FLASH:minimal", "FLASH:low", "FLASH:medium", "PRO:low", "PRO:medium",
                                           "prod"]
        assert replay.REFERENCE == "PRO:low" and replay.REFERENCE in labels(use_matrix=True)

    @pytest.mark.asyncio
    async def test_a_bad_combo_is_refused_before_anything_runs(self, tmp_path):
        with patch.object(replay, "run_case") as run, pytest.raises(SystemExit):
            await replay.main(["--cases", _cases_file(tmp_path, CASES), "--combo", "PRO:minimal"])
        run.assert_not_called()

    def test_a_case_run_goes_on_every_combo_with_the_first_place_rotating(self):
        combos = replay.combos_from(combos=["FLASH:low,PRO:low,prod"])
        assert [c.label for c in replay.rotated(combos, 0)] == ["FLASH:low", "PRO:low", "prod"]
        assert [c.label for c in replay.rotated(combos, 1)] == ["PRO:low", "prod", "FLASH:low"]
        assert [c.label for c in replay.rotated(combos, 3)] == ["FLASH:low", "PRO:low", "prod"]

    def test_a_light_model_goes_in_the_label(self):
        """So runs with a light call never resume as, or get judged as, the reference's."""
        assert replay.parse_light("flash_lite:MINIMAL") == "FLASH_LITE:minimal"
        assert replay.parse_light("off") is None and replay.parse_light("") is None
        with pytest.raises(ValueError, match="PRO doesn't take 'minimal'"):
            replay.parse_light("PRO:minimal")
        assert replay.Combo(light="FLASH_LITE:minimal").label == "prod+light:FLASH_LITE:minimal"
        assert replay.Combo("PRO", "low", "FLASH:minimal").label == "PRO:low+light:FLASH:minimal"
        assert replay.Combo("PRO", "low").label == replay.REFERENCE

    def test_each_combo_goes_with_each_light_model(self):
        def labels(**kwargs):
            return [combo.label for combo in replay.combos_from(**kwargs)]

        assert labels(combos=["prod"], lights=["FLASH_LITE:minimal", "FLASH:minimal"]) == [
            "prod+light:FLASH_LITE:minimal", "prod+light:FLASH:minimal"]
        assert labels(combos=["PRO:low"], lights=[None, "FLASH_LITE:minimal"]) == [
            "PRO:low", "PRO:low+light:FLASH_LITE:minimal"]
        assert labels(lights=[None]) == labels() == ["prod"]

    @pytest.mark.asyncio
    async def test_a_bad_light_model_is_refused_before_anything_runs(self, tmp_path):
        with patch.object(replay, "run_case") as run, pytest.raises(SystemExit):
            await replay.main(["--cases", _cases_file(tmp_path, CASES), "--light", "FLASH_LITE:max"])
        run.assert_not_called()


def _context(agent_id, **kwargs):
    """A context as stream_delegate mints one for the room's agent: no model or level of its own."""
    from core.runtime.foundation.sessions import SessionManager

    return SessionManager().get_session(agent_id=agent_id, source="tool", channel="home-automation",
                                        surface="voice", **kwargs)


@contextmanager
def _running(ctx):
    token = current_execution_context.set(ctx)
    try:
        yield ctx
    finally:
        current_execution_context.reset(token)


@pytest.fixture
def builds(monkeypatch):
    """GraphBuilder's chat_model, recording each (agent, ctx.model, agent.json model) it's asked for."""
    from core.runtime import graph_builder

    built = []
    monkeypatch.setattr(graph_builder, "chat_model",
                        lambda ctx, config: built.append((ctx.agent_id, ctx.model, config.get("model"))))
    return built


class TestComboScope:

    def test_it_reaches_the_delegated_agents_own_context_and_spares_workers(self, builds):
        from core.runtime import graph_builder
        from core.runtime.model import retry
        from core.runtime.model.thinking import PICK_KEY

        before = graph_builder.chat_model, retry.resolve_thinking_level
        specialist, worker = _context("home-steward"), _context("graph-worker", stateless=True)
        with replay.combo_scope(replay.Combo("FLASH_LITE", "minimal")):
            graph_builder.chat_model(specialist, {"model": "FLASH"})
            graph_builder.chat_model(worker, {"model": "FLASH", "stateless": True})
            with _running(specialist):
                specialist.turn_hints[PICK_KEY] = "high"  # the model asked for more
                assert retry.resolve_thinking_level(FLASH_LITE) == "minimal"
                assert PICK_KEY not in specialist.turn_hints, "the pick is still taken, as in production"
            with _running(worker):
                assert retry.resolve_thinking_level(FLASH) is None, "a worker keeps its model's own default"
        assert builds == [("home-steward", "FLASH_LITE", "FLASH"), ("graph-worker", None, "FLASH")]
        assert (graph_builder.chat_model, retry.resolve_thinking_level) == before

    def test_a_level_the_model_doesnt_take_is_raised_to_the_nearest(self):
        from core.runtime.model import retry

        with replay.combo_scope(replay.Combo(None, "minimal")), _running(_context("goal-setter")):
            assert retry.resolve_thinking_level(PRO) == "low"
            assert retry.resolve_thinking_level(FLASH) == "minimal"

    def test_the_real_model_wrapper_is_built_and_sent_on_the_combo(self, monkeypatch):
        """The real chat_model and model wrapper, short of sending the request."""
        import contextvars

        from core.runtime import graph_builder
        from core.runtime.model import chat
        from core.runtime.model.retry import RetryingChatGoogleGenerativeAI
        from core.runtime.observability.turn_trace import start_turn_trace

        monkeypatch.setattr(chat, "RetryingChatGoogleGenerativeAI",
                            lambda **kwargs: RetryingChatGoogleGenerativeAI(google_api_key="test", **kwargs))
        specialist = _context("home-steward")

        def turn():
            trace = start_turn_trace("main", 1)
            with replay.combo_scope(replay.Combo("FLASH_LITE", "minimal")):
                llm = graph_builder.chat_model(specialist, {"model": "FLASH"})
                with _running(specialist):
                    sent = llm._with_thinking({})
            return llm, sent, trace

        llm, sent, trace = contextvars.copy_context().run(turn)
        assert (llm.model, sent["thinking_level"]) == (FLASH_LITE, "minimal")
        calls = [(e["agent"], e["model"], e["thinking"]) for e in trace.events if e["kind"] == "model_call"]
        assert calls == [("home-steward", FLASH_LITE, "minimal")]
        assert replay.combo_misses(replay.Combo("FLASH_LITE", "minimal"),
                                   [e for e in trace.events if e["kind"] == "model_call"]) == []

    def test_each_forced_model_gets_agents_of_its_own(self, monkeypatch):
        from core.loaders.agents_loader import AgentsLoader

        monkeypatch.setattr(replay, "_AGENTS_BY_MODEL", {})
        loader = AgentsLoader()
        prod = loader.get_agent("home-steward")
        with replay.combo_scope(replay.Combo("FLASH", "low")):
            flash = loader.get_agent("home-steward")
        with replay.combo_scope(replay.Combo("FLASH", "medium")):
            assert loader.get_agent("home-steward") is flash, "graphs are built once per model"
        with replay.combo_scope(replay.Combo(None, "low")):
            assert loader.get_agent("home-steward") is prod, "a level is sent per call: the same graphs"
        assert flash is not prod and loader.get_agent("home-steward") is prod

    def test_it_replaces_what_every_agent_model_call_goes_through(self):
        """Were either moved, a combo would quietly run production's model: combo_misses
        fails such runs on the NAS, and this fails the change here."""
        import inspect
        import re as regex

        from core.runtime import graph_builder
        from core.runtime.model import retry

        assert regex.search(r"(?<![\w.])chat_model\(ctx, config\)",
                            inspect.getsource(graph_builder.GraphBuilder.build_graph))
        assert regex.search(r"(?<![\w.])resolve_thinking_level\(self\.model\)",
                            inspect.getsource(retry.RetryingChatGoogleGenerativeAI._with_thinking))

    def test_a_call_off_the_combo_fails_the_run(self):
        calls = [{"agent": "home-steward", "model": FLASH, "thinking": "low"},
                 {"agent": "graph-worker", "model": FLASH_LITE, "thinking": None}]
        assert replay.combo_misses(replay.Combo("FLASH", "low"), calls) == []
        assert replay.combo_misses(replay.Combo("PRO", "low"), calls) == [
            f"not on PRO:low: home-steward ran {FLASH}"]
        assert replay.combo_misses(replay.Combo(None, "medium"), calls) == [
            "not on prod:medium: home-steward thought at low"]
        assert replay.combo_misses(replay.Combo(), calls) == []

    def test_a_light_call_is_held_to_the_light_model(self):
        calls = [{"agent": "home-steward", "model": FLASH_LITE, "thinking": "minimal", "light": True},
                 {"agent": "home-steward", "model": PRO, "thinking": "low"}]
        assert replay.combo_misses(replay.Combo("PRO", "low", "FLASH_LITE:minimal"), calls) == []
        assert replay.combo_misses(replay.Combo(light="FLASH_LITE:minimal"), calls) == []
        assert replay.combo_misses(replay.Combo("PRO", "low"), calls) == [
            "not on PRO:low: home-steward made a light call"]
        assert replay.combo_misses(replay.Combo("PRO", "low", "FLASH:minimal"), calls) == [
            f"not on PRO:low+light:FLASH:minimal: home-steward's light call ran {FLASH_LITE} at minimal"]

    def test_the_light_setting_holds_for_the_run_only(self):
        from core.config.settings import Config

        with Config().overriding(voice_light_model="FLASH:minimal"):
            with replay.light_scope("FLASH_LITE:minimal"):
                assert Config().voice_light_model == "FLASH_LITE:minimal"
            with replay.light_scope(None):
                assert Config().voice_light_model == "off"
            assert Config().voice_light_model == "FLASH:minimal"


@pytest.mark.asyncio
async def test_run_combo_labels_the_run_and_checks_its_calls(tmp_path):
    from core.runtime.observability.turn_trace import current_turn_trace

    case = {"id": "lights", "room": "home-automation", "utterance": "Are the lights on?", "expect": {}}
    raw = "<speech>The kitchen light is on.</speech>The kitchen light is on."

    async def fake_stream(prompt, session):
        trace = current_turn_trace()  # as the model wrapper records a call
        trace.event("model_call", call=1, agent="home-steward", model=FLASH, thinking="low")
        trace.event("model_reply", call=1, phase="first_chunk")
        yield {"type": "token", "content": raw, "agent_id": "home-steward"}
        response = AgentResponse.from_string(raw)
        yield {"type": "final_response", "text": response.text, "response": response}

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)
    db = isolate_sessions(str(tmp_path / "replay"))
    with patch("core.loaders.agents_loader.AgentsLoader") as loader:
        loader.return_value.get_agent.return_value = agent
        loader.return_value.get_agent_config.return_value = {}
        held = await replay.run_combo(case, 1, replay.Combo("FLASH", "low"), db, measure_tts=False)
        missed = await replay.run_combo(case, 1, replay.Combo("PRO", "low"), db, measure_tts=False)

    assert held.passed, held.failures
    assert (held.combo, held.room) == ("FLASH:low", "home-automation")
    assert [(e["call"], e["phase"]) for e in held.model_replies] == [(1, "first_chunk")]
    assert missed.failures == [f"not on PRO:low: home-steward ran {FLASH}"]


@pytest.mark.asyncio
async def test_run_combo_runs_on_its_light_model(tmp_path):
    from core.config.settings import Config
    from core.runtime.observability.turn_trace import current_turn_trace

    case = {"id": "lights", "room": "home-automation", "utterance": "Are the lights on?", "expect": {}}
    raw = "<speech>The kitchen light is on.</speech>The kitchen light is on."
    seen = []

    async def fake_stream(prompt, session):
        seen.append(Config().voice_light_model)  # what GraphBuilder reads
        trace = current_turn_trace()
        trace.event("model_call", call=1, agent="home-steward", model=FLASH_LITE, thinking="minimal", light=True)
        trace.event("handover", agent="home-steward")
        trace.event("model_call", call=2, agent="home-steward", model=FLASH, thinking="low")
        yield {"type": "token", "content": raw, "agent_id": "home-steward"}
        response = AgentResponse.from_string(raw)
        yield {"type": "final_response", "text": response.text, "response": response}

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)
    db = isolate_sessions(str(tmp_path / "replay"))
    with patch("core.loaders.agents_loader.AgentsLoader") as loader:
        loader.return_value.get_agent.return_value = agent
        loader.return_value.get_agent_config.return_value = {}
        light = await replay.run_combo(case, 1, replay.Combo(light="FLASH_LITE:minimal"), db, measure_tts=False)
        off = await replay.run_combo(case, 1, replay.Combo(), db, measure_tts=False)

    assert seen == ["FLASH_LITE:minimal", "off"]
    assert light.passed and light.combo == "prod+light:FLASH_LITE:minimal", light.failures
    assert [(e["kind"], e["agent"]) for e in light.light_events] == [("handover", "home-steward")]
    assert off.failures == ["not on prod: home-steward made a light call"]


@pytest.mark.asyncio
async def test_a_run_whose_light_call_nothing_followed_fails(tmp_path):
    """The light call only says the first line: the answer is the agent's model's."""
    from core.runtime.observability.turn_trace import current_turn_trace

    case = {"id": "thanks", "room": "home-automation", "utterance": "Thanks!", "expect": {}}
    raw = "<speech>You're welcome!</speech>"

    async def fake_stream(prompt, session):
        current_turn_trace().event("model_call", call=1, agent="home-steward", model=FLASH_LITE,
                                   thinking="minimal", light=True)
        yield {"type": "token", "content": raw, "agent_id": "home-steward"}
        response = AgentResponse.from_string(raw)
        yield {"type": "final_response", "text": response.text, "response": response}

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)
    db = isolate_sessions(str(tmp_path / "replay"))
    with patch("core.loaders.agents_loader.AgentsLoader") as loader:
        loader.return_value.get_agent.return_value = agent
        loader.return_value.get_agent_config.return_value = {}
        result = await replay.run_combo(case, 1, replay.Combo(light="FLASH_LITE:minimal"), db, measure_tts=False)

    assert result.failures == ["home-steward's own model never ran after its light call"]


class TestLightMisses:

    LIGHT = {"agent": "home-steward", "model": FLASH_LITE, "thinking": "minimal", "light": True}
    OWN = {"agent": "home-steward", "model": FLASH, "thinking": "low"}

    def test_a_light_call_needs_the_agents_own_model_after_it(self):
        assert replay.light_misses([self.LIGHT, self.OWN]) == []
        assert replay.light_misses([self.LIGHT]) == ["home-steward's own model never ran after its light call"]
        assert replay.light_misses([self.OWN, self.LIGHT]) == [
            "home-steward's own model never ran after its light call"], "a call before it doesn't count"
        assert replay.light_misses([self.OWN]) == [] and replay.light_misses([]) == []

    def test_it_must_be_the_same_agents_model(self):
        main = {"agent": "main", "model": FLASH, "thinking": "low"}
        assert replay.light_misses([self.LIGHT, main]) == [
            "home-steward's own model never ran after its light call"]
        main_light = {**self.LIGHT, "agent": "main"}
        assert replay.light_misses([main_light, self.LIGHT, self.OWN]) == [
            "main's own model never ran after its light call"]

    def test_each_agent_is_named_once(self):
        assert replay.light_misses([self.LIGHT, self.LIGHT]) == [
            "home-steward's own model never ran after its light call"]


class TestBenchmarkReport:

    def test_a_report_per_combo_and_one_combo_reads_as_before(self):
        results = [CaseResult(case_id="a", run=1, marks={"first_token": 900}, combo="FLASH:low"),
                   CaseResult(case_id="a", run=1, marks={"first_token": 2900}, combo="PRO:low",
                              failures=["reply lacks /x/"])]
        report = replay.render_combos(results, ["PRO:low", "FLASH:low"])
        assert report.index("### PRO:low") < report.index("### FLASH:low")
        assert "Quality: 0/1 runs passed." in report and "Quality: 1/1 runs passed." in report
        assert replay.render_combos(results[:1]) == render_report(results[:1])

    def test_a_combo_that_sets_the_level_shows_the_models_picks_as_asks(self):
        pick = {"kind": "thinking_pick", "level": "high", "applied": "medium"}
        assert replay._picks(CaseResult(case_id="a", run=1, marks={}, combo="FLASH:low", thinking_picks=[pick])) \
            == ["asked high (the combo's level held)"]
        assert replay._picks(CaseResult(case_id="a", run=1, marks={}, combo="FLASH:prod", thinking_picks=[pick])) \
            == ["picked medium (asked high)"]

    def test_a_light_first_call_doesnt_set_the_level(self):
        pick = {"kind": "thinking_pick", "level": "high", "applied": "medium"}

        def picks(combo):
            return replay._picks(CaseResult(case_id="a", run=1, marks={}, combo=combo, thinking_picks=[pick]))

        assert picks("prod+light:FLASH_LITE:minimal") == ["picked medium (asked high)"]
        assert picks("FLASH:low+light:FLASH_LITE:minimal") == ["asked high (the combo's level held)"]

    def test_how_the_light_calls_ended_is_noted_and_summed_up(self):
        handover = {"kind": "handover", "agent": "home-steward"}
        results = [
            CaseResult(case_id="a", run=1, marks={}, light_events=[handover]),
            CaseResult(case_id="a", run=2, marks={}, light_events=[
                {"kind": "light_fallback", "agent": "home-steward", "reason": "deadline", "ms": 1500}]),
            CaseResult(case_id="b", run=1, marks={}, light_events=[
                {"kind": "light_cut", "agent": "home-steward", "reason": "error"}, handover]),
            CaseResult(case_id="b", run=2, marks={}, light_events=[
                {"kind": "light_fallback", "agent": "home-steward", "reason": "refused", "ms": 200}]),
        ]
        report = render_report(results)
        rows = [line for line in report.splitlines() if line.startswith(("| a |", "| b |"))]
        assert rows[0].endswith("|  |"), "a hand-over is how a light call should end: no note"
        assert "light call fell back: deadline" in rows[1]
        assert "light call broke off" in rows[2]
        assert "Light calls: 2 handed over, 2 fell back (deadline: 1, refused: 1), 1 broke off." in report
        assert "Light calls" not in render_report([CaseResult(case_id="a", run=1, marks={})])


def _stored(case_id="read", run=1, combo="prod", **kwargs):
    return {**asdict(CaseResult(case_id=case_id, run=run, marks={}, combo=combo, **kwargs)), "passed": True}


class TestResuming:

    @pytest.mark.asyncio
    async def test_a_rerun_skips_the_runs_its_out_file_has_and_saves_after_every_run(self, tmp_path, capsys):
        out = tmp_path / "bench" / "results.json"
        out.parent.mkdir()
        out.write_text(json.dumps([_stored(combo="FLASH:low")]))
        seen = []

        async def fake_run_case(case, run, db_path, **kwargs):
            seen.append((case["id"], run, len(json.loads(out.read_text()))))
            return CaseResult(case_id=case["id"], run=run, marks={})

        with patch.object(replay, "run_case", fake_run_case), patch.object(replay, "guard_writes"), \
             patch("core.channel.voice.engines.tts.TTSEngine"):
            code = await replay.main(["--cases", _cases_file(tmp_path, CASES), "--state-dir", str(tmp_path / "s"),
                                      "--out", str(out), "--combo", "FLASH:low,PRO:low", "--light", "off",
                                      "--runs", "2"])

        assert code == 0
        assert seen == [("read", 1, 1), ("read", 2, 2), ("read", 2, 3)], "each run sees the one before it saved"
        assert [(r["run"], r["combo"]) for r in json.loads(out.read_text())] == [
            (1, "FLASH:low"), (1, "PRO:low"), (2, "PRO:low"), (2, "FLASH:low")]  # run 2 starts on PRO:low
        report = capsys.readouterr().out
        assert "### FLASH:low" in report and "### PRO:low" in report

    @pytest.mark.asyncio
    async def test_a_finished_replay_only_reports_and_old_runs_count_as_prod(self, tmp_path, capsys):
        """Runs stored before combos ran without a light call: a rerun without one finds them."""
        out = tmp_path / "results.json"
        old = _stored()
        del old["combo"]  # written before combos
        out.write_text(json.dumps([old]))
        with patch.object(replay, "isolate_sessions", side_effect=AssertionError("nothing to run")):
            code = await replay.main(["--cases", _cases_file(tmp_path, CASES), "--out", str(out), "--light", "off"])
        assert code == 0
        assert "| read | 1 | PASS |" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_without_light_the_configured_light_model_is_in_the_label(self, tmp_path):
        """With VOICE_LIGHT_MODEL set, a plain rerun is that light model's: it finds its stored runs."""
        from core.config.settings import Config

        out = tmp_path / "results.json"
        out.write_text(json.dumps([_stored(combo="prod+light:FLASH_LITE:minimal")]))
        with patch.object(replay, "isolate_sessions", side_effect=AssertionError("nothing to run")), \
             Config().overriding(voice_light_model="FLASH_LITE:minimal"):
            code = await replay.main(["--cases", _cases_file(tmp_path, CASES), "--out", str(out)])
        assert code == 0

    def test_results_are_saved_whole_or_not_at_all(self, tmp_path):
        path = tmp_path / "bench" / "results.json"
        replay.save_json(str(path), [{"run": 1}])
        with pytest.raises(TypeError):
            replay.save_json(str(path), [{(1, 2): "not JSON"}])
        assert json.loads(path.read_text()) == [{"run": 1}]
        assert os.listdir(path.parent) == ["results.json"], "no temp file left behind"


class TestRateLimits:

    @pytest.fixture
    def waits(self, monkeypatch):
        waited = []

        async def fake_sleep(seconds):
            waited.append(seconds)

        monkeypatch.setattr(replay, "_sleep", fake_sleep)
        return waited

    async def _main(self, tmp_path, run_case, *args):
        with patch.object(replay, "run_case", run_case), patch.object(replay, "guard_writes"), \
             patch("core.channel.voice.engines.tts.TTSEngine"):
            return await replay.main(["--cases", _cases_file(tmp_path, CASES), "--state-dir", str(tmp_path / "s"),
                                      "--out", str(tmp_path / "results.json"), *args])

    @pytest.mark.asyncio
    async def test_a_refused_run_waits_and_runs_again(self, tmp_path, waits):
        refused = CaseResult(case_id="read", run=1, marks={}, errored=True, failures=[
            "agent error: [Error: [429] You exceeded your current quota. Please retry in 42.5s.]"])
        run = AsyncMock(side_effect=[refused, RuntimeError("429 RESOURCE_EXHAUSTED"),
                                     CaseResult(case_id="read", run=1, marks={})])
        assert await self._main(tmp_path, run) == 0
        assert waits == [42.5, 60], "as long as the API asked, else the next wait"
        stored = json.loads((tmp_path / "results.json").read_text())
        assert [r["errored"] for r in stored] == [False], "only the run that went through is kept"

    @pytest.mark.asyncio
    async def test_a_run_still_refused_after_every_wait_stops_the_replay_unrecorded(self, tmp_path, waits):
        refused = CaseResult(case_id="read", run=1, marks={}, errored=True,
                             failures=["agent error: [429] Resource has been exhausted (e.g. check quota)."])
        run = AsyncMock(return_value=refused)
        assert await self._main(tmp_path, run) == 3
        assert run.await_count == len(replay.RATE_LIMIT_WAITS) + 1
        assert waits == list(replay.RATE_LIMIT_WAITS)
        assert not (tmp_path / "results.json").exists(), "a rerun tries it again"

    @pytest.mark.asyncio
    async def test_other_failures_are_results_or_errors_not_retries(self, tmp_path, waits):
        failed = CaseResult(case_id="read", run=1, marks={}, errored=True, failures=["agent error: [503] overloaded"])
        assert await self._main(tmp_path, AsyncMock(return_value=failed)) == 2
        assert waits == []
        with pytest.raises(ValueError):
            await self._main(tmp_path, AsyncMock(side_effect=ValueError("a bug")), "--runs", "2")

    @pytest.mark.asyncio
    async def test_the_pacer_spaces_runs_by_the_calls_their_traces_show(self, monkeypatch, waits):
        now = [100.0]
        monkeypatch.setattr(replay, "_clock", lambda: now[0])
        pacer = replay.Pacer(rpm=12)  # 5 s a call
        await pacer.wait()
        pacer.ran(started=100.0, calls=3)
        now[0] = 108.0  # the run took 8 s
        await pacer.wait()
        assert waits == [7.0], "3 calls take 15 s at 12 a minute"
        now[0] = 115.0
        pacer.ran(started=115.0, calls=0)  # the fixed line: no model call
        await pacer.wait()
        unpaced = replay.Pacer()
        unpaced.ran(started=115.0, calls=50)
        await unpaced.wait()
        assert waits == [7.0]

    @pytest.mark.asyncio
    async def test_main_paces_by_each_runs_model_calls(self, tmp_path, monkeypatch, waits):
        now = [0.0]
        monkeypatch.setattr(replay, "_clock", lambda: now[0])

        async def fake_run_case(case, run, db_path, **kwargs):
            now[0] += 2.0  # each run takes 2 s and makes 2 calls
            return CaseResult(case_id=case["id"], run=run, marks={}, model_calls=[{"kind": "model_call"}] * 2)

        assert await self._main(tmp_path, fake_run_case, "--runs", "2", "--rpm", "6") == 0
        assert waits == [18.0], "2 calls at 6 a minute take 20 s; the first run used 2"


class TestServiceCheck:
    """Before it times combos, two one-word calls per tier: a slow service stops
    the replay, since the combos' latency would measure it, not them."""

    async def _main(self, tmp_path, run_case, *args):
        with patch.object(replay, "run_case", run_case), patch.object(replay, "guard_writes"), \
             patch("core.channel.voice.engines.tts.TTSEngine"):
            return await replay.main(["--cases", _cases_file(tmp_path, CASES), "--state-dir", str(tmp_path / "s"),
                                      "--out", str(tmp_path / "results.json"), *args])

    @staticmethod
    def runs():
        return AsyncMock(side_effect=lambda case, run, db_path, **kwargs: CaseResult(case_id=case["id"], run=run,
                                                                                      marks={}))

    def test_it_checks_the_tiers_the_combos_run_on(self):
        combos = replay.combos_from
        assert replay.tiers_to_check(combos()) == [], "a plain replay times no combo"
        assert replay.tiers_to_check(combos(combos=["PRO:low,FLASH_LITE:minimal"])) == ["FLASH_LITE", "PRO"]
        assert replay.tiers_to_check(combos(thinking="low")) == list(replay.TIER_ORDER), "prod's agents use any"
        assert replay.tiers_to_check(combos(combos=["PRO:low"], lights=["FLASH_LITE:minimal"])) == [
            "FLASH_LITE", "PRO"], "and the light call's"

    def test_a_tier_is_slow_when_every_call_is_or_any_one_hangs(self):
        times = {"FLASH_LITE": [8.5, 11.5], "FLASH": [9.0, 1.0], "PRO": [5.0, 5.5]}
        assert replay.slow_tiers(times) == ["FLASH_LITE"], "one slow call alone is noise"
        assert replay.slow_tiers({"PRO": [float("inf"), 3.19]}) == ["PRO"], "the NAS's PRO at 11:03 on Nov 3"
        assert replay.slow_tiers({"PRO": [float("inf"), float("inf")]}) == ["PRO"]

    @pytest.mark.asyncio
    async def test_a_slow_service_runs_nothing_and_says_why(self, tmp_path, service, capsys):
        service.seconds = {"FLASH_LITE": 8.5}
        run = self.runs()
        assert await self._main(tmp_path, run, "--combo", "FLASH_LITE:minimal,PRO:low") == replay.SLOW_SERVICE
        run.assert_not_awaited()
        assert service.asked == ["FLASH_LITE", "FLASH_LITE", "PRO", "PRO"]
        err = capsys.readouterr().err
        assert "[check] FLASH_LITE: first chunk in 8.50 s, 8.50 s (slow when all are over 2 s, or any hangs)" in err
        assert "[check] PRO: first chunk in 0.10 s, 0.10 s" in err
        assert "slow right now (FLASH_LITE)" in err and "add --anyway" in err
        assert not (tmp_path / "results.json").exists()

    @pytest.mark.asyncio
    async def test_anyway_runs_regardless(self, tmp_path, service, capsys):
        service.seconds = 9.0
        run = self.runs()
        assert await self._main(tmp_path, run, "--combo", "FLASH_LITE:minimal", "--anyway") == 0
        run.assert_awaited_once()
        assert "running anyway" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_a_failing_call_counts_as_slow(self, tmp_path, service, capsys):
        service.seconds = RuntimeError("429 RESOURCE_EXHAUSTED")
        assert await self._main(tmp_path, self.runs(), "--combo", "PRO:low") == replay.SLOW_SERVICE
        err = capsys.readouterr().err
        assert "[check] PRO: a call failed: RuntimeError: 429 RESOURCE_EXHAUSTED" in err
        assert "[check] PRO: first chunk in none, none" in err

    @pytest.mark.asyncio
    async def test_a_stalled_call_is_given_up_on_so_the_check_cant_hang(self, service, monkeypatch, capsys):
        monkeypatch.setattr(replay, "CHECK_GIVE_UP_S", 0.01)
        service.seconds = {"PRO": "stall"}
        times = await replay.check_service(["FLASH", "PRO"])
        assert times == {"FLASH": [0.1, 0.1], "PRO": [float("inf"), float("inf")]}
        assert replay.slow_tiers(times) == ["PRO"]
        assert "[check] PRO: no first chunk after 0.01 s, so given up on" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_a_plain_replay_is_not_checked(self, tmp_path, service):
        """A plain replay is production as configured, light first call included: not timed, so not checked."""
        from core.config.settings import Config

        run = self.runs()
        with Config().overriding(voice_light_model="FLASH_LITE:minimal"):
            assert await self._main(tmp_path, run) == 0
        assert service.asked == []
        run.assert_awaited_once()
        (stored,) = json.loads((tmp_path / "results.json").read_text())
        assert stored["combo"] == "prod+light:FLASH_LITE:minimal"

    @pytest.mark.asyncio
    async def test_a_light_model_asked_for_is_checked(self, tmp_path, service):
        assert await self._main(tmp_path, self.runs(), "--light", "FLASH_LITE:minimal") == 0
        assert "FLASH_LITE" in service.asked

    @pytest.mark.asyncio
    async def test_a_check_call_is_the_tiers_model_at_its_lowest_thinking(self, monkeypatch):
        """first_chunk_s itself, through the real model wrapper, with no request sent."""
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk
        from langchain_google_genai import ChatGoogleGenerativeAI

        sent = []

        async def fake_astream(self, messages, stop=None, run_manager=None, **kwargs):
            sent.append((self.model.removeprefix("models/"), kwargs.get("thinking_level")))
            yield ChatGenerationChunk(message=AIMessageChunk(content="ready"))

        monkeypatch.setenv("GOOGLE_API_KEY", "fake")
        monkeypatch.setattr(ChatGoogleGenerativeAI, "_astream", fake_astream)
        assert await replay.first_chunk_s("PRO") >= 0
        assert await replay.first_chunk_s("FLASH_LITE") >= 0
        assert sent == [(PRO, "low"), (FLASH_LITE, "minimal")]
