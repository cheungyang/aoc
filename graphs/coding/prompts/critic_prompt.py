"""The Goldfish 2 critic's prompt (critic.md), for nodes/audit.py."""
from dataclasses import dataclass
from pathlib import Path

from core.prompts import Prompt


@dataclass
class CriticPrompt(Prompt):
    """The branch diff, audited against the spec for anti-patterns."""

    templates_dir = Path(__file__).resolve().parent
    template = "critic"

    spec_text: str
    git_diff_text: str
