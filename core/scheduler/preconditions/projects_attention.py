"""projects_attention: goal-setter coaches only when an executing project needs it."""
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision


class ProjectsAttention(Scan):
    """An executing project needs coaching.

    Findings: the top project's context, so the run starts coaching.
    """
    type_name = "projects_attention"

    def scan(self, ctx: ScheduleContext) -> WorkDecision:
        raise NotImplementedError("projects_attention is not built yet")
