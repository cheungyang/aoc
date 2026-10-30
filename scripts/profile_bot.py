#!/usr/bin/env python3
"""Profiles the running bot with py-spy, from Discord:

    !python profile_bot.py                     every thread's stack, right now
    !python profile_bot.py record              60 s of samples, as a file
    !python profile_bot.py record --duration 120 --format flamegraph

To see where a slow voice turn goes, start a recording, then talk to the bot.

`dump` prints what each of the bot's Python threads is doing at this instant.
`record` samples them for `--duration` seconds into sessions/profiles/ (the
newest KEEP_PROFILES are kept) and prints an `ATTACH:` line, so `!python`
attaches the file. The default format, speedscope, keeps every thread's
samples in order: at https://www.speedscope.app, "Time Order" replays the
recording as it happened. `--format flamegraph` is an SVG for any browser.

A recording is wall-clock time: every thread, waiting ones included, since a
stall is as likely a wait -- on a lock, the network, the disk -- as work.
(`--gil` keeps only the thread running Python, which hides exactly that:
SQLite lets go of the GIL while it waits on the disk.) The bot isn't paused
to take a sample, so the profile doesn't slow the turn it measures;
`--native` adds the C frames, but pauses the bot at every sample.

The bot is this script's parent when `!python` runs it. From a shell in the
container, the process running main.py is found instead; `--pid` overrides
both.

py-spy reads another process's memory, which a container can't do by
default. The Dockerfile gives the py-spy binary the SYS_PTRACE capability:
the bot runs as appuser, and attaching to a parent process takes it. That
works only in a container docker-compose.yml grants SYS_PTRACE (`cap_add`).
When either is missing, this says so, and how to fix it.
"""
import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
import time
from typing import List, Optional, Sequence, Tuple

# `!python` runs this without `--yes` (core/runtime/commands/typed/python.py):
# it reads the bot's stacks, and writes nothing but its own profiles.
READ_ONLY = True

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_DIR = os.path.join(PROJECT_ROOT, "sessions", "profiles")
KEEP_PROFILES = 20
MAX_DURATION_S = 600
GRACE_S = 60          # beyond the duration, for py-spy to attach and write the file
DUMP_TIMEOUT_S = 60
MAX_ATTACH_BYTES = 8 * 1024 * 1024  # the largest file `!python` attaches

EXTENSIONS = {
    "speedscope": ".speedscope.json",
    "flamegraph": ".svg",
    "raw": ".txt",
    "chrometrace": ".trace.json",
}
VIEWERS = {
    "speedscope": 'Open it at https://www.speedscope.app: "Time Order" replays the recording.',
    "flamegraph": "Open it in a browser.",
    "raw": "One line per distinct stack, ending in its sample count.",
    "chrometrace": "Open it at https://ui.perfetto.dev.",
}

PROC_STATUS = "/proc/self/status"
PTRACE_SCOPE = "/proc/sys/kernel/yama/ptrace_scope"
CAP_SYS_PTRACE = 19  # its bit in the capability masks of /proc/*/status
REBUILD = ("On the NAS, in the project directory: `docker compose up -d --build` "
           "(or rebuild the project in Container Manager).")


# --------------------------------------------------------------------------
# Which process is the bot
# --------------------------------------------------------------------------

def _cmdline(pid: int) -> Optional[List[str]]:
    """The process's arguments, or None where /proc can't say."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except OSError:
        return None
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _pids() -> List[int]:
    try:
        return [int(name) for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return []


def _has_proc() -> bool:
    return os.path.isdir("/proc")


def is_bot(argv: Optional[Sequence[str]]) -> bool:
    """A Python interpreter running main.py."""
    return (bool(argv) and "python" in os.path.basename(argv[0])
            and any(os.path.basename(arg) == "main.py" for arg in argv[1:]))


def find_bot() -> Tuple[Optional[int], str]:
    """The bot's PID: this script's parent, which is the bot when `!python`
    runs it; else the one process running main.py. (None, why) when there's
    no telling."""
    parent = os.getppid()
    if not _has_proc():  # nothing to check it against (macOS)
        return parent, ""
    if is_bot(_cmdline(parent)):
        return parent, ""
    bots = [pid for pid in _pids() if pid != os.getpid() and is_bot(_cmdline(pid))]
    if len(bots) == 1:
        return bots[0], ""
    found = f"{len(bots)} processes are ({', '.join(map(str, bots))})" if bots else "no process is"
    return None, (f"Which process is the bot? This script's parent ({parent}: "
                  f"{' '.join(_cmdline(parent) or ['?'])}) isn't running main.py, and {found}.")


def bot_pid(explicit: Optional[int] = None) -> int:
    """`explicit`, else `find_bot()`'s. SystemExit, saying why, when there's no telling."""
    if explicit:
        return explicit
    pid, why = find_bot()
    if pid is None:
        raise SystemExit(f"{why} Give it with --pid.")
    return pid


# --------------------------------------------------------------------------
# Whether py-spy can attach here
# --------------------------------------------------------------------------

def find_py_spy() -> Optional[str]:
    """py-spy beside this interpreter, where pip puts it, else on PATH."""
    beside = os.path.join(os.path.dirname(sys.executable), "py-spy")
    if os.path.isfile(beside) and os.access(beside, os.X_OK):
        return beside
    return shutil.which("py-spy")


def _capabilities(name: str) -> Optional[int]:
    """This process's capability set `name` from /proc (CapBnd: the most the
    container allows), or None off Linux."""
    try:
        with open(PROC_STATUS, encoding="utf-8") as f:
            for line in f:
                if line.startswith(name + ":"):
                    return int(line.split(":", 1)[1], 16)
    except (OSError, ValueError):
        pass
    return None


def ptrace_scope() -> Optional[int]:
    """kernel.yama.ptrace_scope. 0: any process of the same user may attach;
    1: only to its descendants; 2: only with SYS_PTRACE; 3: never. None: the
    kernel has no Yama, which is 0."""
    try:
        with open(PTRACE_SCOPE, encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def has_file_capability(path: str) -> Optional[bool]:
    """Whether the binary carries file capabilities (setcap), or None where
    that can't be read (off Linux)."""
    getxattr = getattr(os, "getxattr", None)
    if getxattr is None:
        return None
    try:
        getxattr(path, "security.capability")
        return True
    except OSError:
        return False


def _ptrace_granted(mask: int) -> bool:
    return bool(mask >> CAP_SYS_PTRACE & 1)


def blockers(py_spy: Optional[str]) -> List[str]:
    """What will stop py-spy attaching to the bot here, each with its fix.
    Empty: go ahead."""
    if not py_spy:
        return [f"py-spy isn't installed: the image predates it. {REBUILD}"]
    problems = []
    allowed = _capabilities("CapBnd")
    if allowed is not None and not _ptrace_granted(allowed):
        problems.append("The container doesn't have SYS_PTRACE: it was created before "
                        f"docker-compose.yml's `cap_add`. {REBUILD}")
    scope = ptrace_scope()
    if scope == 3:
        problems.append("This host lets no process attach to another (kernel.yama.ptrace_scope = 3).")
    elif scope in (1, 2) and os.geteuid() != 0 and has_file_capability(py_spy) is False:
        problems.append("py-spy lacks its SYS_PTRACE file capability, which this host requires to attach "
                        f"to the bot (kernel.yama.ptrace_scope = {scope}). Either the image predates it -- "
                        f"{REBUILD} -- or the build's setcap failed, and its log says why.")
    return problems


def environment(py_spy: str) -> str:
    """What decides whether py-spy may attach, for a failure report."""
    facts = ["running as root" if os.geteuid() == 0 else f"running as uid {os.geteuid()}"]
    allowed = _capabilities("CapBnd")
    if allowed is not None:
        facts.append(f"container SYS_PTRACE: {'yes' if _ptrace_granted(allowed) else 'no'}")
    marked = has_file_capability(py_spy)
    if marked is not None:
        facts.append(f"py-spy file capability: {'yes' if marked else 'no'}")
    scope = ptrace_scope()
    facts.append(f"ptrace_scope: {scope if scope is not None else 'none (no Yama)'}")
    return ", ".join(facts)


# --------------------------------------------------------------------------
# py-spy
# --------------------------------------------------------------------------

def dump_command(py_spy: str, pid: int, native: bool = False) -> List[str]:
    return [py_spy, "dump", "--pid", str(pid)] + (["--native"] if native else [])


def record_command(py_spy: str, pid: int, output: str, fmt: str, duration: int, rate: int,
                   gil: bool = False, native: bool = False) -> List[str]:
    """Every thread, waiting ones included (with `gil`, only the one running
    Python), without pausing the bot (unless `native`, which must)."""
    cmd = [py_spy, "record", "--pid", str(pid), "--output", output, "--format", fmt,
           "--duration", str(duration), "--rate", str(rate),
           "--gil" if gil else "--idle",
           "--native" if native else "--nonblocking",
           # Plain lines, not a progress bar: when sampling falls behind,
           # the bar moves the cursor, which in a pipe is just noise.
           "--hideprogress"]
    if fmt in ("flamegraph", "raw"):
        cmd.append("--threads")  # a root per thread; the other formats keep threads apart already
    return cmd


def _run(cmd: List[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _failed(what: str, py_spy: str, output: str = "") -> int:
    """Reports a failed py-spy run with what may explain it. Returns the exit status."""
    print(what)
    if output.strip():
        print(output.strip())
    print(f"\nWhat decides attaching here: {environment(py_spy)}.")
    return 1


def _process(pid: int) -> str:
    argv = _cmdline(pid)
    return f"PID {pid}: {' '.join(argv)}" if argv else f"PID {pid}"


def dump(py_spy: str, pid: int, native: bool = False) -> int:
    try:
        result = _run(dump_command(py_spy, pid, native), DUMP_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _failed(f"py-spy couldn't dump PID {pid}: {e}", py_spy)
    if result.returncode != 0:
        return _failed(f"py-spy couldn't dump PID {pid} (exit {result.returncode}):", py_spy,
                       result.stderr + result.stdout)
    print(f"The bot's threads at {time.strftime('%H:%M:%S %Z')}:\n")
    print(result.stdout.rstrip())
    return 0


def attachable(path: str) -> Optional[str]:
    """The file to attach: `path`, gzipped in its place when it's too big for
    Discord; None when even that is."""
    if os.path.getsize(path) <= MAX_ATTACH_BYTES:
        return path
    packed = path + ".gz"
    with open(path, "rb") as src, gzip.open(packed, "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.remove(path)
    return packed if os.path.getsize(packed) <= MAX_ATTACH_BYTES else None


def prune(directory: str, keep: int) -> None:
    """Deletes all but the `keep` newest files in `directory`."""
    try:
        paths = [os.path.join(directory, name) for name in os.listdir(directory)]
    except OSError:
        return
    files = sorted((p for p in paths if os.path.isfile(p)), key=os.path.getmtime, reverse=True)
    for path in files[keep:]:
        try:
            os.remove(path)
        except OSError:
            pass


def record(py_spy: str, pid: int, fmt: str = "speedscope", duration: int = 60, rate: int = 50,
           gil: bool = False, native: bool = False) -> int:
    os.makedirs(PROFILES_DIR, exist_ok=True)
    started = time.time()
    path = os.path.join(PROFILES_DIR, time.strftime("%Y%m%d-%H%M%S", time.localtime(started)) + EXTENSIONS[fmt])
    try:
        result = _run(record_command(py_spy, pid, path, fmt, duration, rate, gil, native), duration + GRACE_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _failed(f"py-spy couldn't record PID {pid}: {e}", py_spy)
    if result.returncode != 0 or not os.path.isfile(path):
        return _failed(f"py-spy couldn't record PID {pid} (exit {result.returncode}):", py_spy,
                       result.stderr + result.stdout)

    threads = "only the thread holding the GIL" if gil else "every thread, waiting ones included"
    pausing = "pausing it at each sample, for the C frames" if native else "without pausing it"
    print(f"Recorded the bot ({_process(pid)}) from {time.strftime('%H:%M:%S', time.localtime(started))} "
          f"to {time.strftime('%H:%M:%S %Z on %Y-%m-%d')}, {rate} samples/s: {threads}, {pausing}.")
    output = result.stdout + result.stderr
    counts = re.search(r"Samples: (\d+) Errors: (\d+)", output)
    if counts:
        print(f"py-spy: {int(counts.group(1)):,} samples, {int(counts.group(2)):,} errors.")
    if "behind in sampling" in output:
        print("py-spy fell behind at times, so the timeline is less exact: a lower --rate helps.")
    print(VIEWERS[fmt])

    attach = attachable(path)
    prune(PROFILES_DIR, KEEP_PROFILES)
    if attach is None:
        kept = os.path.relpath(path + ".gz", PROJECT_ROOT)
        print(f"Even gzipped, it's over {MAX_ATTACH_BYTES // (1024 * 1024)} MB, too big for Discord: it stays "
              f"at {kept} on the NAS. Record for less time, or at a lower --rate.")
        return 0
    if attach != path:
        print("It's gzipped to fit in Discord: unzip it before opening it.")
    print(f"ATTACH: {os.path.relpath(attach, PROJECT_ROOT)}")
    return 0


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def _within(low: int, high: int):
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a whole number: {text}") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be {low} to {high}")
        return value
    return parse


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="profile_bot.py", description="Profile the running bot with py-spy.")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--pid", type=int, help="the process to profile (default: the bot)")
    shared.add_argument("--native", action="store_true",
                        help="include C frames; pauses the bot at each sample")
    modes = parser.add_subparsers(dest="mode", metavar="{dump,record}")
    modes.add_parser("dump", parents=[shared], help="every thread's stack, right now (the default)")
    rec = modes.add_parser("record", parents=[shared], help="sample the bot for a while, into a file")
    rec.add_argument("--duration", type=_within(1, MAX_DURATION_S), default=60, metavar="SECONDS",
                     help=f"how long to record, 1 to {MAX_DURATION_S} (default 60). Past 4 minutes, "
                          "give !python a longer --timeout too")
    rec.add_argument("--rate", type=_within(1, 250), default=50, metavar="PER_SECOND",
                     help="samples per second (default 50)")
    rec.add_argument("--format", choices=sorted(EXTENSIONS), default="speedscope",
                     help="speedscope (default), flamegraph, raw or chrometrace")
    rec.add_argument("--gil", action="store_true",
                     help="only the thread holding the GIL: Python's CPU time, without the waits")

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        argv.insert(0, "dump")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    py_spy = find_py_spy()
    problems = blockers(py_spy)
    if problems:
        print("\n".join(problems))
        return 1
    pid = bot_pid(args.pid)
    if args.mode == "record":
        return record(py_spy, pid, args.format, args.duration, args.rate, args.gil, args.native)
    return dump(py_spy, pid, args.native)


if __name__ == "__main__":
    sys.exit(main())
