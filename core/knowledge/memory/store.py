"""Where Memory v2 lives and how its size is enforced.

Everything is data under `pkm/`:

    wiki/memory/TAGS.md              the closed tag list
    wiki/memory/PROFILE.md           shared, every non-stateless agent
    wiki/memory/topics/<tag>.md      shared, agents subscribed to <tag>
    wiki/memory/state.json           counters and dream health (code only)
    wiki/memory/archive/<tag>.md     evicted / retired / expired shared entries
    wiki/memory/archive/PROFILE.md
    agents/<id>/MEMORY.md            private craft
    agents/<id>/FEEDBACK.md          private corrections
    agents/<id>/archive/MEMORY.md    and archive/FEEDBACK.md

A write that would push a file over budget never lands: the lowest-scoring
entries are moved to the archive until it fits.
"""
import contextlib
import datetime
import os
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from core.knowledge.memory.entries import (
    FEEDBACK, PRIVATE, PROFILE, Entry, MemoryFile, TagsError, format_file, parse_file, parse_tags,
)
from core.util.config import Config

BUDGET_PROFILE = 2000
BUDGET_TOPIC = 1000
BUDGET_PRIVATE = 2000
STALE_DAYS = 90
FEEDBACK_WEIGHT = 3


def pkm_root(pkm_dir: Optional[str] = None) -> str:
    return pkm_dir or Config().pkm_dir


def memory_dir(pkm_dir: Optional[str] = None) -> str:
    return os.path.join(pkm_root(pkm_dir), "wiki", "memory")


def tags_path(pkm_dir: Optional[str] = None) -> str:
    return os.path.join(memory_dir(pkm_dir), "TAGS.md")


def state_path(pkm_dir: Optional[str] = None) -> str:
    return os.path.join(memory_dir(pkm_dir), "state.json")


def agent_dir(agent_id: str, pkm_dir: Optional[str] = None) -> str:
    return os.path.join(pkm_root(pkm_dir), "agents", agent_id)


@dataclass(frozen=True)
class Scope:
    """One memory file: where it is, where its evictions go, and its budget."""
    tag: str
    path: str
    archive: str
    budget: int
    title: str
    agent_id: Optional[str] = None

    @property
    def shared(self) -> bool:
        return self.agent_id is None


def profile_scope(pkm_dir: Optional[str] = None) -> Scope:
    base = memory_dir(pkm_dir)
    return Scope(PROFILE, os.path.join(base, "PROFILE.md"),
                 os.path.join(base, "archive", "PROFILE.md"), BUDGET_PROFILE, "Profile")


def topic_scope(tag: str, pkm_dir: Optional[str] = None) -> Scope:
    base = memory_dir(pkm_dir)
    return Scope(tag, os.path.join(base, "topics", f"{tag}.md"),
                 os.path.join(base, "archive", f"{tag}.md"), BUDGET_TOPIC, f"Topic: {tag}")


def private_scope(agent_id: str, tag: str, pkm_dir: Optional[str] = None) -> Scope:
    name = "FEEDBACK.md" if tag == FEEDBACK else "MEMORY.md"
    base = agent_dir(agent_id, pkm_dir)
    title = "Feedback" if tag == FEEDBACK else "Memory"
    return Scope(tag, os.path.join(base, name), os.path.join(base, "archive", name),
                 BUDGET_PRIVATE, title, agent_id)


def scope_for(tag: str, agent_id: str, pkm_dir: Optional[str] = None) -> Scope:
    if tag == PROFILE:
        return profile_scope(pkm_dir)
    if tag in (PRIVATE, FEEDBACK):
        return private_scope(agent_id, tag, pkm_dir)
    return topic_scope(tag, pkm_dir)


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def write_atomic(path: str, text: str) -> None:
    """Replaces `path` in one step, so a crash never leaves a half-written file."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".memory-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def load(scope: Scope) -> MemoryFile:
    return parse_file(read_text(scope.path))


def save(scope: Scope, memory: MemoryFile) -> None:
    write_atomic(scope.path, format_file(scope.title, memory))


def load_tags(pkm_dir: Optional[str] = None) -> List[Tuple[str, str]]:
    """The closed tag list. A missing file means no topics yet; a malformed one raises."""
    path = tags_path(pkm_dir)
    if not os.path.isfile(path):
        return []
    return parse_tags(read_text(path))


def tag_names(pkm_dir: Optional[str] = None) -> List[str]:
    try:
        return [name for name, _ in load_tags(pkm_dir)]
    except TagsError as e:
        print(f"Warning: {e}")
        return []


def recency_weight(days: int) -> float:
    return 1.0 / (1.0 + max(days, 0) / 30.0)


def score(entry: Entry, today: datetime.date) -> float:
    weight = FEEDBACK_WEIGHT if entry.tag == FEEDBACK else 1
    return entry.count * recency_weight(entry.days_unseen(today)) * weight


def enforce(memory: MemoryFile, budget: int, today: datetime.date) -> Tuple[MemoryFile, List[Entry], List[Entry]]:
    """Drops expired entries, then evicts the lowest scores until the file fits.

    Returns (kept, evicted, expired). Unparsed lines are never evicted: they
    are the user's hand edits and are reported instead.
    """
    expired = [e for e in memory.entries if e.expired(today)]
    kept = [e for e in memory.entries if not e.expired(today)]
    evicted: List[Entry] = []
    result = MemoryFile(entries=kept, unparsed=list(memory.unparsed))
    while result.entries and result.size() > budget:
        weakest = min(result.entries, key=lambda e: (score(e, today), e.seen, e.first))
        result.entries.remove(weakest)
        evicted.append(weakest)
    return result, evicted, expired


def archive(scope: Scope, entries: List[Entry], reason: str, today: datetime.date) -> None:
    """Appends entries, unchanged, under a dated heading. Restoring is moving a line back."""
    if not entries:
        return
    existing = read_text(scope.archive)
    if not existing:
        existing = f"# Archive: {scope.title}\n"
    block = [f"\n## {today.isoformat()} · {reason}"] + [e.format() for e in entries]
    write_atomic(scope.archive, existing.rstrip("\n") + "\n" + "\n".join(block) + "\n")


def commit(scope: Scope, memory: MemoryFile, today: datetime.date,
           archived: Optional[Dict[str, List[Entry]]] = None) -> Dict[str, int]:
    """Enforces the budget, archives what left, writes the file. Returns counts by reason."""
    kept, evicted, expired = enforce(memory, scope.budget, today)
    reasons = dict(archived or {})
    reasons.setdefault("budget", []).extend(evicted)
    reasons.setdefault("expired", []).extend(expired)
    for reason, entries in reasons.items():
        archive(scope, entries, reason, today)
    save(scope, kept)
    return {reason: len(entries) for reason, entries in reasons.items() if entries}
