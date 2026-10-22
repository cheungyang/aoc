"""The plot-drafting prompt (draft_plot.md), for nodes/ideation/draft_plot.py."""
from dataclasses import dataclass
from pathlib import Path

from core.prompts import Prompt


@dataclass
class DraftPlotPrompt(Prompt):
    """The structured Video Plot for a topic, bound to its image and audio."""

    templates_dir = Path(__file__).resolve().parent
    template = "draft_plot"

    topic: str
    style_str: str
    image_path: str
    audio_path: str
    project_path: str
    output_path: str
    video_plot_path: str
    video_plot_json_path: str
    project_guidelines: str = ""
    char_guidelines: str = ""
    feedback: str = ""        # the brand QC audit's, to fix
    human_feedback: str = ""  # the human's revision notes: highest priority
