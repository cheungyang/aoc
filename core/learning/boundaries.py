"""The learning analyst's boundaries, written in code: what it may hold, read and propose.

The "lethal trifecta" is private data, untrusted content, and a way to send data
out. The analyst (Kaizen 🪞, agents/agent-learner/) reads every conversation
(private data) and the tool output inside them (untrusted content) by design: an
email or a web page can carry instructions. So it must never get the third:

    no web, no email, no agent_call to agents that act, no follow-ups (they post
    later), no file writes, and no channel but #learning-lab

and its own conversations stay out of the proactive team (`"taps": false`):
they quote everyone else's, health and finance included.

Its one write is `submit_proposal`, which saves a proposal in memory.db and edits
nothing: an approved proposal is applied by Aki (agent-designer), never by it.
Every agent may write its own vault folder (ToolsLoader's default grant); the
analyst's agent.json refuses that with a deny key, `"!pkm/": ["@change"]`, so
even its own FEEDBACK and MEMORY change only through the nightly dream.

This is an allowlist, not a list of what's forbidden: a tool added to the repo
tomorrow is refused until it's added here, on purpose. `violations()` checks the
analyst's agent.json and the permission map it runs with (its own tools, its
graph's and its skills', as ToolsLoader merges them); tests/core/learning/
test_boundaries.py runs it over agents/agent-learner/, so a grant that breaks the
boundary fails the build.

    READABLE    the only places its filesystem grants may reach
    PROPOSABLE  where a proposal may change text: prompt files, never code
    FROZEN      read, never proposed: safety rules, and the analyst itself
    EVALUATOR   never even read: how the analyst is judged (core/learning/)
"""
import os
import posixpath
from typing import Any, Dict, Iterable, List, Mapping, Set

from core.permissions.bundles import expand_scope
from core.permissions.denies import is_deny

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# The analyst's agent id (Kaizen 🪞) and the one channel it lives in.
ANALYST = "agent-learner"
HOME_CHANNEL = "learning-lab"

# Every tool the analyst may hold. Anything else is refused.
ALLOWED_TOOLS = frozenset({
    "friction_query",   # reads the learning tables
    "submit_proposal",  # saves a proposal in memory.db; edits no file
    "filesystem",       # reading only: READ_ACTIONS
    "load_skill",       # reads its own skills' text
})
# What its filesystem grants may allow: reading, never writing, moving or deleting.
READ_ACTIONS = frozenset({"read", "read_image", "ls", "find", "grep"})

# Where its filesystem grants may reach, and nowhere else: the agents' files, the skills,
# the engine's templates, and the notes it needs from the user's knowledge base.
READABLE = ("agents/", "skills/", "core/prompts/templates/", "pkm/agents/", "pkm/wiki/memory/")

# Where a proposal may land. In the repo, which is public, prompt text only: an agent's
# persona, a skill's SKILL.md, the engine's templates. In the vault, an agent's RULES.md
# and FEEDBACK.md, where a lesson that needs a personal detail goes.
PERSONA_FILES = ("AGENTS.md", "SOUL.md", "INSTRUCTIONS.md")
VAULT_FILES = ("RULES.md", "FEEDBACK.md")
TEMPLATES = "core/prompts/templates/"

# Read, never proposed. A safety rule can lose a test of your preference and still be right,
# so preference must never be what tunes it; and the analyst never proposes changes to itself.
FROZEN = (
    "core/prompts/templates/browser/handoff.md",  # stop before paying or booking: the user clicks
    f"agents/{ANALYST}/",
    f"pkm/agents/{ANALYST}/",
    "skills/friction_review/",
    "skills/prompt_experiment/",
)
# The evaluator: the detectors, the checks, the judge and their prompts. Never readable
# and never proposed, so the analyst can't learn to please it.
EVALUATOR = ("core/learning/",)


def _vaults() -> List[str]:
    """Where the vault is: the repo's pkm/ link, and the configured PKM folder."""
    found = [os.path.realpath(os.path.join(REPO, "pkm"))]
    try:
        from core.config.settings import Config
        found.append(os.path.realpath(os.path.expanduser(Config().pkm_dir)))
    except Exception:
        pass
    return found


def grant_path(key: Any) -> str:
    """A filesystem grant's path as the repo names it: an absolute path inside the vault
    (ToolsLoader's default grant of the agent's own folder) becomes pkm/..."""
    path = os.path.expanduser(str(key).replace("<agent_id>", ANALYST))
    if not os.path.isabs(path):
        return path
    real = os.path.realpath(path)
    for vault in _vaults():
        if real == vault or real.startswith(vault + os.sep):
            return posixpath.join("pkm", os.path.relpath(real, vault).replace(os.sep, "/")).rstrip("/.")
    return real


def filesystem_grants(scope: Mapping[str, Any]) -> Dict[str, Set[str]]:
    """Each path the analyst's filesystem grant reaches, with the actions left once its
    deny keys (`"!pkm/": ["@change"]`) have refused theirs."""
    expanded = expand_scope("filesystem", dict(scope))
    denies = [(repo_path(key[1:]), set(actions or [])) for key, actions in expanded.items() if is_deny(key)]
    reach: Dict[str, Set[str]] = {}
    for key, actions in expanded.items():
        if is_deny(key):
            continue
        path = grant_path(key)
        allowed = set(actions or [])
        for root, refused in denies:
            if root and within(path, [root + "/"]):
                allowed -= refused
        if allowed:
            reach[path] = reach.get(path, set()) | allowed
    return reach


def repo_path(path: Any) -> str:
    """`path` as the repo names it: forward slashes, `.` and `..` resolved, no trailing slash.
    "" for a path that is absolute or leaves the repo."""
    path = posixpath.normpath(str(path or "").replace("\\", "/"))
    if path.startswith("/") or path == ".." or path.startswith("../") or path == ".":
        return ""
    return path


def within(path: Any, roots: Iterable[str]) -> bool:
    """Whether `path` is one of `roots` (each ending in "/") or inside one."""
    path = repo_path(path)
    return bool(path) and any(path == root.rstrip("/") or path.startswith(root) for root in roots)


def why_not_proposable(path: Any) -> str:
    """Why a proposal may not change `path`, in words; "" when it may."""
    clean = repo_path(path)
    if not clean:
        return f"{path!r} is outside the repo"
    if within(clean, EVALUATOR):
        return f"{clean} is the evaluator's: never read, never proposed"
    if within(clean, FROZEN):
        return f"{clean} is frozen: a safety rule, or the analyst itself"
    parts = clean.split("/")
    if parts[0] == "agents" and len(parts) == 3 and parts[2] in PERSONA_FILES:
        return ""
    if parts[0] == "skills" and len(parts) == 3 and parts[2] == "SKILL.md":
        return ""
    if clean.startswith(TEMPLATES) and clean.endswith(".md"):
        return ""
    if parts[:2] == ["pkm", "agents"] and len(parts) == 4 and parts[3] in VAULT_FILES:
        return ""
    return (f"{clean} isn't prompt text a proposal may change: an agent's {', '.join(PERSONA_FILES)}, "
            f"a skill's SKILL.md, a template under {TEMPLATES}, or an agent's {' or '.join(VAULT_FILES)} in pkm")


def proposable(path: Any) -> bool:
    """Whether a proposal may change `path` (`why_not_proposable`)."""
    return not why_not_proposable(path)


def in_vault(path: Any) -> bool:
    """Whether `path` is the user's vault (pkm/), which never goes public."""
    return within(path, ("pkm/",))


def violations(config: Mapping[str, Any], tools: Mapping[str, Any]) -> List[str]:
    """What breaks the boundary, in words; [] when nothing does.

    `config` is the analyst's agent.json; `tools` the permission map it runs with
    (ToolsLoader's merge of its own, its graph's and its skills' tools).
    """
    found = []
    for tool in sorted(set(tools) - ALLOWED_TOOLS):
        found.append(f"may not hold `{tool}`: only {', '.join(sorted(ALLOWED_TOOLS))}")
    if "filesystem" in tools:
        scope = tools["filesystem"]
        if not scope:
            found.append("`filesystem` with no scope allows every action; grant paths with @read")
        elif isinstance(scope, (list, tuple, set)):
            found.append(f"`filesystem` must name its paths, inside {', '.join(READABLE)}")
        elif isinstance(scope, Mapping):
            reach = filesystem_grants(scope)
            writes = sorted(set().union(*reach.values()) - READ_ACTIONS) if reach else []
            if writes:
                found.append(f"`filesystem` may only read, not {', '.join(writes)}")
            outside = sorted(path for path in reach if not within(path, READABLE))
            if outside:
                found.append(f"`filesystem` may reach only {', '.join(READABLE)}, not {', '.join(outside)}")
    channels = list(config.get("channels") or [])
    if channels != [HOME_CHANNEL]:
        found.append(f"lives in #{HOME_CHANNEL} only, not {channels}")
    hosts = list(config.get("channel_hosts") or [])
    if any(host != HOME_CHANNEL for host in hosts):
        found.append(f"may host #{HOME_CHANNEL} only, not {hosts}")
    proactivity = config.get("proactivity") or {}
    if not config.get("stateless") and proactivity.get("follow_ups") is not False:
        found.append('follow-ups post later: set "proactivity": {"follow_ups": false}')
    if not config.get("stateless") and proactivity.get("taps") is not False:
        found.append('its talk quotes everyone\'s conversations: set "proactivity": {"taps": false}')
    return found
