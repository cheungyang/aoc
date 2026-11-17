"""open_patterns: Kaizen's weekly review runs only when a recurring pattern has no proposal yet."""
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report

# Patterns named in the findings, worst first, and example cards for each.
SHOWN = 6
EXAMPLES = 2


class OpenPatterns(Scan):
    """A recurring friction pattern has no checked proposal this week.

    Reads the last 14 days' friction cards and this week's proposals (memory.db),
    with `agents.agent_learner.lib.views`. Skips when the learning loop is off, when
    every recurring pattern already has a checked proposal, or when this week's
    patterns-per-week limit is reached.

    Findings: those patterns, worst first, each with its counts and example card
    ids, so the review starts without another read.
    """
    type_name = "open_patterns"

    def scan(self, ctx: ScheduleContext, report: Report) -> WorkDecision:
        from core.config.settings import Config
        from core.learning.proposals import PATTERNS_PER_WEEK
        from core.learning.report import evidence
        from core.storage.learning import LearningStore
        from agents.agent_learner.lib.views import open_patterns, open_proposals, pattern_key

        if not Config().learning_observe:
            return WorkDecision(False, "LEARNING_OBSERVE is off")
        store = LearningStore()
        if not store.exists():
            return WorkDecision(False, "no memory.db yet")
        waiting = open_proposals(store, ctx.now)
        if len(waiting) >= PATTERNS_PER_WEEK:
            return WorkDecision(False, f"{len(waiting)} patterns already have a checked proposal this week")
        todo = open_patterns(store, ctx.now)
        if not todo:
            return WorkDecision(False, "no recurring pattern without a checked proposal")
        lines = [f"Recurring patterns without a proposal, worst first (room for {PATTERNS_PER_WEEK - len(waiting)} "
                 f"this week):"]
        for p in todo[:SHOWN]:
            newest = sorted(p.events, key=lambda e: e["at"], reverse=True)[:EXAMPLES]
            examples = "; ".join(f"#{e['id']} {evidence(e, 100)}" for e in newest)
            lines.append(f"- {pattern_key(p.agent_id, p.detector)}: {p.count} cards in {p.episodes} sessions, "
                         f"severity {p.weight}. {examples}")
        if len(todo) > SHOWN:
            lines.append(f"... and {len(todo) - SHOWN} more (friction_query view=patterns)")
        return WorkDecision(True, f"{len(todo)} recurring pattern(s) without a proposal", detail="\n".join(lines))
