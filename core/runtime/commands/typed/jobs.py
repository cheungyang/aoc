"""`!jobs`: background jobs that are queued, running, being killed or ended in
error, newest first. And what `!job` and `!kill` share: how a job is written.

Read from this process's JobManager (core/runtime/foundation/jobs.py) and the
session log, with no model. These replace the Concierge's job tools, which
needed a model turn to print a table.
"""
import time
from datetime import datetime
from typing import Optional

from core.runtime.commands.base import ChannelCommand, TypedContext
from core.runtime.foundation.jobs import JobManager
from core.storage.sessions import SessionStore

MAX_JOBS = 20
PROMPT_MAX_CHARS = 80
REPLY_MAX_CHARS = 600
# What `!jobs` lists. Completed and killed jobs stay out, as in JobManager's
# own default; `killing` is in, so a job asked to stop shows until it does.
LISTED_STATUSES = ("queued", "running", "killing", "error", "partial")


def age(seconds: float) -> str:
    """'42s', '5m', '3h', '2d'."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def truncate(text: str, limit: int = PROMPT_MAX_CHARS) -> str:
    """`text` on one line, cut to `limit` characters."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def started_at(job) -> str:
    return datetime.fromtimestamp(job.started).strftime("%m-%d %H:%M")


def format_job(job, now: float) -> str:
    """One line: id, status, agent, when it started, and how its prompt begins."""
    line = f"`{job.job_id}` **{job.status}** {job.agent_id} · {started_at(job)} ({age(now - job.started)} ago)"
    prompt = truncate(job.prompt)
    if prompt:
        line += f' · "{prompt}"'
    return line


def last_reply(session_id: str, store: Optional[SessionStore] = None) -> str:
    """The latest model reply in the job's session log, shortened; "" if none."""
    store = store or SessionStore()
    for entry in reversed(store.load_history(session_id, limit=50)):
        message = str(entry.get("message") or "").strip()
        if entry.get("from") == "ai" and message:
            return truncate(message, REPLY_MAX_CHARS)
    return ""


def jobs_text(manager: Optional[JobManager] = None, now: Optional[float] = None) -> str:
    """The reply to `!jobs`."""
    manager = manager or JobManager()
    now = time.time() if now is None else now
    jobs = sorted(manager.get_jobs(LISTED_STATUSES), key=lambda job: job.started, reverse=True)
    if not jobs:
        return "No active jobs."
    shown = jobs[:MAX_JOBS]
    header = f"**{len(jobs)} job(s)**"
    if len(jobs) > len(shown):
        header += f", newest {len(shown)} shown"
    lines = [header] + [format_job(job, now) for job in shown]
    lines.append("`!job <id>` shows one; `!kill <id>` stops one.")
    return "\n".join(lines)


class JobsCommand(ChannelCommand):
    name = "jobs"
    summary = "Lists background jobs that are queued, running, being killed or ended in error, newest first."

    async def run(self, ctx: TypedContext, args: str) -> None:
        await ctx.reply(jobs_text())
