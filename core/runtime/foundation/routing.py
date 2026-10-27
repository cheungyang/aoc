"""Deciding where a turn goes, before any model is asked.

The concierge's job used to be mostly lookup dressed up as reasoning: in a
channel where exactly one agent is allowed to answer, "which agent should handle
this?" has one possible answer, and asking a model to produce it costs two
round-trips and can still be wrong. This module answers it from configuration.

Routing is deterministic only. A message in a channel with one agent goes to
that agent; a message in a channel several agents share (#general) gets a fixed
line from the host, also without a model call. Having a model guess the agent
there was retired: nearly every message is typed in its agent's own channel,
and #general is used for commands.

A message that calls another agent by name -- "Ted, create a syllabus on THIS
topic" in #pkm-wiki, "Daisy, ..." in #general -- is a hand-off: it goes to
that agent in its home channel, and the conversation continues there. Who a
message calls is addressing.py's rule, read from the words alone.

Nothing here performs work or touches the network. `decide()` is a pure function
of (prompt, context, config) so the routing table is something a test can pin.
`channel_hosts_for()` and `answering_agent()` answer the same question for a
channel rather than a message, for the voice focus and the channel inbox.
"""
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Union

from core.runtime.foundation.addressing import addressed_agent, aliases_for

Prompt = Union[str, list]

# Route targets.
DIRECT_CALL = "direct_call"   # the channel's one agent answers, no model on the host
CONCIERGE = "concierge"       # the executing agent answers, with its own model
FIXED = "fixed"               # the host answers with a fixed line, no model at all
HANDOFF = "handoff"           # the agent the message names answers, in its own channel

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
    is guesswork. `channel` is where a hand-off continues: the named agent's
    home channel.
    """
    target: str
    agent_id: Optional[str] = None
    prompt: Prompt = ""
    reason: str = ""
    channel: Optional[str] = None

    @property
    def is_direct(self) -> bool:
        return self.target == DIRECT_CALL

    @property
    def is_handoff(self) -> bool:
        return self.target == HANDOFF


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
           source: str = CHANNEL_MESSAGE) -> RouteDecision:
    """Resolves a turn to a route. Pure; performs no delegation.

    Only the hand-off rule reads `prompt`; it travels with the decision, so what
    is routed and where it goes stay one value. `source` is the turn's
    `ExecutionContext.source`; the default asks about a message posted in the
    channel, as the voice focus and the channel inbox do. A message calling
    another agent by name goes to that agent's home channel, ahead of the other
    rules.
    """
    if not routing_enabled(agent_id, channel_name, agent_config, loader, source=source):
        return RouteDecision(CONCIERGE, prompt=prompt, reason="routing not enabled for this agent/channel")

    eligible: List[str] = loader.eligible_agents_for_channel(channel_name, exclude_agent_id=agent_id)
    handoff = _handoff(prompt, agent_id, channel_name, eligible, loader)
    if handoff is not None:
        return handoff

    if len(eligible) == 1:
        return RouteDecision(
            DIRECT_CALL,
            agent_id=eligible[0],
            prompt=prompt,
            reason=f"sole eligible agent in #{channel_name}",
        )

    return RouteDecision(FIXED, prompt=prompt, reason=f"{len(eligible)} eligible agents in #{channel_name}")


def _handoff(prompt: Prompt, host_id: str, channel_name: str, eligible: List[str],
             loader: Any) -> Optional[RouteDecision]:
    """A hand-off, if `prompt` calls by name an agent other than the one that
    answers here, and that agent has a home channel to continue in."""
    if not prompt:
        return None  # a question about the channel, not a message (answering_agent)
    answers_here = eligible[0] if len(eligible) == 1 else None
    address = addressed_agent(prompt, addressable_agents(loader, exclude=(host_id, answers_here)))
    if address is None:
        return None
    home = home_channel(address.agent_id, loader)
    if not home or home == channel_name.lower():
        return None
    return RouteDecision(
        HANDOFF,
        agent_id=address.agent_id,
        prompt=prompt,
        channel=home,
        reason=f"{address.form} address to {address.name!r} in #{channel_name}; continues in #{home}",
    )


# --- who a message can call by name, and where it continues --------------------------

def addressable_agents(loader: Any, exclude: Iterable[Optional[str]] = ()) -> Dict[str, str]:
    """Alias -> agent id, for the agents a message can call by name (addressing.py).

    Every agent that keeps a conversation in channels of its own: not one
    reachable everywhere (`channels: ["*"]`: the Concierge, the graph workers),
    which owns no channel, nor a stateless one, which keeps nothing to continue.
    """
    skip = {agent_id for agent_id in exclude if agent_id}
    agents = {}
    for agent_id in loader.list_agent_ids():
        config = loader.get_agent_config(agent_id) or {}
        channels = [str(c).lower() for c in (config.get("channels") or [])]
        if agent_id in skip or not channels or "*" in channels or config.get("stateless"):
            continue
        agents[agent_id] = config
    return aliases_for(agents)


def home_channel(agent_id: str, loader: Any) -> Optional[str]:
    """Where `agent_id` keeps its conversation: the first of its `channels` in
    which it answers untagged messages. The voice focus sends its name to the
    same channel (core/voice/room/focus.py). None for an agent that answers no
    channel on its own."""
    config = loader.get_agent_config(agent_id) or {}
    for channel in [str(c).lstrip("#").lower() for c in (config.get("channels") or [])]:
        if channel == "*":
            return None
        if any(answering_agent(host, channel, loader) == agent_id for host in channel_hosts_for(channel, loader)):
            return channel
    return None


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
    can't drift from them: the sole eligible agent, else the host itself, with
    its fixed line.
    """
    decision = decide(
        prompt="",
        agent_id=host_id,
        channel_name=channel,
        agent_config=loader.get_agent_config(host_id) or {},
        loader=loader,
    )
    return decision.agent_id if decision.is_direct and decision.agent_id else host_id
