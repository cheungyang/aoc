"""`!learn`: the self-learning loop's view (core/learning/README.md). Nothing here changes an agent.

    !learn                         the last 7 days: frictions by detector and agent, the
                                   worst 5, the last nightly scan, Phase 0's exit criteria
    !learn friction [agent]        the last 14 days' patterns (one agent's cards from one
                                   detector), recurring ones first, with example quotes;
                                   `?` for the cards no agent was found for
    !learn cards [n]               cards to label, taken in turn from each detector
    !learn label <id>… real|not    your verdict on cards: what tunes and mutes the detectors
    !learn ledger                  each agent's prompt size, its change over 30 days, alerts
    !learn proposals [id]          what Kaizen (agents/agent-learner/) handed in, and what
                                   the checks found; one in full with its id

Read from memory.db, with no model; core/learning/report.py writes every text.
The experiment commands (`exp`, `keep`, `train`...) come with Phase 2.
"""
import asyncio

from core.learning.report import learn_text
from core.runtime.commands.base import ChannelCommand, TypedContext


class LearnCommand(ChannelCommand):
    name = "learn"
    usage = "[friction [agent|?] | cards [n] | label <id>… real|not | ledger | proposals [id]]"
    summary = "The learning loop: the week's friction, cards to label, the prompt ledger, Kaizen's proposals."

    async def run(self, ctx: TypedContext, args: str) -> None:
        # memory.db is a file: read and written off the event loop.
        await ctx.reply(await asyncio.to_thread(learn_text, args))
