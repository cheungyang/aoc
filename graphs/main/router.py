"""Deciding where a turn goes, before any model is asked.

The concierge's job used to be mostly lookup dressed up as reasoning: in a
channel where exactly one agent is allowed to answer, "which agent should handle
this?" has one possible answer, and asking a model to produce it costs two
round-trips and can still be wrong. This module answers it from configuration.

Nothing here performs work or touches the network. `decide()` is a pure function
of (prompt, context, config) so the routing table is something a test can pin.
"""
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Union

Prompt = Union[str, list]

# Route targets.
DIRECT_CALL = "direct_call"
CONCIERGE = "concierge"

# Message openers that mean "don't route this, answer it here". Declared in the
# orchestrator's `agent.json` under this key, because which phrases belong in it
# depends on which tools that agent holds -- `kill` earns its place only because
# main has `job_kill` -- and that is config, not logic.
#
# One list, two forms, because operationally they do the same thing: skip the
# specialist, build the concierge. They differ only in whether the marker is
# part of the request, and the bracket says which:
#
#   "[main] what did we decide?"  -> bracketed: routing syntax, stripped.
#                                    The concierge reads "what did we decide?"
#   "kill job abc123"             -> bare word: part of the request, kept.
#                                    The concierge needs the verb to act on it.
#
# The bracket also settles anchoring. `]` is its own boundary, so a bracketed
# prefix matches on sight; a bare word must be followed by a space or end the
# message, or "killing time before the flight" would be read as a command.
# Bare entries should stay short and few -- a loose list silently steals
# ordinary messages from the specialist, which is worse than missing a few.
PREFIX_CONFIG_KEY = "concierge_prefixes"


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


def leading_text(prompt: Prompt) -> str:
    """The text a human typed, ignoring attachments.

    A multimodal prompt is a list of parts; the escape prefix and control verbs
    can only appear in its leading text part.
    """
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        for part in prompt:
            if isinstance(part, str):
                return part
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text") or ""
    return ""


def _replace_leading_text(prompt: Prompt, new_text: str) -> Prompt:
    """Rewrites the leading text part, preserving any attachments."""
    if isinstance(prompt, str):
        return new_text
    if isinstance(prompt, list):
        out = list(prompt)
        for i, part in enumerate(out):
            if isinstance(part, str):
                out[i] = new_text
                return out
            if isinstance(part, dict) and part.get("type") == "text":
                out[i] = {**part, "text": new_text}
                return out
        return [{"type": "text", "text": new_text}] + out
    return prompt


def concierge_prefixes(agent_config: dict) -> List[str]:
    """The configured openers, ignoring blanks and non-strings."""
    raw = agent_config.get(PREFIX_CONFIG_KEY) or []
    if isinstance(raw, str):
        raw = [raw]
    return [p.strip() for p in raw if isinstance(p, str) and p.strip()]


def is_bracketed(prefix: str) -> bool:
    """Whether the prefix is routing syntax rather than part of the request."""
    return prefix.startswith("[") and prefix.endswith("]")


def match_concierge_prefix(prompt: Prompt, prefixes) -> Optional[Tuple[str, Prompt]]:
    """Finds the opener that claims this message for the concierge.

    Returns `(matched_prefix, prompt)` where the prompt is what the concierge
    should read -- with a bracketed marker removed, since it is addressing
    syntax and not a request -- or None if no prefix matched.

    Returning the match rather than a bool is what lets `decide` name the rule
    that fired in its `reason`, which is the only useful thing to have in the
    log when a message goes somewhere surprising.
    """
    text = leading_text(prompt).lstrip()
    if not text:
        return None
    lowered = text.lower()

    for prefix in prefixes:
        needle = prefix.lower()
        if is_bracketed(needle):
            if lowered.startswith(needle):
                return prefix, _replace_leading_text(prompt, text[len(needle):].lstrip())
        elif lowered == needle or lowered.startswith(needle + " "):
            return prefix, prompt

    return None


def routing_enabled(agent_id: str, channel_name: str, agent_config: dict, loader: Any) -> bool:
    """Whether this agent is in the business of routing at all.

    Derived rather than declared. `graphs/main` is the default graph for every
    agent in the system, so a flag in one agent's config would not have scoped
    this -- and a flag in *every* agent's config is a migration. Two facts
    already recorded elsewhere say the same thing:

      - holding `agent_call` means this agent's purpose includes delegating;
      - hosting the channel means untagged messages arrive here by default.

    Only an orchestrator satisfies both. A specialist holds no `agent_call`, and
    an agent that hosts its own channel has no other eligible agent in it anyway.
    """
    if not agent_id or not channel_name:
        return False
    if "agent_call" not in (agent_config.get("tools") or {}):
        return False
    return loader.hosts_channel(agent_id, channel_name)


def decide(prompt: Prompt, agent_id: str, channel_name: str, agent_config: dict, loader: Any) -> RouteDecision:
    """Resolves a turn to a route. Pure; performs no delegation."""
    if not routing_enabled(agent_id, channel_name, agent_config, loader):
        return RouteDecision(CONCIERGE, prompt=prompt, reason="routing not enabled for this agent/channel")

    matched = match_concierge_prefix(prompt, concierge_prefixes(agent_config))
    if matched is not None:
        prefix, for_concierge = matched
        return RouteDecision(CONCIERGE, prompt=for_concierge, reason=f"concierge prefix '{prefix}'")

    eligible: List[str] = loader.eligible_agents_for_channel(channel_name, exclude_agent_id=agent_id)
    if len(eligible) == 1:
        return RouteDecision(
            DIRECT_CALL,
            agent_id=eligible[0],
            prompt=prompt,
            reason=f"sole eligible agent in #{channel_name}",
        )

    return RouteDecision(
        CONCIERGE,
        prompt=prompt,
        reason=f"{len(eligible)} eligible agents in #{channel_name}",
    )
