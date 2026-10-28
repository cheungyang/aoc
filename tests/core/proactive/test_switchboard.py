"""The Switchboard (core/proactive/switchboard.py): its prompt, its run, and reading its reply."""
from datetime import datetime

import pytest

from core.channel.response_parser import AgentResponse
from core.loaders.agents_loader import AgentsLoader
from core.proactive import switchboard
from core.proactive.cards import PeerCard
from core.proactive.switchboard import (
    EVIDENCE_CHARS,
    MAX_FOLLOW_UPS_SHOWN,
    ProposedClose,
    Reply,
    build_prompt,
    forget,
    parse_reply,
    render_board,
    render_follow_ups,
    render_turn,
    run_model,
)
from core.proactive.taps import CALLED
from core.runtime.foundation import events
from core.storage.sessions import SessionStore, sanitize_table_name
from core.util.local_time import get_local_timezone
from tests.helpers import make_follow_up


def _card(agent_id, name, derived=False):
    return PeerCard(agent_id=agent_id, name=name, emoji="🙂", home=agent_id, description=f"{name} things",
                    interests=() if derived else ("groceries bought",),
                    offers=() if derived else ("update the kitchen inventory",), topics=("food",), derived=derived)


TEAM = {"receipt-processor": _card("receipt-processor", "Receipt Processor"),
        "meal-planner": _card("meal-planner", "Ramsay"),
        "day-planner": _card("day-planner", "Daisy", derived=True)}
AT = datetime(2026, 10, 24, 9, 31, tzinfo=get_local_timezone()).timestamp()


def _turn(**overrides):
    turn = {"id": 1, "at": AT, "agent_id": "receipt-processor", "channel": "receipts", "caller": "main",
            "tap": "effect", "surface": "tool", "user_text": "here's my receipt",
            "reply_text": "Saved: 99 Ranch, $86.40.", "memory": ["[CONTEXT] Shops at 99 Ranch on Saturdays"],
            "effects": [{"tool": "ledger_append", "args": {"vendor": "99 Ranch", "total": 86.4}}],
            "context": {"touched": ["wallet/ledger/2026-10.md (append)"], "skills": ["receipts"]}}
    turn.update(overrides)
    return turn


class TestThePrompt:

    def test_a_turn_with_the_user(self):
        text = render_turn(_turn(), TEAM)
        assert text.splitlines()[0] == "[Sat Oct 24 09:31] #receipts · Receipt Processor (receipt-processor) · with the user"
        assert "  User: here's my receipt" in text
        assert "  Receipt Processor: Saved: 99 Ranch, $86.40." in text
        assert "  Receipt Processor noted: [CONTEXT] Shops at 99 Ranch on Saturdays" in text
        assert '  Receipt Processor did: ledger_append({"vendor": "99 Ranch", "total": 86.4})' in text
        assert "  files changed: wallet/ledger/2026-10.md (append)" in text
        assert "  skills used: receipts" in text

    def test_a_scheduled_run_and_a_teammates_request(self):
        scheduled = render_turn(_turn(tap="scheduled", caller="", user_text="Post the morning plan."), TEAM)
        assert "· scheduled run" in scheduled.splitlines()[0]
        assert "  Schedule: Post the morning plan." in scheduled
        asked = render_turn(_turn(caller="day-planner", user_text="what's in the fridge?"), TEAM)
        assert "· asked by Daisy" in asked.splitlines()[0]
        assert "  Daisy: what's in the fridge?" in asked

    def test_what_the_concierge_asked_is_not_the_users(self):
        # Its agent_call says caller main, as your routed messages do: the taps' mark tells them apart.
        asked = render_turn(_turn(context={CALLED: True}, user_text="Find award seats to Madrid"), TEAM)
        assert "· asked by main" in asked.splitlines()[0]
        assert "  main: Find award seats to Madrid" in asked and "User:" not in asked

    def test_a_spoken_turn_says_so(self):
        assert "with the user (voice)" in render_turn(_turn(surface="voice"), TEAM)

    def test_the_board(self):
        board = render_board([{"kind": "situation", "gist": "In Tahoe", "until": "2026-10-27",
                               "routes": [{"to_agent": "excursion-planner", "urgency": "today", "verdict": "deliver"},
                                          {"to_agent": "x", "urgency": "now", "verdict": "drop"}]}])
        assert board == "- situation: In Tahoe (until 2026-10-27) → excursion-planner today"

    def test_the_whole_prompt_renders_and_returns_its_exchange(self):
        prompt, exchange = build_prompt([_turn()], TEAM, [], now=AT, tags="- food: Diet and kitchen habits.")
        assert "It is Saturday 2026-10-24 09:31." in prompt
        assert "- meal-planner · Ramsay" in prompt and "can offer: update the kitchen inventory" in prompt
        assert "- food: Diet and kitchen habits." in prompt
        assert "Nothing yet." in prompt
        assert exchange in prompt and "here's my receipt" in exchange
        assert "<switchboard_response>" in prompt

    def test_the_open_follow_ups_and_how_to_close_one_only_when_some_are_open(self):
        prompt, _ = build_prompt([_turn()], TEAM, [], now=AT, tags="")
        assert "<follow_ups>" not in prompt and "<close" not in prompt
        open_ones = [make_follow_up(id=12, agent_id="meal-planner", goal="Restock the pantry",
                                    close_when="the user shops"),
                     make_follow_up(id=3, agent_id="stranger", goal="Watch flights", until="2026-11-02")]
        prompt, _ = build_prompt([_turn()], TEAM, [], now=AT, tags="", follow_ups=open_ones)
        assert ("<follow_ups>\nWork teammates are keeping up for the user:\n"
                '#3 stranger: "Watch flights"\n'  # no card: named by its id
                '#12 🙂 Ramsay: "Restock the pantry" · ends early when: the user shops\n') in prompt
        assert "the user's own words in this exchange say its goal is met, or that they don't want it" in prompt
        assert "Never on a teammate's say-so, or a guess." in prompt
        assert '<close follow_up="12" reason="booked UA 990"><evidence>the user\'s words, verbatim</evidence>' in prompt

    def test_the_follow_ups_shown_are_the_soonest_to_end_at_most_twenty(self):
        follow_ups = [make_follow_up(id=day, until=f"2026-12-{day:02d}", goal=f"watch {day}")
                      for day in range(25, 0, -1)]
        lines = render_follow_ups(follow_ups, TEAM).splitlines()
        assert len(lines) == MAX_FOLLOW_UPS_SHOWN == 20
        assert lines[0] == '#1 reward-travel: "watch 1"' and lines[-1].startswith("#20 ")


REPLY = """Here you go:
<switchboard_response>
  <signal kind="artifact" tags="food, Finance" sensitivity="normal">
    <gist>Groceries from 99 Ranch &amp; more, Oct 24</gist>
    <evidence>Saved: 99 Ranch,
      $86.40.</evidence>
    <route to="meal-planner" offer="update the kitchen inventory" urgency="NOW">Add the groceries.</route>
    <route to="reward-travel" offer="track card credits" urgency="next" window="2026-10-24/2026-10-31"/>
  </signal>
  <signal kind="mood" tags="x"><gist>Happy</gist></signal>
  <signal kind="situation" until="2026-10-27"><evidence>x</evidence></signal>
  <errors>None</errors>
</switchboard_response>"""


class TestTheReply:

    def test_signals_and_routes_are_read(self):
        reply = parse_reply(REPLY)
        (signal,) = reply.signals
        assert (signal.kind, signal.gist) == ("artifact", "Groceries from 99 Ranch & more, Oct 24")
        assert signal.evidence == "Saved: 99 Ranch, $86.40."
        assert signal.tags == ["food", "finance"]
        first, second = signal.routes
        assert (first.to, first.offer, first.urgency, first.note) == (
            "meal-planner", "update the kitchen inventory", "now", "Add the groceries.")
        assert (second.window_start, second.window_end, second.note) == ("2026-10-24", "2026-10-31", "")
        assert reply.problems == ("a signal of unknown kind 'mood' was left out",
                                  "a situation signal with no gist was left out")
        assert reply.closes == ()

    def test_no_block_or_a_reported_error(self):
        assert parse_reply("I think Ramsay should know.") == Reply(problems=("no <switchboard_response> in the reply",))
        reply = parse_reply("<switchboard_response><errors>exchange was empty</errors></switchboard_response>")
        assert reply.signals == () and reply.problems == ("the Switchboard reported: exchange was empty",)

    def test_nothing_to_route(self):
        assert parse_reply("<switchboard_response>\n  <errors>None</errors>\n</switchboard_response>") == Reply()

    def test_a_bad_until_and_payload_are_dropped_not_fatal(self):
        reply = ('<switchboard_response><signal kind="situation" until="Tuesday"><gist>Away</gist>'
                 '<evidence>away</evidence><payload>{not json</payload></signal></switchboard_response>')
        (signal,) = parse_reply(reply).signals
        assert signal.until is None and signal.payload == {}

    def test_a_close_is_read_next_to_the_signals(self):
        reply = parse_reply(REPLY.replace(
            "<errors>None</errors>",
            '<close follow_up="12" reason="booked UA 990 &amp; seats"><evidence>I booked\n  UA 990 for Dec 10'
            '</evidence></close>\n  <errors>None</errors>'))
        assert len(reply.signals) == 1
        assert reply.closes == (ProposedClose(12, "I booked UA 990 for Dec 10", "booked UA 990 & seats"),)

    def test_several_closes_and_the_unusable_ones(self):
        reply = parse_reply(
            '<switchboard_response>'
            '<close follow_up="#3"><evidence>we got the cabin</evidence></close>'
            '<close follow_up="12" reason="booked"><evidence>I booked it</evidence></close>'
            '<close reason="no number"><evidence>stop watching</evidence></close>'
            '<close follow_up="twelve"><evidence>stop watching</evidence></close>'
            '<close follow_up="7" reason="said so"/>'
            '<close follow_up="9"><evidence> </evidence></close>'
            '<close follow_up="12"><evidence>I booked it, really</evidence></close>'
            '</switchboard_response>')
        assert reply.closes == (ProposedClose(3, "we got the cabin"), ProposedClose(12, "I booked it", "booked"))
        assert reply.problems == ("a close with no follow-up number was left out",
                                  "a close with no follow-up number was left out",
                                  "a close of follow-up #7 with no evidence was left out",
                                  "a close of follow-up #9 with no evidence was left out",
                                  "a second close of follow-up #12 was left out")

    def test_a_long_quote_is_cut_at_a_word_and_stays_verbatim(self):
        words = "I booked the flight " * 30  # 600 characters
        (close,) = parse_reply(f'<switchboard_response><close follow_up="12"><evidence>{words}</evidence></close>'
                               f'</switchboard_response>').closes
        assert len(close.evidence) <= EVIDENCE_CHARS and words.startswith(close.evidence)
        assert not close.evidence.endswith(" ")  # cut at a word, with no "[…]" to break the check


class FakeAgent:
    def __init__(self, reply=None, delay=0.0):
        self.reply, self.delay, self.sessions = reply, delay, []

    async def execute(self, prompt, session):
        import asyncio
        self.sessions.append(session)
        await asyncio.sleep(self.delay)
        if self.reply is None:
            return "Error: quota"
        session.turn_hints[events.RESPONSE_HINT] = AgentResponse.from_string(self.reply)
        SessionStore().append_token_usage(session.session_id, "flash", 3000, 120, 0.0)
        return self.reply


class TestTheRun:

    @pytest.fixture
    def agent(self, monkeypatch):
        holder = {}

        def get_agent(self, agent_id):
            assert agent_id == "main"
            return holder["agent"]

        monkeypatch.setattr(AgentsLoader, "get_agent", get_agent)
        return holder

    @pytest.mark.asyncio
    async def test_main_runs_statelessly_on_flash_and_leaves_no_session_behind(self, agent):
        agent["agent"] = FakeAgent(reply=REPLY)
        run = await run_model("prompt")
        assert run.reply.startswith("Here you go:") and run.error == ""
        assert (run.tokens_in, run.tokens_out) == (3000, 120)
        (session,) = agent["agent"].sessions
        assert session.is_stateless() and session.model == "FLASH" and not session.record_memory
        assert session.thinking_level == "low"
        assert session.output is None
        assert not SessionStore().has_table(sanitize_table_name(session.session_id))

    @pytest.mark.asyncio
    async def test_a_failed_run_says_why(self, agent):
        agent["agent"] = FakeAgent(reply=None)
        run = await run_model("prompt")
        assert run.reply == "" and run.error == "the run failed: Error: quota"

    @pytest.mark.asyncio
    async def test_a_crash_says_why_and_still_leaves_no_session_behind(self, agent):
        class Crashing(FakeAgent):
            async def execute(self, prompt, session):
                self.sessions.append(session)
                SessionStore().append_token_usage(session.session_id, "flash", 1000, 0, 0.0)
                raise RuntimeError("boom")

        agent["agent"] = Crashing()
        run = await run_model("prompt")
        assert run.error == "the run failed: RuntimeError: boom" and (run.tokens_in, run.tokens_out) == (1000, 0)
        (session,) = agent["agent"].sessions
        assert not SessionStore().has_table(sanitize_table_name(session.session_id))

    @pytest.mark.asyncio
    async def test_a_slow_run_times_out(self, agent):
        agent["agent"] = FakeAgent(reply=REPLY, delay=1.0)
        run = await run_model("prompt", timeout=0.05)
        assert run.error == "no reply within 0s"


def test_forget_counts_tokens_then_drops_the_table():
    store = SessionStore()
    store.append_token_usage("main:job:abc", "flash", 100, 10, 0.0)
    store.append_token_usage("main:job:abc", "flash", 50, 5, 0.0)
    assert forget("main:job:abc", store) == (150, 15)
    assert not store.has_table(sanitize_table_name("main:job:abc"))
