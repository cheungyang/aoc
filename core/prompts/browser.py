"""Browser prompts: what is added to the browser agent's own system prompt.

tools/browser.py drives a browser-use Agent, which has a system prompt of its
own; these rules are appended to it (`extend_system_message`).
"""
from core.prompts.base import Prompt


class BrowserHandoffPrompt(Prompt):
    """The agent works in a window the user is watching, and hands back control
    instead of guessing or committing."""

    template = "browser/handoff"
