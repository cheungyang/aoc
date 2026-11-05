"""coding_attention: Elephant is woken when the coding queues need a decision.

Reads every project manifest (pkm/wiki/software/<project>/build_request.json)
and asks `agents.software_planner.lib.coding_queue` what needs Elephant:
halted tasks, the project-level `tick_error` the coding scheduler records, and
PRs whose review the tick stopped checking. It fires only for items whose
event is newer than his last successful check (`ctx.last_success_at`), so a
handled halt never wakes him twice, and a turn that failed leaves its items
new for the next run.

It runs only on the host that ticks (`tick_enabled`, the gate
coding_tick.has_work uses): only that host's manifests are current.

Findings: each new item in full (its last error, pending review feedback, PR,
spec, the tasks waiting behind it, Elephant's fixes so far), then the
still-open ones in a line each. A manifest that can't be read is an issue:
posted on a quiet run, and carried in the findings on a run with work.
"""
import os
from typing import List, Optional

from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report


class CodingAttention(Scan):
    """Something in the coding queues needs Elephant (module docstring)."""
    type_name = "coding_attention"
    # The folder with one folder per project; None for the repo's pkm/wiki/software.
    software_root: Optional[str] = None

    def scan(self, ctx: ScheduleContext, report: Report) -> WorkDecision:
        from core.tooling import load_rules
        from graphs.coding.utils.host import tick_enabled
        from graphs.coding.utils.manifest import load_manifest
        from agents.software_planner.lib.coding_queue import describe, findings, queue_items
        from agents.software_planner.lib.rules import RULES

        enabled, why = tick_enabled()
        if not enabled:
            return WorkDecision(False, f"this host doesn't tick the coding queues: {why}")

        rules = load_rules(ctx.agent_id, RULES, report)
        items = []
        for path in self._manifests():
            project = os.path.basename(os.path.dirname(path))
            try:
                manifest = load_manifest(path)
            except Exception as e:
                report.problem(path, f"can't be read, so its queue wasn't checked ({e.__cause__ or e})")
                continue
            items += queue_items(manifest, project, ctx.now, rules)

        if not items:
            return WorkDecision(False, "nothing in the coding queues needs a decision")
        new = [item for item in items if item.is_new(ctx.last_success_at)]
        if not new:
            return WorkDecision(False, f"nothing new in the coding queues ({len(items)} still open)")
        still_open = [item for item in items if not item.is_new(ctx.last_success_at)]
        return WorkDecision(True, describe(new), detail=findings(new, still_open, ctx.now))

    def _manifests(self) -> List[str]:
        from core.scheduler.preconditions.files import PROJECT_ROOT
        from graphs.coding.utils.dag import SOFTWARE_ROOT, discover_manifests

        return discover_manifests(self.software_root or os.path.join(PROJECT_ROOT, SOFTWARE_ROOT))
