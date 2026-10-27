"""The taps (core/proactive/taps.py): what a finished turn leaves on the Board.

The tools are real: which calls count comes from the effects table
(core/proactive/effects.py), with the tools' own permission bundles.
"""
import pytest

from core.channel.response_parser import AgentResponse
from core.config.settings import Config
from core.loaders.skills_loader import SkillsLoader
from core.proactive import taps
from core.proactive.taps import (
    CALLS_HINT,
    TAP_CHAT,
    TAP_EFFECT,
    TAP_SCHEDULED,
    CallLog,
    SignalHook,
    caller_of,
    compact,
    counts,
    entry_for,
    memory_lines,
    user_words,
)
from core.runtime.foundation.sessions import SessionManager
from core.runtime.hooks import Turn
from core.tooling.hooks import ToolCall

RECEIPTS = {"channels": ["receipts", "general"], "skills": {"receipts": {}, "budget": {}}}


@pytest.fixture(autouse=True)
def skills(monkeypatch):
    skill_tools = {"receipts": {"ledger_append": {}, "filesystem": {}}, "budget": {"filesystem": {}}}
    monkeypatch.setattr(SkillsLoader, "get_skill_tools", lambda self, skill_id: skill_tools.get(skill_id, {}))


def _turn(calls=(), agent_id="receipt-processor", config=RECEIPTS, prompt="<caller>main</caller>\nhere's my receipt",
          reply="Saved: Costco, $86.40.\n<system_memory_log>\n- [12:00:00] [CONTEXT] Costco run on Oct 24\n"
                "</system_memory_log>", **session):
    session.setdefault("source", "tool")
    session.setdefault("channel", "receipts")
    ctx = SessionManager.get_session(agent_id=agent_id, **session)
    ctx.turn_hints.clear()
    if calls:
        ctx.turn_hints[CALLS_HINT] = list(calls)
    response = AgentResponse.from_string(reply) if reply is not None else None
    return Turn(agent_id=agent_id, agent_config=config, ctx=ctx, prompt=prompt, response=response)


def _call(tool, args, status="ok"):
    return {"tool": tool, "args": args, "status": status}


RECEIPT = _call("ledger_append", {"vendor": "Costco", "total": 86.4, "items": [{"name": "Spinach frittata"}]})


class TestWhichTurnsCount:

    def test_an_agents_own_turn_counts(self):
        assert counts(_turn())
        assert counts(_turn(source="scheduled"))

    @pytest.mark.parametrize("why, turn", [
        ("the host, reachable everywhere", lambda: _turn(agent_id="main", config={"channels": ["*"]})),
        ("a stateless worker", lambda: _turn(config={"channels": ["receipts"], "stateless": True})),
        ("a dream or a replay", lambda: _turn(record_memory=False)),
        ("a step inside a graph run", lambda: _turn(graph_id="coding")),
        ("a background job", lambda: _turn(source="job")),
        ("the voice replay", lambda: _turn(source="voice_replay")),
        ("an agent with no channel", lambda: _turn(config={})),
    ])
    def test_these_are_skipped(self, why, turn):
        assert not counts(turn()), why
        assert entry_for(turn()) is None


class TestTheEntry:

    def test_a_saved_receipt_is_an_effect_that_wakes_the_switchboard_at_once(self):
        entry = entry_for(_turn([RECEIPT]), now=123.0)
        assert entry.tap == TAP_EFFECT
        assert entry.at == 123.0
        assert (entry.agent_id, entry.channel, entry.caller) == ("receipt-processor", "receipts", "main")
        assert entry.user_text == "here's my receipt"
        assert entry.reply_text == "Saved: Costco, $86.40."
        assert entry.memory == ["[CONTEXT] Costco run on Oct 24"]
        (effect,) = entry.effects
        assert (effect["tool"], effect["role"], effect["status"]) == ("ledger_append", "trigger", "ok")
        assert effect["args"]["vendor"] == "Costco"

    def test_a_failed_call_is_no_effect(self):
        entry = entry_for(_turn([_call("ledger_append", {"vendor": "Costco"}, status="failed")]))
        assert entry.effects == [] and entry.tap == TAP_CHAT

    def test_context_effects_dont_wake_the_switchboard(self):
        entry = entry_for(_turn([_call("graph_call", {"graph_name": "coding", "query": "build it"})]))
        assert entry.tap == TAP_CHAT
        assert entry.effects[0]["role"] == "context"

    def test_only_the_actions_in_the_table_count(self):
        reads = _call("home_assistant", {"instructions": [{"action": "get_state", "entity_id": "light.x"}]})
        writes = _call("home_assistant", {"instructions": [{"action": "call_service", "service": "light.turn_off"}]})
        entry = entry_for(_turn([reads, writes]))
        assert [e["actions"] for e in entry.effects] == [["call_service"]]

    def test_a_tool_not_in_the_table_is_ignored(self):
        assert entry_for(_turn([_call("web_search", {"query": "pumpkins"})])).effects == []

    def test_a_scheduled_run_wakes_the_switchboard_at_once(self):
        assert entry_for(_turn(source="scheduled", prompt="Post the morning plan.")).tap == TAP_SCHEDULED

    def test_a_proactive_turn_counts_with_hops_1_so_it_starts_nothing(self):
        turn = _turn([RECEIPT], source="proactive", prompt="A teammate learned something about the user...")
        assert counts(turn)
        entry = entry_for(turn)
        # Read at once like a scheduled run, even with a trigger effect.
        assert (entry.hops, entry.tap, entry.source, entry.surface) == (1, TAP_SCHEDULED, "proactive", "scheduled")
        assert entry_for(_turn([RECEIPT])).hops == 0

    def test_filesystem_writes_become_touched_paths_not_effects(self):
        with Config().overriding(pkm_dir="/vault"):
            entry = entry_for(_turn([
                _call("filesystem", {"instructions": [
                    {"action": "read", "path": "/vault/wiki/topics/AI_Memory.md"},
                    {"action": "write", "path": "/vault/wiki/topics/Prompt_Caching.md", "content": "..."},
                ]}),
                _call("filesystem", {"instructions": [
                    {"action": "move", "path": "/vault/inbox/a.md", "destination": "/vault/wiki/a.md"}]}),
            ]))
        assert entry.effects == []
        assert entry.context["touched"] == ["wiki/topics/Prompt_Caching.md (write)", "inbox/a.md (move)",
                                            "wiki/a.md (move)"]

    def test_skills_are_labeled_from_the_calls(self):
        entry = entry_for(_turn([RECEIPT, _call("load_skill", {"skill_id": "budget"})]))
        # ledger_append belongs to one of the agent's skills; filesystem to two, so it says nothing.
        assert entry.context["skills"] == ["budget", "receipts"]

    def test_an_interrupted_turn_has_no_reply_but_still_counts(self):
        entry = entry_for(_turn([RECEIPT], reply=None))
        assert entry.reply_text == "" and entry.tap == TAP_EFFECT

    def test_the_ledger_keeps_a_bounded_amount_of_text(self):
        entry = entry_for(_turn(prompt="w" * 10_000))
        assert len(entry.user_text) < taps.TEXT_CHARS + 10


class TestTheWords:

    def test_a_handoff_capsule_is_left_out(self):
        prompt = ('<caller>main</caller>\nTed, create a syllabus on THIS topic\n\n'
                  '<handoff_context from="wiki-gardener" channel="pkm-wiki">\n<recent>...</recent>\n'
                  '</handoff_context>')
        assert user_words(prompt) == "Ted, create a syllabus on THIS topic"
        assert caller_of(prompt) == "main"

    def test_a_multimodal_prompt(self):
        prompt = [{"type": "text", "text": "<caller>main</caller>\nthis one"}, {"type": "image_url", "image_url": {}}]
        assert user_words(prompt) == "this one"
        assert caller_of(prompt) == "main"
        assert caller_of("no tag") == ""

    def test_memory_lines_lose_bullets_and_stamps(self):
        log = "- [12:00:01] [MEMORY] Task: saved receipt. Status: Success.\n\n* [CONTEXT] Evergreen: buys at Costco"
        assert memory_lines(log) == ["[MEMORY] Task: saved receipt. Status: Success.",
                                     "[CONTEXT] Evergreen: buys at Costco"]

    def test_arguments_are_trimmed(self):
        trimmed = compact({"content": "x" * 1000, "items": list(range(50)), "deep": {"a": {"b": {"c": {"d": 1}}}}})
        assert len(trimmed["content"]) < 220
        assert len(trimmed["items"]) == taps.ARG_ITEMS + 1 and trimmed["items"][-1] == "… 30 more"
        assert trimmed["deep"]["a"]["b"] == "…"  # a container three levels down is elided

    def test_a_receipts_items_survive_the_trim(self):
        trimmed = compact({"items": [{"name": "Pork belly", "amount": 12.48}]})
        assert trimmed == {"items": [{"name": "Pork belly", "amount": 12.48}]}


class TestTheCallLog:
    """The tool hook: it notes the turn's finished calls for entry_for."""

    def test_it_notes_each_finished_call_in_its_turn(self):
        turn = _turn()
        log = CallLog()
        log.called(ToolCall("ledger_append", {"vendor": "Costco"}, "ok", turn.ctx))
        log.called(ToolCall("load_skill", {"skill_id": "budget"}, "ok", turn.ctx))
        assert turn.ctx.turn_hints[CALLS_HINT] == [
            {"tool": "ledger_append", "args": {"vendor": "Costco"}, "status": "ok"},
            {"tool": "load_skill", "args": {"skill_id": "budget"}, "status": "ok"},
        ]
        assert entry_for(turn).tap == TAP_EFFECT

    def test_it_keeps_only_the_name_of_a_tool_the_taps_dont_read(self):
        turn = _turn()
        CallLog().called(ToolCall("web_fetch", {"url": "https://example.com", "body": "x" * 10_000}, "ok", turn.ctx))
        assert turn.ctx.turn_hints[CALLS_HINT] == [{"tool": "web_fetch", "args": {}, "status": "ok"}]

    def test_a_call_with_no_session_is_left_alone(self):
        CallLog().called(ToolCall("ledger_append", {}, "ok", None))


class TestTheHook:

    def test_it_hands_the_entry_to_the_sink(self):
        seen = []
        SignalHook(seen.append).ended(_turn([RECEIPT]))
        assert [e.tap for e in seen] == [TAP_EFFECT]

    def test_a_skipped_turn_reaches_no_sink(self):
        seen = []
        SignalHook(seen.append).ended(_turn(source="job"))
        assert seen == []

    def test_a_failure_never_reaches_the_turn(self, capsys):
        def broken(entry):
            raise RuntimeError("disk full")

        SignalHook(broken).ended(_turn([RECEIPT]))
        assert "disk full" in capsys.readouterr().out
