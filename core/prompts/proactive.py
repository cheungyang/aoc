"""The proactive team's prompts (core/proactive/README.md)."""
from dataclasses import dataclass

from core.prompts.base import Prompt


@dataclass
class SwitchboardPrompt(Prompt):
    """What the Switchboard is asked after a conversation or a scheduled run.

    core/proactive/switchboard.py renders the parts: the teammates' peer
    cards, the tag list, the Board's live signals, and the exchange, one block
    per ledger turn. The reply is a <switchboard_response> that code checks
    field by field (switchboard.parse_reply, then the Governor).
    """

    template = "proactive/switchboard"

    now: str
    teammates: str
    tags: str
    board: str
    exchange: str
