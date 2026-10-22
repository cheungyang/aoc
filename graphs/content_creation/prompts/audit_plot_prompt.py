"""The brand QC audit prompt (audit_plot.md), for nodes/ideation/audit_plot.py."""
from dataclasses import dataclass
from pathlib import Path

from core.prompts import Prompt


@dataclass
class AuditPlotPrompt(Prompt):
    """A drafted Video Plot, audited against the QC playbook before HITL Gate 1."""

    templates_dir = Path(__file__).resolve().parent
    template = "audit_plot"

    topic: str
    image_path: str
    video_plot_path: str
    project_path: str
    output_path: str
    qc_playbook_content: str = ""
    plot_content: str = ""
