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

A tier names an *intent*, not a vendor. Which concrete model serves that intent
depends on where the agent runs, so the tables below are keyed by provider:
`"model": "FLASH"` under `"provider": "local"` asks for the on-device model that
fills the everyday-reasoning slot. That is what makes moving an agent on-device
a one-key change, with its `model` line untouched.
"""
from typing import Optional

# --- Google / Gemini -----------------------------------------------------
# Capability tiers.
FLASH_LITE = "gemini-3.5-flash-lite"
FLASH = "gemini-3.6-flash"
PRO = "gemini-3.1-pro-preview"

# Multimodal image generation. Not interchangeable with the tiers above.
IMAGE = "gemini-3.1-flash-image-preview"

# --- On-device -----------------------------------------------------------
# Served by a local OpenAI-compatible server (LiteRT-LM). The id is a build
# artifact, date-stamped and specific to one machine's weights, which is
# exactly why it belongs here rather than in an agent.json.
LOCAL_GEMMA4_26B = "gemma4-26b-hw-q4_0-20260622"

# The tier name a call site uses when configuration is silent. Deliberately a
# tier and not a model id: a default expressed as a concrete name cannot follow
# the agent's provider, which is the same hardcode this module exists to stop.
DEFAULT_AGENT_TIER = "FLASH_LITE"

# Rewriting agent output for speech. Deliberately the cheapest tier: it runs
# mid-turn, before the listener has heard anything, so the round-trip is
# audible as silence. Never raise this to PRO.
DEFAULT_VERBALIZER_MODEL = FLASH_LITE

# Browser automation needs to read rendered pages, so it needs the strongest
# multimodal reasoning rather than the cheapest.
DEFAULT_BROWSER_MODEL = PRO

# The names configuration may use. Keyed by the constant's own name so the two
# cannot drift: adding a tier above makes it available to config immediately.
GOOGLE_TIERS = {
    "FLASH_LITE": FLASH_LITE,
    "FLASH": FLASH,
    "PRO": PRO,
    "IMAGE": IMAGE,
}

# One model answers every tier today, because the device serves one model. That
# is not a placeholder to be tidied away: this table is the seam a second
# on-device build lands in, and it keeps the artifact name out of every config
# that wants to run locally. The distinctness that holds for GOOGLE_TIERS
# therefore does not apply here.
#
# IMAGE is deliberately absent. The device does not generate images, and
# mapping it to a text model would defer the failure to an image call site far
# from the config that asked for it; omitting it fails at graph-build time
# instead, next to the mistake.
LOCAL_TIERS = {
    "FLASH_LITE": LOCAL_GEMMA4_26B,
    "FLASH": LOCAL_GEMMA4_26B,
    "PRO": LOCAL_GEMMA4_26B,
}

PROVIDER_TIERS = {
    "google": GOOGLE_TIERS,
    "local": LOCAL_TIERS,
}

# Retained under its original name: `TIERS` is re-exported from `core.util` and
# read by callers that predate the per-provider split.
TIERS = GOOGLE_TIERS

# Used when an agent's config omits `model` and names no provider.
DEFAULT_AGENT_MODEL = GOOGLE_TIERS[DEFAULT_AGENT_TIER]


def _looks_like_a_tier(value: str) -> bool:
    """Whether the author meant a tier name rather than a literal model id.

    Every provider model id we use carries a version number. A bare word with
    no digits is an attempt at a tier -- which matters because it lets a typo
    be reported as a typo instead of being forwarded to the API as if it were
    a real model.
    """
    return not any(character.isdigit() for character in value)


def tiers_for(provider: Optional[str]) -> dict:
    """The tier table governing `provider`.

    An unrecognised provider gets the Google table. That is the right fallback
    rather than an error: `ollama` configs name their models literally
    (`gemma:4b`), so they never look a tier up, and failing here would break a
    provider that has no tier vocabulary to offer.
    """
    return PROVIDER_TIERS.get(provider or "google", GOOGLE_TIERS)


def resolve_model(value: Optional[str], provider: str = "google") -> str:
    """Turns a configured `model` value into a concrete model id for `provider`.

    Accepts a tier name (`FLASH_LITE`, case- and separator-insensitive) or a
    literal model id, which passes through so a specific version can still be
    pinned when there is a reason to.

    Raises `ValueError` on something that looks like a tier but is not one *for
    this provider*. Falling back would be worse than failing: a mistyped `PRO`
    would quietly run that agent on the cheapest model, and nothing downstream
    would report it -- the agent would simply be a bit worse at its job. The
    same reasoning makes `IMAGE` raise under `local`, where no such model
    exists, rather than resolving to something that cannot do the job.
    """
    tiers = tiers_for(provider)

    if not value or not str(value).strip():
        return tiers[DEFAULT_AGENT_TIER]

    text = str(value).strip()
    key = text.upper().replace("-", "_")
    if key in tiers:
        return tiers[key]

    if _looks_like_a_tier(text):
        raise ValueError(
            f"Unknown model tier {text!r} for provider {provider!r}. Use one of "
            f"{', '.join(sorted(tiers))}, or a literal model id."
        )

    return text
