"""What an agent.json may contain, checked when the agent is loaded.

An agent whose config fails these checks is not loaded -- the treatment a
malformed JSON file already gets -- and every reason is printed. Failing at
load time puts the error next to the mistake. Without it, a mistyped skill id,
thinking level or voice would be silently ignored, and the agent would simply
be a little worse at its job with nothing to say why.

Checked here: the set of top-level keys, `skills`, `thinking` and `voice`.
Schedules have their own validator (core.scheduler.registry), which rejects
entries one at a time; tool grants are resolved by ToolsLoader.

The shapes:

    "skills": {                                   # required form; a list is rejected
        "coach_home_workout": {"preload": "full"},  # whole SKILL.md in the prompt
        "research": {"preload": "core"},            # only its `## Core` section
        "weekly_review": {}                         # on demand, via load_skill
    },
    "thinking": {"text": "high"},                 # per-surface overrides, rarely needed
    "voice": {"name": "en-US-Chirp3-HD-Zephyr", "rate": 1.1}
"""
import os
import re
from typing import Any, Dict, Iterable, List, Set

from core.util.models import THINKING_BY_SURFACE, THINKING_LEVELS

# Every top-level key an agent.json may use. A key outside this set is almost
# always a typo or a stale field, and both are cheaper to catch here than to
# discover as a setting that silently does nothing.
KNOWN_KEYS = frozenset({
    "id",
    "agent_id",
    "name",
    "emoji",
    "description",
    "model",
    "provider",
    "graph",
    "stateless",
    "channels",
    "channel_hosts",
    "concierge_prefixes",
    "discord_token_key",
    "memory_topics",
    "tools",
    "skills",
    "schedules",
    "thinking",
    "voice",
    "voice_config",
})

PRELOAD_MODES = ("full", "core")
SKILL_KEYS = frozenset({"preload"})
THINKING_SURFACES = tuple(THINKING_BY_SURFACE)
VOICE_KEYS = frozenset({"name", "rate"})

# The voice name's shape. Whether the voice exists is checked against
# core/voice/settings.py by the config tests rather than here, because
# importing the voice package pulls in the whole audio stack.
VOICE_NAME = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-Chirp3-HD-[A-Za-z]+$")
MIN_SPEAKING_RATE = 0.25
MAX_SPEAKING_RATE = 2.0

SKILLS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "skills"))


def known_skill_ids(skills_dir: str = SKILLS_DIR) -> Set[str]:
    """Ids of the skills that exist: directories under `skills_dir` with a skill.json."""
    if not os.path.isdir(skills_dir):
        return set()
    return {
        name for name in os.listdir(skills_dir)
        if os.path.isfile(os.path.join(skills_dir, name, "skill.json"))
    }


def _validate_skills(skills: Any, known_skills: Set[str]) -> List[str]:
    if not isinstance(skills, dict):
        return [
            "'skills' must be an object mapping skill id to its settings, e.g. "
            '{"research": {"preload": "core"}, "weekly_review": {}}'
        ]
    errors = []
    for skill_id, settings in skills.items():
        if skill_id not in known_skills:
            errors.append(f"skills: unknown skill {skill_id!r}")
        if not isinstance(settings, dict):
            errors.append(f"skills.{skill_id}: settings must be an object ({{}} for defaults)")
            continue
        unknown = sorted(set(settings) - SKILL_KEYS)
        if unknown:
            errors.append(f"skills.{skill_id}: unknown key(s) {', '.join(unknown)}")
        if "preload" in settings and settings["preload"] not in PRELOAD_MODES:
            errors.append(
                f"skills.{skill_id}.preload must be one of {', '.join(PRELOAD_MODES)}, "
                f"got {settings['preload']!r}"
            )
    return errors


def _validate_thinking(thinking: Any) -> List[str]:
    if not isinstance(thinking, dict):
        return ["'thinking' must be an object mapping a surface to a level, e.g. {\"text\": \"high\"}"]
    errors = []
    for surface, level in thinking.items():
        if surface not in THINKING_SURFACES:
            errors.append(f"thinking: unknown surface {surface!r} (allowed: {', '.join(THINKING_SURFACES)})")
        if level not in THINKING_LEVELS:
            errors.append(f"thinking.{surface}: unknown level {level!r} (allowed: {', '.join(THINKING_LEVELS)})")
    return errors


def _validate_voice(voice: Any) -> List[str]:
    if not isinstance(voice, dict):
        return ["'voice' must be an object, e.g. {\"name\": \"en-US-Chirp3-HD-Zephyr\"}"]
    errors = []
    unknown = sorted(set(voice) - VOICE_KEYS)
    if unknown:
        errors.append(f"voice: unknown key(s) {', '.join(unknown)}")
    name = voice.get("name")
    if not isinstance(name, str) or not VOICE_NAME.match(name):
        errors.append(f"voice.name must be a Chirp 3 HD voice like 'en-US-Chirp3-HD-Zephyr', got {name!r}")
    if "rate" in voice:
        rate = voice["rate"]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not (
            MIN_SPEAKING_RATE <= rate <= MAX_SPEAKING_RATE
        ):
            errors.append(f"voice.rate must be a number from {MIN_SPEAKING_RATE} to {MAX_SPEAKING_RATE}, got {rate!r}")
    return errors


def validate_agent_config(config: Any, known_skills: Iterable[str]) -> List[str]:
    """Every reason `config` can't be loaded, or an empty list."""
    if not isinstance(config, dict):
        return ["agent.json must be a JSON object"]
    errors = []
    unknown = sorted(set(config) - KNOWN_KEYS)
    if unknown:
        errors.append(f"unknown key(s): {', '.join(unknown)}")
    if "skills" in config:
        errors.extend(_validate_skills(config["skills"], set(known_skills)))
    if "thinking" in config:
        errors.extend(_validate_thinking(config["thinking"]))
    if "voice" in config:
        errors.extend(_validate_voice(config["voice"]))
    return errors
