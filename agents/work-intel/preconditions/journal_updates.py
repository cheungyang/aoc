"""journal_updates: the afternoon sparring runs only when a recent journal is new."""
import os
from datetime import datetime

from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report


class JournalUpdates(Scan):
    """A recent daily journal is new or updated since the last sparring session.

    Takes the `lookback_days` (RULES.md) newest daily journals with an entry in
    pkm/vault/journals/work/ (`agents.work_intel.lib.journals`) and fires
    when one of them was modified after the last successful run
    (`ctx.last_success_at`); before the first one, any of them does. An edit to
    an older journal, a journal with only its front matter and weekly_rollups.md
    never fire it. A journals folder that isn't there is posted as an issue.

    Findings: those journals, newest first, the new ones marked: the context the
    work_sparring skill reads.
    """
    type_name = "journal_updates"

    def scan(self, ctx: ScheduleContext, report: Report) -> WorkDecision:
        from core.tooling import display_path, get_local_timezone, load_rules, rules_path
        from agents.work_intel.lib.journals import journals_folder, recent_journals
        from agents.work_intel.lib.rules import RULES

        lookback = load_rules(ctx.agent_id, RULES, report)["lookback_days"]
        if lookback < 1:
            default = next(rule.default for rule in RULES if rule.key == "lookback_days")
            report.problem(rules_path(ctx.agent_id),
                           f"lookback_days is {lookback}, but at least 1 journal must be read; using the default {default}")
            lookback = default

        folder = journals_folder()
        if not os.path.isdir(folder):
            report.problem(folder, "folder not found, so new journal entries can't be checked")
            return WorkDecision(False, "no journals folder to check")
        recent = recent_journals(folder, lookback, report)
        if not recent:
            return WorkDecision(False, f"no daily journal with an entry in {display_path(folder)}")

        since = ctx.last_success_at
        if since is None:
            new, last = recent, "none yet"
        else:
            new = [journal for journal in recent if journal.modified > since]
            last = f"{datetime.fromtimestamp(since, get_local_timezone()):%a %Y-%m-%d %H:%M}"
        if not new:
            return WorkDecision(False, f"no recent journal new since the last sparring session ({last})")

        lines = [f"The {len(recent)} most recent daily journals (lookback_days: {lookback}), newest first. "
                 f"(new) marks one new or updated since the last sparring session ({last}):"]
        lines += [f"- {display_path(journal.path)}{' (new)' if journal in new else ''}" for journal in recent]
        dates = ", ".join(journal.day.isoformat() for journal in new)
        return WorkDecision(True, f"{len(new)} recent journal(s) new since the last sparring session ({last}): {dates}",
                            detail="\n".join(lines))
