"""projects_attention: goal-setter coaches only when an executing project needs it."""
import json
from datetime import datetime

from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report, load_rules

# Runners-up named in the findings, so the agent can say what comes next.
RUNNERS_UP = 2


class ProjectsAttention(Scan):
    """An executing project needs coaching.

    Ranks the executing projects with `agents.goal_setter.lib.attention` (never or
    long ago coached, stalled, no open tasks, overdue tasks, a lapsed commitment),
    weighted by goal-setter's RULES.md; projects coached within `rest_days` rest.
    Skips when none needs attention.

    Findings: the first projects of the ranking with their reasons, and the first
    one's project_context, so the run can start coaching without another read. The
    coach_project skill decides which project to coach.
    """
    type_name = "projects_attention"

    def scan(self, ctx: ScheduleContext, report: Report) -> WorkDecision:
        from core.util.local_time import get_local_timezone
        from agents.goal_setter.lib.attention import project_context, rank_projects
        from agents.goal_setter.lib.rules import RULES
        from agents.goal_setter.lib.snapshot import load_snapshot

        today = datetime.fromtimestamp(ctx.now, get_local_timezone()).date()
        rules = load_rules(ctx.agent_id, RULES, report)
        snapshot = load_snapshot(report)
        ranked, resting = rank_projects(snapshot, today, rules, report)
        if not ranked:
            rested = f" ({len(resting)} coached in the last {rules['rest_days']} days)" if resting else ""
            return WorkDecision(False, f"no executing project needs coaching{rested}")

        top = ranked[0]
        reasons = "; ".join(top.reasons)
        lines = [f"Ranked first: {top.project['name']} ({reasons})."]
        if len(ranked) > 1:
            lines.append("Next in line:")
            lines += [f"- {a.project['name']} ({'; '.join(a.reasons)})" for a in ranked[1:1 + RUNNERS_UP]]
        # Its own report: the ranking above already reported what couldn't be read of this project.
        context = project_context(top.project["id"], today, snapshot, Report())
        lines.append(f"Its project_context: {json.dumps(context, ensure_ascii=False)}")
        return WorkDecision(True, f"{top.project['name']}: {reasons}", detail="\n".join(lines))
