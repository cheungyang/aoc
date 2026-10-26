"""`!kill <id>`: stops a background job, and says how it ended.

A kill is a request. JobManager marks the job `killing`, and the job stops
itself at its next safe point, before its next tool call (`JobManager.honor_kill`).
The command waits a little for that, without blocking the bot, then reports.
Only a job this process runs can be stopped: the request is honored in memory.
"""
import asyncio
from typing import Optional

from core.runtime.commands.base import ChannelCommand, TypedContext
from core.runtime.commands.typed.jobs import last_reply
from core.runtime.foundation.jobs import TERMINAL_STATUSES, JobManager
from core.storage.sessions import SessionStore

USAGE = "Usage: `!kill <id>`. `!jobs` lists them."
WAIT_SECONDS = 10.0
POLL_SECONDS = 0.5


async def kill_text(job_id: str, manager: Optional[JobManager] = None, store: Optional[SessionStore] = None,
                    wait_s: float = WAIT_SECONDS, poll_s: float = POLL_SECONDS) -> str:
    """Asks job `job_id` to stop, waits up to `wait_s` for it, and says how it ended."""
    job_id = (job_id or "").strip()
    if not job_id:
        return USAGE
    manager = manager or JobManager()

    job = manager.job(job_id)
    if job is None:
        known = manager.get_job(job_id)
        if known is not None and known.status in TERMINAL_STATUSES:
            return f"`{job_id}` already ended: **{known.status}**."
        return f"No job `{job_id}` running here. `!jobs` lists them."
    if job.status in TERMINAL_STATUSES:
        return f"`{job_id}` already ended: **{job.status}**."

    manager.kill_job(job_id)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while job.status not in TERMINAL_STATUSES and loop.time() < deadline:
        await asyncio.sleep(poll_s)
        job = manager.job(job_id) or job

    if job.status == "killed":
        reply = last_reply(job.session_id, store)
        text = f"🛑 Killed `{job_id}` ({job.agent_id})."
        return f"{text}\nLast reply: {reply}" if reply else text
    if job.status in TERMINAL_STATUSES:
        return f"`{job_id}` ended as **{job.status}** before the kill took effect."
    return f"⏳ Asked `{job_id}` to stop. It stops before its next step; `!job {job_id}` shows when."


class KillCommand(ChannelCommand):
    name = "kill"
    summary = "Stops a background job at its next step, and says how it ended."
    usage = "<job id>"

    async def run(self, ctx: TypedContext, args: str) -> None:
        await ctx.reply(await kill_text(args))
