"""The copywriting prompt (draft_copy.md), for nodes/production/draft_copy.py."""
from dataclasses import dataclass
from pathlib import Path

from core.prompts import Prompt


@dataclass
class DraftCopyPrompt(Prompt):
    """The social copy for a topic: caption, vocabulary tips and hashtags."""

    templates_dir = Path(__file__).resolve().parent
    template = "draft_copy"

    topic: str
    project_path: str
    output_path: str
    copy_path: str
    copy_json_path: str
    instructions_text: str = ""
    human_feedback: str = ""
    is_revision: bool = False

    def values(self):
        # The human's notes reach the prompt only when the copy is being revised.
        return {**super().values(), "revision_feedback": self.human_feedback if self.is_revision else ""}
