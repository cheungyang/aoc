"""`!python [--yes] [--timeout <seconds>] <script.py> [args…]`: runs one of the
project's scripts where the bot runs -- the NAS, in prod -- and posts what it
printed, so a diagnostic needs no shell on the host.

Only the bot's owner may run one (`TypedContext.is_owner`: discord.py's
Bot.is_owner, the Discord application's owner or team). The script must be a
bare `*.py` name in `scripts/`, and it runs as a scheduled script does: the
project interpreter, from the repo root, stdin closed, no shell, under a
timeout -- AOC_SCRIPT_TIMEOUT unless `--timeout` says otherwise, at most
MAX_TIMEOUT_S. The scripts come in the context (`TypedContext.scripts`, the
Scripts port in ../ports.py), so this command never imports the scheduler.

A run that may change something goes ahead only with `--yes`. A script that
changes nothing says so at module level, and lists the flags that make it
write after all; both are read by parsing the file, never by importing it:

    READ_ONLY = True
    WRITES_WITH = ("--archive-all",)

Otherwise -- no `READ_ONLY = True`, or one of those flags given -- nothing runs
without `--yes`, and the reply says why and how to confirm. The options go
before the script's name: everything after it is the script's own
(coding_admin.py has a `--yes` of its own).

One script runs at a time, across every bot in the process. The status line
is edited with the outcome, then the output follows as a code block; when it
is long, its two ends are shown and the whole of it is attached. A line
`ATTACH: <path>` in the output attaches that file in its place, when it is
under sessions/ or the temp directory and small enough for Discord.
"""
import os
import shlex
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from core.runtime.commands.base import ChannelCommand, TypedContext
from core.runtime.commands.ports import ScriptRun

USAGE = "Usage: `!python [--yes] [--timeout <seconds>] <script.py> [args…]`, for a script in `scripts/`."
MAX_TIMEOUT_S = 3 * 3600
INLINE_CHARS = 6000   # longer output: its two ends shown, the whole of it attached
END_CHARS = 1500      # how much of each end is shown
ATTACH_PREFIX = "ATTACH:"
MAX_ATTACH_BYTES = 8 * 1024 * 1024  # Discord's upload limit without server boosts

# One run at a time. A threading lock, not an asyncio one: each bot may run
# on its own event loop.
_lock = threading.Lock()
_running: Optional[str] = None


@dataclass
class Invocation:
    """What `!python` was asked: the script and its arguments, and the options."""
    script: str
    args: Tuple[str, ...] = ()
    confirmed: bool = False        # --yes
    timeout: Optional[int] = None  # --timeout, in seconds

    @property
    def label(self) -> str:
        return " ".join((self.script, *self.args))


def parse(args: str) -> Invocation:
    """The options, then the script and its arguments. A ValueError says what's
    wrong, as the reply."""
    try:
        parts = shlex.split(args or "")
    except ValueError as e:
        raise ValueError(f"Can't read that: {e}.") from None
    confirmed, timeout = False, None
    while parts and parts[0].startswith("--"):
        option = parts.pop(0)
        if option == "--yes":
            confirmed = True
        elif option == "--timeout":
            timeout = _seconds(parts.pop(0) if parts else "")
        elif option.startswith("--timeout="):
            timeout = _seconds(option.partition("=")[2])
        else:
            raise ValueError(f"Unknown option `{option}`. Before the script's name: `--yes`, `--timeout <seconds>`.")
    if not parts:
        raise ValueError(USAGE)
    return Invocation(parts[0], tuple(parts[1:]), confirmed, timeout)


def _seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError:
        raise ValueError(f"`--timeout` takes whole seconds, not `{value or 'nothing'}`.") from None
    if not 1 <= seconds <= MAX_TIMEOUT_S:
        raise ValueError(f"`--timeout` is 1 to {MAX_TIMEOUT_S} seconds.")
    return seconds


def split_attachments(stdout: str) -> Tuple[str, List[str]]:
    """The output without its `ATTACH: <path>` lines, and those paths."""
    kept, paths = [], []
    for line in stdout.splitlines():
        if line.startswith(ATTACH_PREFIX):
            paths.append(line[len(ATTACH_PREFIX):].strip())
        else:
            kept.append(line)
    return "\n".join(kept), paths


def attachment(path: str, root: str) -> Tuple[Optional[str], str]:
    """(the file to attach, "") for a path that may be attached, else (None, why not).

    A relative path is from `root`, the repo root. It must be an existing file
    under sessions/ or the temp directory, at most MAX_ATTACH_BYTES.
    """
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    allowed = (os.path.realpath(os.path.join(root, "sessions")), os.path.realpath(tempfile.gettempdir()))
    if not any(full.startswith(base + os.sep) for base in allowed):
        return None, "only files under sessions/ or the temp directory are attached"
    if not os.path.isfile(full):
        return None, "no such file"
    if os.path.getsize(full) > MAX_ATTACH_BYTES:
        return None, f"over {MAX_ATTACH_BYTES // (1024 * 1024)} MB"
    return full, ""


def output_text(stdout: str, stderr: str) -> str:
    """What the script printed: stdout, then stderr under its own heading."""
    out = stdout.strip("\n").rstrip()
    err = stderr.strip("\n").rstrip()
    return "\n\n".join(part for part in (out, f"stderr:\n{err}" if err else "") if part)


def status_line(run: ScriptRun) -> str:
    label = _shown(run.label)
    took = f"{run.seconds:.1f} s" if run.seconds < 120 else f"{run.seconds / 60:.1f} min"
    if run.timed_out:
        return f"⌛ `{label}` was stopped at its {run.timeout} s timeout."
    if run.ok:
        return f"✅ `{label}` finished in {took}."
    return f"❌ `{label}` exited with {run.returncode} after {took}."


def _shown(text: str) -> str:
    """`text` safe inside inline code."""
    return text.replace("`", "'")


def _fenced(text: str) -> str:
    return "```\n" + text.replace("```", "'''") + "\n```"


async def _owned_by(ctx: TypedContext) -> bool:
    is_owner = getattr(ctx, "is_owner", None)
    if is_owner is None or ctx.author is None:
        return False
    try:
        return bool(await is_owner(ctx.author))
    except Exception as e:
        print(f"[!python] Could not check who owns the bot: {type(e).__name__}: {e}")
        return False


async def _update(ctx: TypedContext, message: Any, text: str) -> None:
    """Edits the status message, or posts `text` when it can't be edited."""
    edit = getattr(message, "edit", None)
    if edit is not None:
        try:
            await edit(content=text)
            return
        except Exception:
            pass
    await ctx.send(text)


async def post_output(ctx: TypedContext, script: str, text: str, attachments: List[str], root: str) -> None:
    """The output as a code block: whole, or its two ends with the whole of it
    attached. Then the files the script asked to attach."""
    if text and len(text) <= INLINE_CHARS:
        await ctx.reply(_fenced(text))
    elif text:
        hidden = len(text) - 2 * END_CHARS
        await ctx.reply(_fenced(text[:END_CHARS]) + f"\n… {hidden:,} characters not shown: the whole output is attached …\n"
                        + _fenced(text[-END_CHARS:]))
        stem = os.path.splitext(script)[0]
        await ctx.send_file(text.encode("utf-8"), f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.txt")
    for path in attachments:
        full, why_not = attachment(path, root)
        if full is None:
            await ctx.send(f"Not attached: `{_shown(path)}`: {why_not}.")
            continue
        with open(full, "rb") as f:
            await ctx.send_file(f.read(), os.path.basename(full))


class PythonCommand(ChannelCommand):
    name = "python"
    summary = "Runs a script from `scripts/` here and posts its output (owner only)."
    usage = "[--yes] [--timeout <seconds>] <script.py> [args…]"

    async def run(self, ctx: TypedContext, args: str) -> None:
        global _running
        if not await _owned_by(ctx):
            await ctx.reply("Only the bot's owner can run scripts.")
            return
        scripts = getattr(ctx, "scripts", None)
        if scripts is None:
            await ctx.reply("Scripts can't be run from here.")
            return
        try:
            invocation = parse(args)
            path = scripts.path(invocation.script)
        except ValueError as e:
            await ctx.reply(str(e))
            return
        script, label = _shown(invocation.script), _shown(invocation.label)
        if not os.path.isfile(path):
            await ctx.reply(f"There's no `scripts/{script}`.")
            return
        why = scripts.writes(path, invocation.args)
        if why and not invocation.confirmed:
            await ctx.reply(f"⚠️ `{label}` may change things: {_shown(why)}. "
                            f"Nothing ran. To run it: `!python --yes {label}`")
            return
        if not _lock.acquire(blocking=False):
            await ctx.reply(f"⏳ `{_running or 'Another script'}` is still running. One at a time: try again when it's done.")
            return
        try:
            _running = label
            print(f"[!python] {getattr(ctx.author, 'name', ctx.author)} runs: {invocation.label}")
            status = await ctx.send(f"⏳ Running `{label}`…")
            try:
                run = await scripts.run(invocation.script, invocation.args, invocation.timeout)
            except Exception as e:
                await _update(ctx, status, f"❌ `{label}` couldn't be started: {type(e).__name__}: {e}")
                return
            text, attachments = split_attachments(run.stdout)
            body = output_text(text, run.stderr)
            await _update(ctx, status, status_line(run) + ("" if body or attachments else " It printed nothing."))
            await post_output(ctx, invocation.script, body, attachments, scripts.root)
        finally:
            _running = None
            _lock.release()
