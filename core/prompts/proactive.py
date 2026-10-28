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


@dataclass
class TeamFindingsPrompt(Prompt):
    """What the team learned for a scheduled run: its agent's live `next` routes.

    core/proactive/delivery.py (SignalsForMe) renders one item per route, and the
    schedule runner puts the block after the run's own prompt. As in a proactive
    turn's prompt, nothing names where a fact came from: the agent uses what fits
    this run, in its own voice, and leaves out the rest.
    """

    template = "proactive/team_findings"

    items: str     # one per route: its gist, until or window, details, offer and note


@dataclass
class FollowUpPrompt(Prompt):
    """A follow-up's check: the agent's own open loop, due now.

    core/proactive/follow_ups.py renders it from one claimed row: the goal,
    what would end it early, its age and its checks, what the agent last told
    the user, and what to check now. A scan check's findings follow it, in
    their own block. A reply of <silent/> posts nothing (delivery.is_silent),
    and a check whose goal is met closes the follow-up itself. The last check
    before `until` ends never stays silent: it ends with one short question,
    whether to keep watching and until when, and the user answers in the
    conversation, where the agent can extend it.
    """

    template = "proactive/follow_up"

    id: int
    now: str
    goal: str
    close_when: str
    opened: str        # the day it was opened
    until: str
    checks: str        # how many checks since, in words
    last_report: str   # what the last find posted, clipped; "" if none yet
    reported: str      # when that was
    check: str         # "Check now: ..." for a model check; a scan check's findings follow
    last_check: bool = False  # no other check would come before `until` ends: offer to keep watching
