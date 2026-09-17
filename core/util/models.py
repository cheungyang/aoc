"""The model names this system uses, in one place.

Model identifiers are version strings that expire. They were previously written
inline at each call site, which made a provider bump a search-and-replace across
unrelated subsystems -- and the failure mode of missing one is not an error but
a voice pipeline quietly still running last quarter's model.

Two kinds of name live here, and the distinction matters when editing:

- **Tiers** (`FLASH_LITE`, `FLASH`, `PRO`, `IMAGE`) name a capability/cost
  point. A call site that must be cheap and fast references the tier directly,
  because that is a property of the call, not a default someone might want to
  override.
- **Defaults** (`DEFAULT_AGENT_MODEL`) name a fallback used when configuration
  is silent. Changing this changes every agent that has not opted out.

They happen to point at the same string today. Keeping them separate is the
point: raising the agent default should not silently make every voice turn
slower, and collapsing them into one constant is how that happens by accident.

Per-agent `model` keys in `agents/*/agent.json` name a **tier**, not a version
string -- `"model": "FLASH_LITE"` -- so bumping a tier here updates every agent
that asked for it. Choosing a tier per agent is a real configuration decision;
pinning a version string in fourteen files is the hardcode this replaces.
"""
from typing import Optional

# Capability tiers.
FLASH_LITE = "gemini-3.5-flash-lite"
FLASH = "gemini-3.6-flash"
PRO = "gemini-3.1-pro-preview"

# Multimodal image generation. Not interchangeable with the tiers above.
IMAGE = "gemini-3.1-flash-image-preview"

# Used when an agent's config omits `model`.
DEFAULT_AGENT_MODEL = FLASH_LITE

# Rewriting agent output for speech. Deliberately the cheapest tier: it runs
# mid-turn, before the listener has heard anything, so the round-trip is
# audible as silence. Never raise this to PRO.
DEFAULT_VERBALIZER_MODEL = FLASH_LITE

# Browser automation needs to read rendered pages, so it needs the strongest
# multimodal reasoning rather than the cheapest.
DEFAULT_BROWSER_MODEL = PRO

# The names configuration may use. Keyed by the constant's own name so the two
# cannot drift: adding a tier above makes it available to config immediately.
TIERS = {
    "FLASH_LITE": FLASH_LITE,
    "FLASH": FLASH,
    "PRO": PRO,
    "IMAGE": IMAGE,
}


def _looks_like_a_tier(value: str) -> bool:
    """Whether the author meant a tier name rather than a literal model id.

    Every provider model id we use carries a version number. A bare word with
    no digits is an attempt at a tier -- which matters because it lets a typo
    be reported as a typo instead of being forwarded to the API as if it were
    a real model.
    """
    return not any(character.isdigit() for character in value)


def resolve_model(value: Optional[str], default: str = DEFAULT_AGENT_MODEL) -> str:
    """Turns a configured `model` value into a concrete model id.

    Accepts a tier name (`FLASH_LITE`, case- and separator-insensitive) or a
    literal model id, which passes through so a specific version can still be
    pinned when there is a reason to.

    Raises `ValueError` on something that looks like a tier but is not one.
    Falling back would be worse than failing: a mistyped `PRO` would quietly
    run that agent on the cheapest model, and nothing downstream would report
    it -- the agent would simply be a bit worse at its job.
    """
    if not value or not str(value).strip():
        return default

    text = str(value).strip()
    key = text.upper().replace("-", "_")
    if key in TIERS:
        return TIERS[key]

    if _looks_like_a_tier(text):
        raise ValueError(
            f"Unknown model tier {text!r}. Use one of {', '.join(sorted(TIERS))}, "
            f"or a literal model id."
        )

    return text
