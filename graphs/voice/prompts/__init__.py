"""The voice graph's prompts: each a `core.prompts.Prompt` whose template is
the `.md` file beside it, so the graph's prompts stay with the graph."""
from .handed_over_prompt import HandedOverPrompt
from .light_call_prompt import LightCallPrompt

__all__ = ["HandedOverPrompt", "LightCallPrompt"]
