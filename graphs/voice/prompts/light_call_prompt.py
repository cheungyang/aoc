"""The light call's rules (light_call.md), for graph.py."""
from dataclasses import dataclass
from pathlib import Path

from core.prompts import Prompt


@dataclass
class LightCallPrompt(Prompt):
    """A voice turn's light first call's own rules, on that call only, after the agent's prompt."""

    templates_dir = Path(__file__).resolve().parent
    template = "light_call"
