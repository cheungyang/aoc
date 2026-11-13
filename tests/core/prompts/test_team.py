"""The team block (core/prompts/team.py): who else the user can talk to, the
directives that hand them over or pass something on, and the open-passes block:
what an agent passed in a conversation that hasn't reached its teammate yet."""
from datetime import datetime
from unittest.mock import patch

from core.prompts.team import PASS_BRIEF_CHARS, get_open_passes_prompt, get_team_prompt, short_description
from core.storage.handoff_capsules import PASS, PASS_TTL_S, SWITCH, HandoffCapsuleStore
from core.util.local_time import get_local_timezone


class _Loader:
    """Agent configs, with the lookups `home_channel` makes (core/runtime/foundation/routing.py)."""

    CONFIGS = {
        "main": {"name": "Concierge", "channels": ["*"],
                 "channel_hosts": ["general", "project-planning", "fitness-coach", "day-planning"]},
        "goal-setter": {"name": "Grace", "channels": ["project-planning", "general"],
                        "description": "The Ambitious Project Coach: Optimistic, upbeat, and visionary."},
        "fitness-coach": {"name": "Frankie", "channels": ["fitness-coach"],
                          "description": "Fitness coach agent that curates and tracks home workouts."},
        "day-planner": {"name": "Daisy", "channels": ["day-planning", "general"]},
        "graph-worker-low": {"name": "Graph Worker Low", "channels": ["*"], "stateless": True},
    }

    def list_agent_ids(self):
        return list(self.CONFIGS)

    def get_agent_config(self, agent_id):
        return self.CONFIGS.get(agent_id)

    def hosts_channel(self, agent_id, channel):
        return channel in (self.CONFIGS.get(agent_id) or {}).get("channel_hosts", [])

    def eligible_agents_for_channel(self, channel, exclude_agent_id=None):
        return sorted(a for a, c in self.CONFIGS.items()
                      if a != exclude_agent_id and "*" not in c["channels"] and channel in c["channels"])


def test_an_agent_sees_its_teammates_but_not_itself():
    block = get_team_prompt("goal-setter", loader=_Loader())
    assert block.startswith("<team>") and block.endswith("</team>")
    assert "- Frankie [fitness-coach], #fitness-coach: Fitness coach agent that curates and tracks home workouts." in block
    assert "- Daisy [day-planner], #day-planning" in block
    assert "[goal-setter]" not in block, "not itself"
    assert "Concierge" not in block and "Graph Worker" not in block, "only agents with a channel of their own"


def test_it_says_how_to_hand_the_user_over_and_not_to_refuse():
    """Ted said he had no permission to move the user; Aki set out to design
    Frankie, who already existed."""
    block = get_team_prompt("goal-setter", loader=_Loader())
    assert 'end your reply with <switch to="ID"/>' in block
    assert "Never tell the user you can't move them" in block
    assert "They all exist already" in block
    assert "only mentions a teammate" in block


def test_it_moves_the_user_only_when_asked():
    """Users who only wanted something passed along were moved mid-conversation,
    and agents moved users who hadn't asked to go."""
    block = get_team_prompt("goal-setter", loader=_Loader())
    assert "Do it only when the user asks to go to a teammate or to talk to one now" in block
    assert "never move them on your own initiative" in block


def test_it_says_how_to_pass_something_on_and_keep_the_user():
    block = get_team_prompt("goal-setter", loader=_Loader())
    assert '("pass this to Daisy", "let Ramsay know", "send these to Aki"), don\'t move them' in block
    assert 'end your reply with <pass to="ID">brief</pass>' in block
    assert "the user stays with you" in block
    assert 'hand them over to that teammate with <switch to="ID"/>: the brief is already waiting there' in block
    assert "Keep both tags outside <speech>" in block


def test_it_says_how_to_open_when_the_user_was_just_handed_over():
    block = get_team_prompt("goal-setter", loader=_Loader())
    assert "A message that is only <handoff_arrival/> and that context" in block
    assert "They've just heard who you are and who handed them over" in block
    assert "No generic greeting." in block
    # Aki's first reply after the 17:43 hand-off had none: only the fallback was heard.
    assert "start with <speech>, as always" in block
    assert "If it holds <also_waiting>" in block


def test_agents_that_dont_talk_with_the_user_get_none():
    assert get_team_prompt("main", loader=_Loader()) == ""
    assert get_team_prompt("graph-worker-low", loader=_Loader()) == ""
    assert get_team_prompt("nobody", loader=_Loader()) == ""


def test_a_description_is_its_first_sentence_cut_at_a_word():
    assert short_description("Wiki Gardener & Knowledge Weaver. Triages inbox notes.") == "Wiki Gardener & Knowledge Weaver."
    long = "A motivational yet critical daily planner that aligns daily intentions with long-term projects and priorities."
    assert short_description(long) == "A motivational yet critical daily planner that aligns daily intentions with long-term…"
    assert short_description(None) == ""


# --- the open-passes block -------------------------------------------------------

NOW = datetime(2026, 11, 12, 17, 30, tzinfo=get_local_timezone()).timestamp()


def _pass(to_agent="day-planner", to_channel="day-planning", brief="Book the dentist for Tuesday.", at=NOW - 14 * 60,
          from_agent="goal-setter", from_channel="project-planning", via=PASS):
    """A capsule Grace's reply in #project-planning parked: a pass, by default, 14 minutes ago."""
    return HandoffCapsuleStore().park(from_agent=from_agent, from_channel=from_channel, to_agent=to_agent,
                                      to_channel=to_channel, capsule="<handoff_context/>", brief=brief, via=via,
                                      now=at)


def _open_passes(agent="goal-setter", channel="project-planning", loader=None):
    return get_open_passes_prompt(agent, channel, loader=loader or _Loader(), now=NOW)


def _lines(block):
    return [line for line in block.splitlines() if line.startswith("- ")]


def test_the_block_lists_this_conversations_passes_oldest_first():
    _pass()
    _pass(to_agent="fitness-coach", to_channel="fitness-coach", brief="Plan a lighter week.", at=NOW - 5 * 60)
    block = _open_passes()
    assert block.startswith("<open_passes>") and block.endswith("</open_passes>")
    assert "they haven't picked them up yet" in block
    assert 'When the user is done with you, hand them over to the one that should continue with <switch to="ID"/>' in block
    assert "Don't pass the same thing again." in block
    assert _lines(block) == ["- Daisy [day-planner], passed 17:16: Book the dentist for Tuesday.",
                             "- Frankie [fitness-coach], passed 17:25: Plan a lighter week."]


def test_only_this_conversations_passes_that_still_wait_are_listed():
    _pass()
    _pass(from_agent="fitness-coach", from_channel="fitness-coach", to_agent="a", to_channel="a")  # another agent's
    _pass(from_channel="general", to_agent="b", to_channel="b")  # passed from another channel
    _pass(via=SWITCH, to_agent="c", to_channel="c")  # a switch: the user went along
    _pass(to_agent="d", to_channel="d", at=NOW - PASS_TTL_S - 1)  # expired
    _pass(to_agent="e", to_channel="e")
    HandoffCapsuleStore().take("e", "e", now=NOW)  # picked up
    assert _lines(_open_passes(channel="#Project-Planning")) == [
        "- Daisy [day-planner], passed 17:16: Book the dentist for Tuesday."]


def test_a_brief_is_one_line_cut_at_a_word_and_a_pass_without_one_has_no_colon():
    _pass(brief="Book the dentist.\nKeep mornings free.")  # a brief carried over from an earlier pass
    _pass(to_agent="fitness-coach", to_channel="fitness-coach", brief="")
    _pass(to_agent="x", to_channel="x", brief="word " * 100)
    daisy, frankie, long = _lines(_open_passes())
    assert daisy == "- Daisy [day-planner], passed 17:16: Book the dentist. Keep mornings free."
    assert frankie == "- Frankie [fitness-coach], passed 17:16"
    brief = long.split(": ", 1)[1]
    assert long.startswith("- x [x], passed 17:16: word word") and brief.endswith("word…")
    assert len(brief) <= PASS_BRIEF_CHARS + 1


def test_a_pass_from_another_day_says_which():
    at = NOW - 20 * 3600
    _pass(at=at)
    (line,) = _lines(_open_passes())
    assert line.startswith(f"- Daisy [day-planner], passed {datetime.fromtimestamp(at, get_local_timezone()):%a %H:%M}: ")


def test_no_block_without_a_pass_an_agent_or_a_channel():
    assert _open_passes() == ""
    _pass()
    assert _open_passes(agent="") == ""
    assert _open_passes(channel="") == ""
    assert _open_passes(agent="day-planner") == ""


def test_an_error_never_breaks_the_prompt(capsys):
    _pass()
    with patch("core.prompts.team.HandoffCapsuleStore.open_passes", side_effect=RuntimeError("locked")):
        assert _open_passes() == ""
    assert "No open passes for goal-setter in #project-planning: RuntimeError: locked" in capsys.readouterr().out

    class Broken(_Loader):
        def get_agent_config(self, agent_id):
            raise KeyError(agent_id)

    assert _open_passes(loader=Broken()) == ""
