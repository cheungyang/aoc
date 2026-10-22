"""Scheduler prompts: what a schedule adds to its run's prompt."""
from dataclasses import dataclass

from core.prompts.base import Prompt


@dataclass
class FindingsPrompt(Prompt):
    """A scan precondition's findings, appended to its scheduled run's prompt
    so the run starts from what the check already read (Findings.as_block in
    core/scheduler/spec.py). `issues` lists what the scan couldn't read."""

    template = "scheduler/findings"
    source: str
    body: str
    issues: str = ""
