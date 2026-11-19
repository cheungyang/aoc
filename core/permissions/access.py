"""Who may use an agent, when more than one person uses the system.

People are listed in each agent.json's `access`, and mapped to their Discord
accounts in the environment, next to the bot tokens:

    DISCORD_USER_OWNER=123456789012345678     # several accounts: comma-separated
    DISCORD_USER_PLAYER2=234567890123456789

    "access": ["owner", "player2"]

Whoever an agent lists may do everything with it: talk to it, typed or
spoken, vote in its polls, open threads, reset or compact its channel. Anyone
else gets nothing from it: its channel is hidden, and they can't call it. An
agent without `access` is the owner's alone, as every agent was before. The
owner is the person named `owner`: DISCORD_USER_OWNER. Admin is whoever may
use the Concierge (`main`): the owner, unless its agent.json lists others.

Memory isn't split by person: an agent keeps one memory, one feedback file and
one context, whoever talks to it. In a household (`household_mode`) the stamps
on user messages say who is talking, and an agent more than one person may use
(`is_household`) answers its channel one turn at a time.

With no DISCORD_USER_* set, nothing changes: every account is the owner
(`person_for`), the owner may use every agent (`can`), and the stamps on user
messages name nobody. The startup log says when `access` lists wait for
people to be configured (`problems`).

Everything here is pure: it reads the environment, on every call, and the
config it is given, so a test can pin the whole matrix.
"""
import os
import re
from typing import Any, Dict, FrozenSet, List, Mapping, Optional

# The Concierge: whoever may use it is admin, and gives the system's own
# commands (!restart, !python, !claim, ...).
ADMIN_AGENT = "main"

# DISCORD_USER_<NAME>: a person's Discord account id(s).
USER_ENV_PREFIX = "DISCORD_USER_"
# The owner's name, so DISCORD_USER_OWNER: whose the agents without `access`
# are. With nobody configured, every account is the owner.
OWNER = "owner"

# A person's name, as `access` lists and stamps write it.
NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


def _clean(name: Any) -> str:
    return str(name).strip().lower() if name is not None else ""


def people() -> Dict[str, FrozenSet[str]]:
    """Each configured person's Discord account ids, by name.

    Read from DISCORD_USER_<NAME>; a name that isn't a valid one, or a variable
    with no id in it, configures nobody.
    """
    found: Dict[str, FrozenSet[str]] = {}
    for key, value in os.environ.items():
        if not key.startswith(USER_ENV_PREFIX):
            continue
        name = _clean(key[len(USER_ENV_PREFIX):])
        ids = frozenset(part.strip() for part in (value or "").split(",") if part.strip())
        if ids and NAME.match(name):
            found[name] = ids
    return found


def owner() -> str:
    """Whose the agents without `access` are: the person named `owner`
    (DISCORD_USER_OWNER)."""
    return OWNER


def household_mode() -> bool:
    """Whether more than one person is configured: then a user message's stamp
    names who wrote it, and the agents know who they're talking to."""
    return len(people()) > 1


def person_for(user_id: Any) -> Optional[str]:
    """The person a Discord account belongs to, or None if it's nobody's.

    With nobody configured, every account is the owner, as every message was
    the owner's before. Once anyone is configured, an account nobody lists is
    a guest, and the caller ignores it: None here never means the system (see
    `can`).
    """
    configured = people()
    if not configured:
        return owner()
    key = str(user_id).strip() if user_id is not None else ""
    if not key:
        return None
    return next((name for name, ids in sorted(configured.items()) if key in ids), None)


def access_of(config: Optional[Mapping]) -> FrozenSet[str]:
    """Who may use the agent `config` describes: its `access` list, names
    lowercase. Without a list, or with an empty one, the owner alone."""
    listed = config.get("access") if isinstance(config, Mapping) else None
    names = frozenset(_clean(name) for name in listed if _clean(name)) if isinstance(listed, list) else frozenset()
    return names or frozenset({owner()})


def can(person: Optional[str], config: Optional[Mapping]) -> bool:
    """Whether `person` may use the agent `config` describes.

    A turn nobody started -- a schedule, a delivery, a job: `person` None --
    acts for the agent itself, and nothing here refuses it. An account that
    belongs to nobody is the caller's to turn away before asking
    (`person_for`), never passed in as None. With nobody configured every
    account is the owner, who may use every agent, listed or not: a list added
    before DISCORD_USER_* would otherwise lock the one person there is out.
    """
    if person is None:
        return True
    name = _clean(person)
    if not people() and name == owner():
        return True
    return name in access_of(config)


def is_household(config: Optional[Mapping]) -> bool:
    """Whether more than one person may use the agent: then its channel is a
    conversation they share, answered one turn at a time."""
    return len(access_of(config)) > 1


def validate_access(listed: Any) -> List[str]:
    """Every reason an agent.json `access` list is malformed, or an empty list.
    Whether its names are configured people depends on the environment, so
    `problems` reports that at startup instead."""
    if not isinstance(listed, list) or not listed:
        return ['\'access\' must be a list of the people who may use the agent, e.g. ["owner", "player2"]']
    return [f"access: {name!r} is not a person's name (lowercase letters, digits, '-' or '_')"
            for name in listed if not isinstance(name, str) or not NAME.match(name)]


def problems(configs: Mapping[str, Mapping]) -> List[str]:
    """What's wrong with the household's configuration, for the startup log:
    with nobody configured, only that some agent has an `access` list anyway.
    `configs` is every agent's config, by id."""
    configured = people()
    listing = {agent_id: config.get("access") for agent_id, config in configs.items()
               if isinstance(config, Mapping) and isinstance(config.get("access"), list)}
    if not configured:
        if not listing:
            return []
        listed = sorted(listing)
        return [f"{', '.join(listed)} {'has' if len(listed) == 1 else 'have'} an 'access' list, but no "
                f"{USER_ENV_PREFIX}<NAME> is set, so every account is still the owner, who may use every "
                f"agent. Set {USER_ENV_PREFIX}{OWNER.upper()}, and {USER_ENV_PREFIX}<NAME> for everyone else."]
    found = []
    if OWNER not in configured:
        found.append(f"No {USER_ENV_PREFIX}{OWNER.upper()} is set, so the agents without an 'access' list, "
                     f"and admin, belong to no account. Set it to your Discord account id.")
    for agent_id, listed in sorted(listing.items()):
        unknown = sorted({_clean(name) for name in listed if _clean(name) and _clean(name) not in configured})
        if unknown:
            found.append(f"{agent_id}: access names {', '.join(unknown)}, with no "
                         f"{USER_ENV_PREFIX}<NAME> account; they get nothing from it.")
    return found
