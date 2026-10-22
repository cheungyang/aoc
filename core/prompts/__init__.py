"""Prompt templates: stored as files beside this module, filled in by name.

A template is `core/prompts/<name>.md`. Its placeholders are `{{name}}`, so
single braces, JSON and dollar signs in a prompt are plain text:

    render_prompt("speech_writer", name="Daisy", channel="day-planning", ...)

Rendering fails loudly on a placeholder that has no value, so a template and
its caller can't drift apart silently. Values are inserted as they are and
never scanned for placeholders themselves.
"""
import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def load_prompt(template: str, /) -> str:
    """The raw text of `template`. Read on each call, so an edit takes effect without a restart."""
    return (PROMPTS_DIR / f"{template}.md").read_text(encoding="utf-8")


def placeholders(template: str, /) -> set:
    """The placeholder names `template` uses."""
    return {match.group(1) for match in _PLACEHOLDER.finditer(load_prompt(template))}


def render_prompt(template: str, /, **values) -> str:
    """`template` with every `{{placeholder}}` filled from `values`. Positional-only,
    so a placeholder may be called anything, `template` included."""
    text = load_prompt(template)
    missing = sorted({match.group(1) for match in _PLACEHOLDER.finditer(text)} - set(values))
    if missing:
        raise KeyError(f"prompt '{template}' needs a value for: {', '.join(missing)}")
    return _PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), text)
