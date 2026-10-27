"""`!job <id>`: one background job -- its record, and the last thing it said --
read from JobManager and the session log, with no model.

It took over from the Concierge's `job_status` tool, since deleted, which
handed the session log to another agent to summarize.
"""
import time
from typing import Optional

from core.runtime.commands.base import ChannelCommand, TypedContext
from core.runtime.commands.typed.jobs import age, last_reply, started_at, truncate
from core.runtime.foundation.jobs import JobManager
from core.storage.sessions import SessionStore

USAGE = "Usage: `!job <id>`. `!jobs` lists them."
PROMPT_DETAIL_CHARS = 300


def job_text(job_id: str, manager: Optional[JobManager] = None, store: Optional[SessionStore] = None,
             now: Optional[float] = None) -> str:
    """The reply to `!job {job_id}`. A finished job is found too, while the store keeps it."""
    job_id = (job_id or "").strip()
    if not job_id:
        return USAGE
    manager = manager or JobManager()
    now = time.time() if now is None else now
    job = manager.get_job(job_id)
    if job is None:
        return f"No job `{job_id}`. `!jobs` lists them."

    lines = [
        f"`{job.job_id}` **{job.status}** · {job.agent_id}",
        f"Started {started_at(job)} ({age(now - job.started)} ago) · updated {age(now - job.updated)} ago",
        f"Session: `{job.session_id}`",
    ]
    prompt = truncate(job.prompt, PROMPT_DETAIL_CHARS)
    if prompt:
        lines.append(f"Prompt: {prompt}")
    reply = last_reply(job.session_id, store)
    lines.append(f"Last reply: {reply}" if reply else "No reply logged yet.")
    return "\n".join(lines)


class JobCommand(ChannelCommand):
    name = "job"
    summary = "Shows one background job: its status, its prompt and its last reply."
    usage = "<job id>"

    async def run(self, ctx: TypedContext, args: str) -> None:
        await ctx.reply(job_text(args))
