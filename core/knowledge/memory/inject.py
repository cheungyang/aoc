"""Memory v2 as it appears in a system prompt.

Stored lines carry metadata for code; the prompt sees only the facts. Order,
most stable first: Profile → subscribed topics (by tag) → private memory →
feedback. Budgets are enforced on this rendered form.
"""
from typing import Dict, List, Optional

from core.knowledge.memory import store
from core.knowledge.memory.entries import FEEDBACK, PRIVATE


def memory_topics(config: Dict, pkm_dir: Optional[str] = None, agent_id: str = "") -> List[str]:
    """The agent's subscriptions that exist in TAGS.md, sorted. Unknown tags are
    ignored here (this runs every turn) and reported once at load time by
    `report_unknown_topics`."""
    wanted = config.get("memory_topics") or []
    if not wanted:
        return []
    known = set(store.tag_names(pkm_dir))
    return sorted(t for t in wanted if t in known)


def unknown_topics(config: Dict, pkm_dir: Optional[str] = None) -> List[str]:
    """Subscriptions missing from TAGS.md. Logged, not fatal: TAGS.md lives in
    the vault, so CI can't check it, and a typo shouldn't crash the bot."""
    wanted = config.get("memory_topics") or []
    if not wanted:
        return []
    known = set(store.tag_names(pkm_dir))
    return [t for t in wanted if t not in known]


def report_unknown_topics(config: Dict, pkm_dir: Optional[str] = None) -> None:
    """Warns once at agent load time about subscriptions missing from TAGS.md.
    Never raises: a missing or unreadable vault shouldn't stop agents loading."""
    agent_id = config.get("id")
    try:
        unknown = unknown_topics(config, pkm_dir)
    except Exception as e:
        print(f"Warning: could not check memory_topics for {agent_id}: {e}")
        return
    if unknown:
        print(f"Warning: agent '{agent_id}' subscribes to memory topics not in "
              f"TAGS.md: {unknown}. They are ignored until added there.")


def render_scope(scope: store.Scope) -> str:
    return store.load(scope).rendered().strip()


def render_profile(pkm_dir: Optional[str] = None) -> str:
    return render_scope(store.profile_scope(pkm_dir))


def render_topics(topics: List[str], pkm_dir: Optional[str] = None) -> str:
    blocks = []
    for tag in topics:
        body = render_scope(store.topic_scope(tag, pkm_dir))
        if body:
            blocks.append(f"### {tag}\n{body}")
    return "\n\n".join(blocks)


def render_private(agent_id: str, tag: str, pkm_dir: Optional[str] = None) -> str:
    return render_scope(store.private_scope(agent_id, tag, pkm_dir))


def render_memory(agent_id: str, pkm_dir: Optional[str] = None) -> str:
    return render_private(agent_id, PRIVATE, pkm_dir)


def render_feedback(agent_id: str, pkm_dir: Optional[str] = None) -> str:
    return render_private(agent_id, FEEDBACK, pkm_dir)
