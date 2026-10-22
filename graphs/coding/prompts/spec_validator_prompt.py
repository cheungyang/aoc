"""The Goldfish 0 spec validator's prompt (spec_validator.md), for tools/spec_validator.py."""
from dataclasses import dataclass
from pathlib import Path

from core.prompts import Prompt


@dataclass
class SpecValidatorPrompt(Prompt):
    """Is a spec self-contained enough to be run with no one to ask?
    Not used by any node: the tool runs it before a task is queued."""

    templates_dir = Path(__file__).resolve().parent
    template = "spec_validator"

    spec_text: str
