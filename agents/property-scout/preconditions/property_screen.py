"""property_screen: property-scout's weekly scout runs only when new listings match."""
from core.scheduler.preconditions.base import PreconditionError
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report


class PropertyScreen(Scan):
    """New listings match the real_estate_scout criteria.

    Runs the property_screen tool's search: criteria.md's hard filters over one
    Zillow job for the whole target area, minus the listings the skill already
    archived (TOUR reports in properties/, PASS lines in passed.md). Every
    request to that API counts against the RapidAPI quota, result reads
    included, so the scan reads sparsely: one submit and at most eight reads,
    nine requests a week (SCAN_REQUESTS, a hard limit), where the agent's own
    search submitted per call and read every 2 s. A job still running after
    the last read is a timeout: logged, not posted, and the next run retries.

    What the screen couldn't read is in the report: criteria.md lines that
    didn't read and Zillow's per-URL errors ride along with the findings (or
    are posted on a dry week); no criteria file, nowhere to search, or no
    listings back skip the run and are posted. So does Zillow refusing a
    request with a 4xx, a spent quota (429) or a rejected key (401); a 5xx or
    a dropped connection is only logged.

    Findings: the new matches, with the listing tool's numbers, then
    criteria.md's lines the filters don't cover, for the verdict.
    """
    type_name = "property_screen"

    def validate(self):
        super().validate()
        if self.args:
            raise PreconditionError("'property_screen' takes no args: the filters live in criteria.md")

    def scan(self, ctx: ScheduleContext, report: Report) -> WorkDecision:
        from agents.property_scout.lib.listings import render_matches
        from agents.property_scout.lib.scout import SCAN_POLLING, JobPending, run_screen

        try:
            run = run_screen(report, polling=SCAN_POLLING)
        except JobPending as e:  # transient: logged, not posted
            raise TimeoutError(str(e)) from e
        matches, evaluated = run.matches, run.already_evaluated
        if not matches:
            return WorkDecision(
                False, f"no new matches: {run.pulled} listings pulled, {evaluated} matches already evaluated"
            )
        header = (
            f"{len(matches)} new listing{'s' if len(matches) != 1 else ''} pass criteria.md "
            f"({run.pulled} pulled; {evaluated} more matches were already evaluated)."
        )
        parts = [header, render_matches(matches)]
        if run.unread:
            parts.append("criteria.md lines the screen couldn't read, so their filters weren't applied:\n"
                         + "\n".join(run.unread))
        if run.to_judge:
            parts.append("criteria.md lines the filters don't cover; judge each listing against them:\n"
                         + "\n".join(run.to_judge))
        return WorkDecision(
            True,
            f"{len(matches)} new matches of {run.pulled} listings",
            detail="\n\n".join(parts),
        )
