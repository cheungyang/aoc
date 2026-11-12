"""missing_journal: the morning nudge runs only while today's journal isn't in the vault."""
import os
from datetime import datetime

from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report


class MissingJournal(Scan):
    """Today's journal has no entry yet, so the nudge to paste the 6 AM update is worth sending.

    Reads today's daily journal, by local date, in pkm/vault/journals/work/
    (`agents.work_intel.lib.journals`) and skips the run when it holds an
    entry; a file with only its front matter doesn't count. A journals folder
    that isn't there rides along with the nudge as an issue.

    Findings: the file today's update goes in.
    """
    type_name = "missing_journal"

    def scan(self, ctx: ScheduleContext, report: Report) -> WorkDecision:
        from core.tooling import display_path, get_local_timezone
        from agents.work_intel.lib.journals import has_entry, journal_path, journals_folder, read_journal

        today = datetime.fromtimestamp(ctx.now, get_local_timezone()).date()
        path = journal_path(today)
        text = read_journal(path)
        if text is not None and has_entry(text):
            return WorkDecision(False, f"journal for {today.isoformat()} already in ({display_path(path)})")
        if not os.path.isdir(journals_folder()):
            report.problem(journals_folder(), "folder not found, so today's journal can't be checked")
        state = "doesn't exist yet" if text is None else "holds only its front matter so far"
        return WorkDecision(True, f"no journal entry for {today.isoformat()} yet",
                            detail=f"Today's update goes in {display_path(path)}, which {state}.")
