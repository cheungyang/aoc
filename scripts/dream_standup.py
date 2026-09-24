#!/usr/bin/env python3
"""The nightly dream standup, as a script rather than a prompt.

This replaces a five-step instruction that the orchestrator executed on a
`gemini-3.1-pro` turn every night. The instruction had grown defensive --

    "You MUST pause and WAIT for these tool calls to return.
     DO NOT hallucinate their responses."

-- which is the tell. Fanning a fixed trigger out to a known list of agents and
tabulating the replies is not a reasoning task; asking a model to do it costs a
long pro-model run and can still invent a row. Here the fan-out is a loop and
the table is a format string, so a missing reply is visibly missing.

The same reasoning applies to each dream's file I/O. Agents used to list, read,
overwrite and delete their own memory files with the `filesystem` tool, and the
weakest models got the path wrong (`agents/<id>/...` without `pkm/`), were
denied, and reported "No new memories" night after night while the logs piled
up. Now the script reads the logs and the current `MEMORY.md`, `FEEDBACK.md` and
`CONTEXT.md`, hands them to the agent inline, and takes the rewritten files back
as XML. The agent only synthesises; the script validates, writes and deletes.

Run by `script-executor` on a cron. Its stdout becomes the channel message, so
anything printed to stdout is user-facing and anything diagnostic goes to stderr.
"""
import argparse
import asyncio
import contextlib
import datetime
import io
import os
import re
import sys
import tempfile

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.loaders.agents_loader import AgentsLoader
from core.runtime.execution_context import surface_scope
from core.scheduler.script_runner import default_timeout
from core.util.config import Config
from tools.agent_call import agent_call

DREAM_TRIGGER = "Run your dream skill."

# `script-executor` is a shell runner with no model behind it, so it has no
# memory logs to consolidate. The graph workers are stateless by configuration:
# they keep no session between invocations, so there is nothing for a dream to
# synthesise.
EXCLUDED_AGENTS = {"script-executor"}

# The agents run real model turns. Firing all of them at once is a burst of
# concurrent pro/flash calls against one quota; the system-wide
# `Config().max_concurrency` pool keeps the standup well inside it while still
# finishing far faster than sequential execution.
#
# The whole standup runs against one deadline: the scheduler kills this script
# after `default_timeout()` (AOC_SCRIPT_TIMEOUT), and a killed standup posts no
# report and never restores the logs of the dreams still in flight. Stopping a
# margin short of the kill leaves time for that cleanup and for the report. A
# dream that runs out of time keeps its logs and is retried the next night.
DEADLINE_MARGIN = 30

STATUS_DREAMED = "Dreamed"
STATUS_EMPTY = "No new memories"

# The long-term files the dream rewrites, and the reply tag carrying each one.
MEMORY_FILES = (
    ("MEMORY.md", "memory_md"),
    ("FEEDBACK.md", "feedback_md"),
    ("CONTEXT.md", "context_md"),
)

# A reply that echoes the skill's template back ("[Strictly either ...]")
# instead of filling it in. Writing that would replace real memory with a
# placeholder, so it is rejected.
PLACEHOLDER = re.compile(r"^\[[^\n]*\]$")

RESPONSE_FORMAT = """<dream_response>
  <status>Dreamed or No new memories</status>
  <memory_md>full new contents of MEMORY.md</memory_md>
  <feedback_md>full new contents of FEEDBACK.md</feedback_md>
  <context_md>full new contents of CONTEXT.md</context_md>
  <errors>None</errors>
  <learnings>one-line highlight for the standup</learnings>
</dream_response>"""


def memory_logs_root(root=None):
    """`<vault>/agents`. The vault is `Config().pkm_dir`, not a path under the
    project: `<project>/pkm` exists only as a symlink on some hosts."""
    return os.path.join(root or Config().pkm_dir, "agents")


def agent_memory_dir(agent_id, root=None):
    return os.path.join(memory_logs_root(root), agent_id)


def agent_logs_dir(agent_id, root=None):
    return os.path.join(agent_memory_dir(agent_id, root), "memory_logs")


def list_logs(agent_id, root=None):
    """Sorted names of an agent's memory log files, hidden files excluded."""
    logs_dir = agent_logs_dir(agent_id, root)
    try:
        entries = os.listdir(logs_dir)
    except OSError:
        return []
    names = []
    for name in entries:
        if name.startswith("."):
            continue
        try:
            if os.path.isfile(os.path.join(logs_dir, name)):
                names.append(name)
        except OSError:
            continue
    return sorted(names)


def agents_with_logs(root=None):
    """Agent ids with at least one memory log.

    A dream consumes and deletes its logs, so "has a log" is exactly "has
    something to dream about". An agent whose dream fails keeps its logs and
    is dreamed again the next night.
    """
    try:
        agent_ids = os.listdir(memory_logs_root(root))
    except OSError:
        return set()
    return {agent_id for agent_id in agent_ids if list_logs(agent_id, root)}


def has_work(ctx):
    """Scheduler gate: skip the whole standup when no agent has a memory log."""
    agents = agents_with_logs()
    if not agents:
        return False, "no memory logs to dream about"
    return True, f"memory logs for: {', '.join(sorted(agents))}"


@contextlib.contextmanager
def quiet_stdout(verbose: bool = False):
    """Keeps stdout for the standup report alone.

    The runtime narrates itself on stdout — graph reloads, tool rosters, every
    tool call the worker makes. That is fine in a terminal and wrong here: the
    script-executor posts this script's stdout to Discord verbatim, so the
    narration became the message and the one line that mattered was buried in it.
    Captured chatter is discarded, or sent to stderr under `--verbose`.
    """
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            yield
    finally:
        noise = buffer.getvalue()
        if verbose and noise.strip():
            sys.stderr.write(noise)


def standup_agents(loader):
    """Agents with memory worth consolidating, in a stable order."""
    selected = []
    for agent_id in sorted(loader.list_agent_ids()):
        if agent_id in EXCLUDED_AGENTS:
            continue
        config = loader.get_agent_config(agent_id) or {}
        if config.get("stateless"):
            continue
        selected.append((agent_id, config))
    return selected


def resolve_channel(config):
    """A channel the agent is actually permitted to answer in.

    Delegation enforces `channels` as a permission, so a standup that assumed
    `#general` would silently skip any agent scoped to one channel --
    `property-scout` is exactly that case.
    """
    channels = config.get("channels") or []
    if "*" in channels or "general" in channels:
        return "general"
    return channels[0] if channels else "general"


def find_tag(tag, text):
    """Returns the stripped contents of `<tag>`, or None when it is absent.

    Distinguishes a missing tag from an empty one: an empty `<context_md>` is a
    legitimate "this file is now empty", a missing one is a malformed reply.
    """
    if not text:
        return None
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else None


def extract_tag(tag, text):
    """Returns the contents of `<tag>`, or an empty string."""
    return find_tag(tag, text) or ""


def parse_dream_response(raw):
    """Reads the dream reply into (status, learnings).

    A reply that isn't the documented XML is reported as-is rather than guessed
    at: an agent that answered conversationally has not dreamt, and quietly
    rendering it as "No new memories" would hide that.
    """
    if not raw or not raw.strip():
        return None, "empty response"

    dream_xml = extract_tag("dream_response", raw) or raw
    status = extract_tag("status", dream_xml)
    learnings = extract_tag("learnings", dream_xml)

    if not status:
        return None, "no <dream_response> in reply"

    return status, learnings


def read_text(path):
    """A file's contents, or an empty string when it does not exist."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def snapshot_logs(agent_id, root=None):
    """{name: contents} of every log the dream is about to consume."""
    logs_dir = agent_logs_dir(agent_id, root)
    return {name: read_text(os.path.join(logs_dir, name)) for name in list_logs(agent_id, root)}


def read_memory_files(agent_id, root=None):
    """{file name: contents} of the current long-term files."""
    base = agent_memory_dir(agent_id, root)
    return {name: read_text(os.path.join(base, name)) for name, _ in MEMORY_FILES}


def build_prompt(agent_id, memory_files, logs):
    """The dream trigger with everything the agent needs inlined.

    The response format is stated here as well as in the skill: this prompt is
    the contract the parser below enforces, and the weakest models follow what
    is in front of them more reliably than a loaded skill.
    """
    current = "\n".join(
        f'<file name="{name}">\n{text.strip()}\n</file>' for name, text in memory_files.items()
    )
    log_blocks = "\n".join(
        f'<log name="{name}">\n{text.strip()}\n</log>' for name, text in logs.items()
    )
    return (
        f"{DREAM_TRIGGER}\n"
        "Your current memory files and your unprocessed memory logs are inlined "
        "below. Do not use any tools: the files are read and written for you. "
        "Do not emit a <system_memory_log>.\n"
        f'<dream_input agent_id="{agent_id}">\n'
        f"<current_files>\n{current}\n</current_files>\n"
        f"<memory_logs>\n{log_blocks}\n</memory_logs>\n"
        "</dream_input>\n"
        "Reply with only this XML. On Dreamed, each *_md tag holds the complete "
        "new file, not a diff; on No new memories, omit them.\n"
        f"{RESPONSE_FORMAT}"
    )


def write_atomic(path, text):
    """Replaces `path` in one step, so a crash never leaves a half-written file."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".dream-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def restore_logs(agent_id, snapshot, root=None):
    """Undoes anything written to the logs while the dream ran.

    A dream reply carrying a `<system_memory_log>` is saved by the runtime as
    today's log, and a dream about logs that writes a log is one the next night
    dreams about again, forever. Whatever appeared during the call -- a new file,
    or text appended to one that was snapshotted -- is the dream's own and is
    removed.
    """
    logs_dir = agent_logs_dir(agent_id, root)
    for name in list_logs(agent_id, root):
        path = os.path.join(logs_dir, name)
        if name not in snapshot:
            with contextlib.suppress(OSError):
                os.remove(path)
        elif read_text(path) != snapshot[name]:
            write_atomic(path, snapshot[name])


def delete_logs(agent_id, names, root=None):
    logs_dir = agent_logs_dir(agent_id, root)
    for name in names:
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(logs_dir, name))


def parse_memory_files(dream_xml):
    """{file name: new contents} from a Dreamed reply, or (None, reason)."""
    contents = {}
    for name, tag in MEMORY_FILES:
        text = find_tag(tag, dream_xml)
        if text is None:
            return None, f"Dreamed without <{tag}>; nothing written"
        if PLACEHOLDER.match(text):
            return None, f"<{tag}> is the template placeholder; nothing written"
        contents[name] = text
    return contents, None


def apply_dream(agent_id, raw, root=None):
    """Validates a dream reply and applies it. Returns (status, learnings, error).

    Only a well-formed reply changes anything. On Dreamed all three files must
    be present and real; they are written and the consumed logs deleted. On No
    new memories the logs are deleted and nothing is written. Anything else
    leaves the logs in place so the next night retries them.
    """
    dream_xml = find_tag("dream_response", raw) or raw or ""
    errors = find_tag("errors", dream_xml)
    if errors and errors.lower() != "none":
        return None, None, errors

    status, learnings = parse_dream_response(raw)
    if status is None:
        return None, None, learnings

    if status.lower() == STATUS_EMPTY.lower():
        return STATUS_EMPTY, learnings, None
    if status.lower() != STATUS_DREAMED.lower():
        return None, None, f"unrecognised status: {status}"

    contents, error = parse_memory_files(dream_xml)
    if error:
        return None, None, error

    base = agent_memory_dir(agent_id, root)
    for name, text in contents.items():
        write_atomic(os.path.join(base, name), text + "\n" if text else "")
    return STATUS_DREAMED, learnings, None


def standup_deadline():
    """The event-loop time by which every dream must be finished."""
    return asyncio.get_running_loop().time() + max(default_timeout() - DEADLINE_MARGIN, 1)


async def dream(agent_id, config, semaphore, deadline):
    """Runs one agent's dream end to end and normalises the outcome."""
    channel = resolve_channel(config)
    failed = {"agent_id": agent_id, "config": config}

    async with semaphore:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return {**failed, "error": "standup deadline reached before this dream started"}
        # Snapshot inside the semaphore: a queued dream must see the logs as
        # they are when it runs, not as they were when the standup started.
        logs = snapshot_logs(agent_id)
        prompt = build_prompt(agent_id, read_memory_files(agent_id), logs)
        try:
            raw = await asyncio.wait_for(
                agent_call.ainvoke({
                    "agent_id": agent_id,
                    "prompt": prompt,
                    "channel": channel,
                    "caller": "script-executor",
                }),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            restore_logs(agent_id, logs)
            return {**failed, "error": f"timed out: standup deadline reached after {int(remaining)}s"}
        except (Exception, asyncio.CancelledError) as e:
            restore_logs(agent_id, logs)
            return {**failed, "error": str(e) or type(e).__name__}

        restore_logs(agent_id, logs)
        try:
            status, learnings, error = apply_dream(agent_id, raw)
        except OSError as e:
            return {**failed, "error": f"could not write memory files: {e}"}
        if error:
            return {**failed, "error": error}

        delete_logs(agent_id, logs)

    return {
        "agent_id": agent_id,
        "config": config,
        "status": status,
        "learnings": learnings,
    }


def render_row(result):
    config = result["config"]
    emoji = config.get("emoji", "🤖")
    name = config.get("name", result["agent_id"])

    if result.get("error"):
        return f"{emoji} {name} | ⚠️ Standup failed: {result['error']}"

    status = (result.get("status") or "").strip()
    learnings = (result.get("learnings") or "").strip()

    if status.lower().startswith(STATUS_DREAMED.lower()):
        detail = learnings or "no notable learnings recorded."
        return f"{emoji} {name} | 🌙 Dreamed -> Key Update: {detail}"

    return f"{emoji} {name} | 💤 No new memories to process today."


def render(results, today=None):
    today = today or datetime.date.today().isoformat()
    lines = [f"**Nightly Dream Standup - {today}**", ""]
    lines.extend(render_row(r) for r in results)
    return "\n".join(lines)


def filter_agents_with_logs(agents, root=None):
    """Keeps only the agents that have something to dream about.

    Each dream is a paid model turn; an agent with no memory log would only
    answer "No new memories", so it is not asked at all.
    """
    with_logs = agents_with_logs(root)
    return [(agent_id, config) for agent_id, config in agents if agent_id in with_logs]


async def run_standup(verbose: bool = False, include_all: bool = False):
    with quiet_stdout(verbose=verbose):
        loader = AgentsLoader()
        agents = standup_agents(loader)
        if not include_all:
            agents = filter_agents_with_logs(agents)
        if not agents:
            print("No agents to include in the standup.", file=sys.stderr)
            return ""

        print(f"Dream standup: triggering {len(agents)} agents...", file=sys.stderr)

        semaphore = asyncio.Semaphore(Config().max_concurrency)
        deadline = standup_deadline()
        raw_results = await asyncio.gather(
            *(dream(agent_id, config, semaphore, deadline) for agent_id, config in agents),
            return_exceptions=True,
        )

        results = []
        for (agent_id, config), res in zip(agents, raw_results):
            if isinstance(res, BaseException):
                results.append({
                    "agent_id": agent_id,
                    "config": config,
                    "error": str(res) or type(res).__name__,
                })
            elif isinstance(res, dict):
                results.append(res)
            else:
                results.append({
                    "agent_id": agent_id,
                    "config": config,
                    "error": f"Unexpected result: {res}",
                })

    return render(results)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the nightly dream standup.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the agents that would be triggered, without calling them.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Write captured runtime narration to stderr.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Dream every eligible agent, even those with no memory logs.",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        loader = AgentsLoader()
        agents = standup_agents(loader)
        if not args.all:
            agents = filter_agents_with_logs(agents)
        for agent_id, config in agents:
            print(f"{agent_id} -> #{resolve_channel(config)}")
        return

    try:
        summary = asyncio.run(
            run_standup(verbose=args.verbose, include_all=args.all)
        )
        if summary:
            print(summary)
    except Exception as e:
        print(f"Standup execution failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    # Run as a script, this is the nightly scheduled standup: the dreams it
    # triggers are booked as scheduled work.
    with surface_scope("scheduled"):
        main()
