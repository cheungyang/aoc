"""Which prompt a model call ran on: a version per agent, kept on every token row.

A version is two short hashes, "<static>.<memory>":

    static   the repo files the agent's prompt is built from: its AGENTS.md,
             INSTRUCTIONS.md, IDENTITY.md, SOUL.md and agent.json, the system
             and voice templates, and the SKILL.md of every skill it lists
    memory   the vault files the prompt carries: its USER.md, MEMORY.md and
             FEEDBACK.md, the shared Profile and its subscribed topics

So a change to a skill shows as a new static half, and a night's dream as a new
memory half. Each new version is recorded once (prompt_versions), and every
static file's text is kept by its hash (prompt_files), so any version can be
shown or compared later. Vault files' text isn't kept: the vault's git keeps it.

`current()` runs on every model call, so it's cheap: an agent's files are
checked at most every TTL_S seconds, by size and mtime, and only a changed
file is read and hashed again. It never raises; on any error the version is
None and the token row keeps a NULL.
"""
import glob
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.config.settings import Config

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PERSONA_FILES = ("AGENTS.md", "INSTRUCTIONS.md", "IDENTITY.md", "SOUL.md", "agent.json")
TEMPLATE_GLOBS = ("core/prompts/templates/system/*.md", "core/prompts/templates/voice/*.md")
MEMORY_FILES = ("USER.md", "MEMORY.md", "FEEDBACK.md")
TTL_S = 30.0
HASH_CHARS = 8


@dataclass
class _Known:
    """What was last seen for one agent."""
    checked: float                                   # time.monotonic() of the last check
    fingerprint: Tuple                               # (path, size, mtime_ns) of every file
    version: str
    hashes: Dict[str, str] = field(default_factory=dict)  # file key -> sha256 of its text


_KNOWN: Dict[str, _Known] = {}
_LOCK = threading.Lock()


def _observing() -> bool:
    from core.learning.events import observing  # here, so importing this module loads no store

    return observing()


def agent_config(agent_id: str, repo: str) -> dict:
    try:
        with open(os.path.join(repo, "agents", agent_id, "agent.json"), encoding="utf-8") as f:
            config = json.load(f)
        return config if isinstance(config, dict) else {}
    except (OSError, ValueError):
        return {}


def static_paths(agent_id: str, repo: str = REPO) -> List[str]:
    """The repo files the agent's prompt is built from (module docstring), that exist."""
    paths = [os.path.join(repo, "agents", agent_id, name) for name in PERSONA_FILES]
    for pattern in TEMPLATE_GLOBS:
        paths += sorted(glob.glob(os.path.join(repo, pattern)))
    skills = agent_config(agent_id, repo).get("skills") or {}
    if isinstance(skills, dict):
        paths += [os.path.join(repo, "skills", skill, "SKILL.md") for skill in sorted(skills)]
    return [p for p in paths if os.path.isfile(p)]


def memory_paths(agent_id: str, repo: str = REPO, pkm_dir: Optional[str] = None) -> List[str]:
    """The vault files the agent's prompt carries (module docstring), that exist."""
    pkm = pkm_dir or Config().pkm_dir
    if not pkm:
        return []
    paths = [os.path.join(pkm, "agents", agent_id, name) for name in MEMORY_FILES]
    paths.append(os.path.join(pkm, "wiki", "memory", "PROFILE.md"))
    topics = agent_config(agent_id, repo).get("memory_topics") or []
    if isinstance(topics, list):
        paths += [os.path.join(pkm, "wiki", "memory", "topics", f"{topic}.md") for topic in sorted(map(str, topics))]
    return [p for p in paths if os.path.isfile(p)]


def _fingerprint(paths: List[str]) -> Tuple:
    out = []
    for path in paths:
        try:
            stat = os.stat(path)
        except OSError:
            continue
        out.append((path, stat.st_size, stat.st_mtime_ns))
    return tuple(out)


def _sha(path: str) -> Tuple[str, str]:
    """The file's text and its sha256."""
    with open(path, "rb") as f:
        data = f.read()
    return data.decode("utf-8", errors="replace"), hashlib.sha256(data).hexdigest()


def _key(path: str, repo: str, pkm: str) -> str:
    """How a file is named in a version's record: repo-relative, or pkm/... for the vault."""
    path = os.path.abspath(path)
    if pkm and path.startswith(os.path.abspath(pkm) + os.sep):
        return "pkm/" + os.path.relpath(path, pkm)
    return os.path.relpath(path, repo)


def _half(hashes: Dict[str, str], keys: List[str]) -> str:
    joined = "\n".join(f"{key}:{hashes[key]}" for key in sorted(keys))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:HASH_CHARS]


def compute(agent_id: str, repo: str = REPO, pkm_dir: Optional[str] = None,
            known: Optional[_Known] = None) -> Tuple[str, Dict[str, str], Dict[str, Tuple[str, str]], int, int, Tuple]:
    """(version, {key: sha}, {sha: (key, text)} of the static files read, static bytes, memory bytes, fingerprint).

    A file whose size and mtime match `known`'s fingerprint isn't read again.
    """
    pkm = pkm_dir or Config().pkm_dir or ""
    static, memory = static_paths(agent_id, repo), memory_paths(agent_id, repo, pkm or None)
    fingerprint = _fingerprint(static + memory)
    stats = {path: (size, mtime) for path, size, mtime in fingerprint}
    seen = set(known.fingerprint) if known is not None else set()
    hashes: Dict[str, str] = {}
    texts: Dict[str, Tuple[str, str]] = {}
    sizes = {"static": 0, "memory": 0}
    keys: Dict[str, List[str]] = {"static": [], "memory": []}
    for group, paths in (("static", static), ("memory", memory)):
        for path in paths:
            if path not in stats:  # gone since it was listed
                continue
            key = _key(path, repo, pkm)
            keys[group].append(key)
            sizes[group] += stats[path][0]
            if known is not None and (path, *stats[path]) in seen and key in known.hashes:
                hashes[key] = known.hashes[key]
                continue
            text, sha = _sha(path)
            hashes[key] = sha
            if group == "static":
                texts[sha] = (key, text)
    version = f"{_half(hashes, keys['static'])}.{_half(hashes, keys['memory'])}"
    return version, hashes, texts, sizes["static"], sizes["memory"], fingerprint


def current(agent_id: str, repo: str = REPO, pkm_dir: Optional[str] = None) -> Optional[str]:
    """The agent's prompt version now (module docstring); None if it can't be told, or
    with LEARNING_OBSERVE off, so no token row names a version that wasn't recorded. Never raises."""
    if not agent_id or not _observing():
        return None
    try:
        now = time.monotonic()
        with _LOCK:
            known = _KNOWN.get(agent_id)
        if known is not None and now - known.checked < TTL_S:
            return known.version
        version, hashes, texts, static_chars, memory_chars, fingerprint = compute(agent_id, repo, pkm_dir, known)
        with _LOCK:
            _KNOWN[agent_id] = _Known(now, fingerprint, version, hashes)
        if known is None or known.version != version:
            _note(agent_id, version, hashes, texts, static_chars, memory_chars)
        return version
    except Exception as e:
        print(f"[learning] no prompt version for {agent_id}: {type(e).__name__}: {e}")
        return None


def _note(agent_id: str, version: str, hashes: Dict[str, str], texts: Dict[str, Tuple[str, str]],
          static_chars: int, memory_chars: int) -> None:
    """Records a version seen for the first time, and its static files' texts, off the caller's thread."""
    from core.learning import events

    def write(store):
        store.keep_prompt_files(texts)
        store.note_prompt_version(agent_id, version, hashes, static_chars=static_chars, memory_chars=memory_chars)

    events.submit(write)


def forget() -> None:
    """Drops what was seen, for tests."""
    with _LOCK:
        _KNOWN.clear()
