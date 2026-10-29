"""The team block (core/prompts/team.py): who else the user can talk to, and the
directive that hands them over."""
from core.prompts.team import get_team_prompt, short_description


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


def test_agents_that_dont_talk_with_the_user_get_none():
    assert get_team_prompt("main", loader=_Loader()) == ""
    assert get_team_prompt("graph-worker-low", loader=_Loader()) == ""
    assert get_team_prompt("nobody", loader=_Loader()) == ""


def test_a_description_is_its_first_sentence_cut_at_a_word():
    assert short_description("Wiki Gardener & Knowledge Weaver. Triages inbox notes.") == "Wiki Gardener & Knowledge Weaver."
    long = "A motivational yet critical daily planner that aligns daily intentions with long-term projects and priorities."
    assert short_description(long) == "A motivational yet critical daily planner that aligns daily intentions with long-term…"
    assert short_description(None) == ""
