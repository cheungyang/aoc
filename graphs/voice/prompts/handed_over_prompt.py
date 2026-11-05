"""The hand-over's result (handed_over.md), for graph.py's `handover` tool."""
from dataclasses import dataclass
from pathlib import Path

from core.prompts import Prompt


@dataclass
class HandedOverPrompt(Prompt):
    """What the agent's model reads as the `handover` call's result: the user
    heard the light first call's line, so it isn't said again."""

    templates_dir = Path(__file__).resolve().parent
    template = "handed_over"
