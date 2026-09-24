import time
from datetime import datetime
from langchain_core.tools import tool
from core.runtime.job_manager import JobManager
from core.util import format_tool_response

MAX_JOBS = 20
PROMPT_MAX_CHARS = 80


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _truncate(text: str, limit: int = PROMPT_MAX_CHARS) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_job(job, now: float) -> str:
    started = datetime.fromtimestamp(job.started).strftime("%m-%d %H:%M")
    line = f"{job.job_id} {job.status} {job.agent_id} {started} ({_age(now - job.started)} ago)"
    prompt = _truncate(getattr(job, "prompt", ""))
    if prompt:
        line += f' "{prompt}"'
    return line


@tool
def job_list() -> str:
    """
    Returns a compact list of active background jobs (queued/running/error/partial),
    newest first, one line per job: job_id status agent_id started (age) "prompt".
    Prompts are truncated; use job_status(job_id) for details.
    """
    now = time.time()
    jobs = sorted(JobManager().get_jobs(), key=lambda j: j.started, reverse=True)
    if not jobs:
        return format_tool_response("job_list", payload="No active jobs.", errors="None")

    shown = jobs[:MAX_JOBS]
    lines = [_format_job(job, now) for job in shown]
    omitted = len(jobs) - len(shown)
    header = f"{len(jobs)} active job(s)"
    if omitted:
        header += f", showing newest {len(shown)}; {omitted} older omitted"
    payload = header + ":\n" + "\n".join(lines)
    return format_tool_response("job_list", payload=payload, errors="None")
