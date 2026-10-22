"""The content creation graph's prompts, one module per graph-worker task.

Each is a `core.prompts.Prompt` whose template is the `.md` file beside it, so
the graph's prompts stay with the graph. Each asks for a strict <payload> XML
reply that its node parses; the single-brace fields in a template's schema are
text for the model to fill, not placeholders.
"""
from .draft_plot_prompt import DraftPlotPrompt
from .audit_plot_prompt import AuditPlotPrompt
from .draft_copy_prompt import DraftCopyPrompt

__all__ = [
    "DraftPlotPrompt",
    "AuditPlotPrompt",
    "DraftCopyPrompt",
]
