"""Each agent's prompt size, snapshotted once a day: the prompt ledger.

One prompt_ledger row per agent and day (core/storage/schemas/learning.sql),
measured as the system prompt carries the text (core/prompts/system.py):

    persona_chars   AGENTS.md, INSTRUCTIONS.md, IDENTITY.md and SOUL.md, each
                    without its title line, as read_prompt_file reads it
    preload_chars   its preloaded skills: a skill's body, or its Core section
    user_chars      its USER.md, in the vault
    shared_chars    the Profile and its subscribed topics, as rendered
                    (none for a stateless agent, which doesn't get them)
    memory_chars    its MEMORY, as rendered
    feedback_chars  its FEEDBACK, as rendered
    instructions    instruction-like lines in persona and preloads (instruction_count)

Characters, not tokens (about 4 to a token). The system templates every agent
shares aren't counted: no agent's change moves them, and their size would hide
each agent's own. report.ledger_text shows the change over a month, and flags
growth past a tenth and any sudden drop of a quarter -- a rewrite that lost
text (README: the prompt ledger).
"""
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from core.config.settings import Config
from core.learning.versions import REPO, agent_config
from core.storage.learning import LearningStore
from core.util.local_time import get_local_timezone

PERSONA_FILES = ("AGENTS.md", "INSTRUCTIONS.md", "IDENTITY.md", "SOUL.md")
SIZES = ("persona_chars", "preload_chars", "user_chars", "shared_chars", "memory_chars", "feedback_chars")
# instruction_count: a list item, or a line that tells the model what it must or must not do.
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_IMPERATIVE = re.compile(r"\b(?:must|never|always|do not|don't|avoid)\b", re.IGNORECASE)


def instruction_count(text: str) -> int:
    """Instruction-like lines: list items, and other lines saying must, never, always, do not
    or avoid. A rough count, for its trend: past a few hundred, models follow fewer of them."""
    return sum(1 for line in (text or "").splitlines() if _LIST_ITEM.match(line) or _IMPERATIVE.search(line))


def today(now: Optional[float] = None) -> str:
    """The date on your wall clock, YYYY-MM-DD."""
    moment = datetime.now(get_local_timezone()) if now is None else datetime.fromtimestamp(now, get_local_timezone())
    return moment.strftime("%Y-%m-%d")


def _preloads(config: Dict[str, Any], repo: str) -> List[str]:
    """The text each preloaded skill puts in the prompt (SkillsLoader.preloaded_text)."""
    from core.loaders.skills_loader import core_section, skill_body

    out = []
    skills = config.get("skills") or {}
    for skill_id, settings in sorted(skills.items() if isinstance(skills, dict) else ()):
        mode = settings.get("preload") if isinstance(settings, dict) else None
        if not mode:
            continue
        try:
            with open(os.path.join(repo, "skills", skill_id, "SKILL.md"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        out.append((skill_body(text) if mode == "full" else core_section(text)) or "")
    return out


class LedgerError(Exception):
    """Some agents couldn't be measured. The others were recorded."""


def measure(agent_id: str, repo: str = REPO, pkm_dir: Optional[str] = None) -> Dict[str, Any]:
    """One agent's ledger row (module docstring), without the day. Raises when the vault
    can't be read: a row missing a part would read as a sudden drop."""
    from core.knowledge.memory import inject
    from core.prompts.system import read_prompt_file

    config = agent_config(agent_id, repo)
    agent_dir = os.path.join(repo, "agents", agent_id)
    persona = [read_prompt_file(os.path.join(agent_dir, name)) for name in PERSONA_FILES]
    preloads = _preloads(config, repo)
    row: Dict[str, Any] = {"agent_id": agent_id, "persona_chars": sum(map(len, persona)),
                           "preload_chars": sum(map(len, preloads)),
                           "instructions": sum(instruction_count(text) for text in persona + preloads),
                           "user_chars": 0, "shared_chars": 0, "memory_chars": 0, "feedback_chars": 0}
    pkm = pkm_dir or Config().pkm_dir
    if pkm:
        row["user_chars"] = len(read_prompt_file(os.path.join(pkm, "agents", agent_id, "USER.md")))
        if not config.get("stateless"):
            topics = inject.memory_topics(config, pkm, agent_id)
            row["shared_chars"] = len(inject.render_profile(pkm)) + len(inject.render_topics(topics, pkm))
        row["memory_chars"] = len(inject.render_memory(agent_id, pkm))
        row["feedback_chars"] = len(inject.render_feedback(agent_id, pkm))
    return row


def snapshot(store: LearningStore, agents: Sequence[str], *, day: Optional[str] = None, repo: str = REPO,
             pkm_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Measures every agent that has a prompt and records the day's rows; returns them.
    An agent with no persona and no preload (a script runner) has no row. One that can't
    be measured has none that day either: the others are recorded, then LedgerError says why."""
    rows, failed = [], []
    for agent_id in agents:
        try:
            row = measure(agent_id, repo, pkm_dir)
        except Exception as e:
            failed.append(f"{agent_id}: {type(e).__name__}: {e}")
            continue
        if row["persona_chars"] or row["preload_chars"]:
            rows.append(row)
    store.record_ledger(day or today(), rows)
    if failed:
        raise LedgerError("; ".join(failed))
    return rows


def total(row: Dict[str, Any]) -> int:
    """All the characters a ledger row counts."""
    return sum(int(row.get(size) or 0) for size in SIZES)
