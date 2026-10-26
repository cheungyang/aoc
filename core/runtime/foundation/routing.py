"""Deciding where a turn goes, before any model is asked.

The concierge's job used to be mostly lookup dressed up as reasoning: in a
channel where exactly one agent is allowed to answer, "which agent should handle
this?" has one possible answer, and asking a model to produce it costs two
round-trips and can still be wrong. This module answers it from configuration.

Routing is deterministic only. A message in a channel with one agent goes to
that agent; a message in a channel several agents share (#general) gets a fixed
line from the host, also without a model call. Having a model guess the agent
there was retired: nearly every message is typed in its agent's own channel,
and #general is used for commands. `ROUTING_LLM_FALLBACK`
(core/config/settings.py) brings the guess back, as a rollback switch.

Nothing here performs work or touches the network. `decide()` is a pure function
of (prompt, context, config) so the routing table is something a test can pin.
`channel_hosts_for()` and `answering_agent()` answer the same question for a
channel rather than a message, for the voice focus and the channel inbox.
"""
from dataclasses import dataclass
from typing import Any, List, Optional, Union

Prompt = Union[str, list]

# Route targets.
DIRECT_CALL = "direct_call"   # the channel's one agent answers, no model on the host
CONCIERGE = "concierge"       # the executing agent answers, with its own model
FIXED = "fixed"               # the host answers with a fixed line, no model at all

# `ExecutionContext.source` of turns addressed to the agent running them: a
# delegation (`tool`), a job, a scheduled run, a graph's worker. Such a turn is
# that agent's own to answer, whatever channel it names, so it is never routed:
# the dream standup reaches the Concierge by delegation, in #general, and must
# reach its model. Every other turn -- a message typed or spoken in a channel,
# or a replay of one (scripts/voice_dryrun.py) -- is.
ADDRESSED_SOURCES = frozenset({"tool", "job", "scheduled", "graph"})

# The source of a message posted in a channel, typed or spoken (a voice turn
# runs on its channel's `discord` session): what the voice focus and the channel
# inbox ask about.
CHANNEL_MESSAGE = "discord"


@dataclass(frozen=True)
class RouteDecision:
    """Where a turn goes, and why.

    `reason` exists for logs and tests: when routing is wrong, the useful
    question is which rule fired, and reconstructing that from the target alone
    is guesswork.
    """
    target: str
    agent_id: Optional[str] = None
    prompt: Prompt = ""
    reason: str = ""

    @property
    def is_direct(self) -> bool:
        return self.target == DIRECT_CALL


def routing_enabled(agent_id: str, channel_name: str, agent_config: dict, loader: Any,
                    source: str = CHANNEL_MESSAGE) -> bool:
    """Whether this turn is the router's to place.

    Derived rather than declared. `graphs/main` is the default graph for every
    agent in the system, so a flag in one agent's config would not have scoped
    this -- and a flag in *every* agent's config is a migration. Three facts
    already recorded elsewhere say it:

      - the turn arrived in the channel, not addressed to this agent by name
        (`source` not in `ADDRESSED_SOURCES`);
      - the agent hosts the channel, so untagged messages arrive here by default;
      - the agent doesn't list the channel in its own `channels`: an agent that
        answers in a channel it hosts has nobody to hand it to.

    Only the Concierge satisfies all three today: it hosts every channel and
    lists none (its `channels` is `["*"]`).
    """
    if source in ADDRESSED_SOURCES or not agent_id or not channel_name:
        return False
    own = [str(c).lower() for c in (agent_config.get("channels") or [])]
    if channel_name.lower() in own:
        return False
    return loader.hosts_channel(agent_id, channel_name)


def decide(prompt: Prompt, agent_id: str, channel_name: str, agent_config: dict, loader: Any, *,
           source: str = CHANNEL_MESSAGE, llm_fallback: bool = False) -> RouteDecision:
    """Resolves a turn to a route. Pure; performs no delegation.

    No rule reads `prompt`; it travels with the decision, so what is routed and
    where it goes stay one value. `source` is the turn's `ExecutionContext.source`;
    the default asks about a message posted in the channel, as the voice focus
    and the channel inbox do. `llm_fallback` is `ROUTING_LLM_FALLBACK`: with it
    on, a channel without one agent reaches the host's model instead of the
    fixed line.
    """
    if not routing_enabled(agent_id, channel_name, agent_config, loader, source=source):
        return RouteDecision(CONCIERGE, prompt=prompt, reason="routing not enabled for this agent/channel")

    eligible: List[str] = loader.eligible_agents_for_channel(channel_name, exclude_agent_id=agent_id)
    if len(eligible) == 1:
        return RouteDecision(
            DIRECT_CALL,
            agent_id=eligible[0],
            prompt=prompt,
            reason=f"sole eligible agent in #{channel_name}",
        )

    reason = f"{len(eligible)} eligible agents in #{channel_name}"
    if llm_fallback:
        return RouteDecision(CONCIERGE, prompt=prompt, reason=f"{reason}; ROUTING_LLM_FALLBACK is on")
    return RouteDecision(FIXED, prompt=prompt, reason=reason)


# --- who answers a channel, for the voice focus and the channel inbox ---------------

def channel_hosts_for(channel_name: str, loader: Any) -> List[str]:
    """Agent ids whose `channel_hosts` include `channel_name`, in loader order."""
    target = str(channel_name or "").lstrip("#").lower()
    if not target:
        return []
    hosts = []
    for agent_id in loader.list_agent_ids():
        config = loader.get_agent_config(agent_id) or {}
        if target in [str(h).lower() for h in (config.get("channel_hosts") or [])]:
            hosts.append(agent_id)
    return hosts


def answering_agent(host_id: str, channel: str, loader: Any) -> str:
    """Who answers an untagged message in `channel` hosted by `host_id`.

    The policy typed messages follow (`decide`), so voice and the channel inbox
    can't drift from them: the sole eligible agent, else the host itself (with
    its fixed line, or its model under `ROUTING_LLM_FALLBACK`).
    """
    decision = decide(
        prompt="",
        agent_id=host_id,
        channel_name=channel,
        agent_config=loader.get_agent_config(host_id) or {},
        loader=loader,
    )
    return decision.agent_id if decision.is_direct and decision.agent_id else host_id
