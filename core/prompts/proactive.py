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


@dataclass
class DeliveryPrompt(Prompt):
    """A proactive turn's prompt: what a teammate learned, and what the recipient could offer.

    core/proactive/delivery.py renders the parts from one due delivery, its
    route and its signal. Nothing in it names where the fact came from: the
    recipient speaks in its own voice and never mentions another agent. Empty
    parts are left out. A reply of <silent/> posts nothing (delivery.is_silent).
    """

    template = "proactive/delivery"

    now: str
    gist: str
    evidence: str
    details: str   # the signal's payload, as JSON
    until: str
    window: str
    offer: str
    note: str      # the Switchboard's one sentence on what the recipient could do with it
