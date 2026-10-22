"""The Goldfish 1 coder's prompt (coder.md), for nodes/implement.py."""
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.prompts import Prompt


@dataclass
class CoderPrompt(Prompt):
    """The coder's task, with a strict boundary and the targeted retry delta:
    each retry block is there only when it has something to say."""

    templates_dir = Path(__file__).resolve().parent
    template = "coder"

    workspace_path: str
    task_id: str
    spec_path: str
    allowed_files: Optional[List[str]]
    acceptance_criteria: str
    verification_command: str
    spec_content: Optional[str] = None
    test_stderr: Optional[str] = None
    critic_feedback: Optional[str] = None
    human_feedback: Optional[str] = None
    # Where and what kind only: the findings never carry the matched words,
    # and the worker is told not to repeat them, because its summary becomes
    # the public PR body.
    privacy_findings: Optional[str] = None

    def values(self):
        # An empty list is "no restriction", not "no files".
        files = "\n".join(f"- {f}" for f in (self.allowed_files or [])) or "- (All files within workspace)"
        return {**super().values(), "allowed_files": files}
