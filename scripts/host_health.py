#!/usr/bin/env python3
"""How the NAS is doing, and how fast its disk is for the bot's database.
From Discord:

    !python host_health.py
    !python host_health.py --cold      the first read of memory.db comes from the disk

The host, over two seconds: how busy the CPU and each disk were, load,
memory and swap, and whether the container is held back (cgroup). The bot
over the same two seconds: its CPU, threads and memory, and whether it has
memory.db open at that moment.

memory.db, the file every turn reads and writes:
- its size and its WAL's, the filesystem it's on, how much of it the page
  cache holds, and how many pieces (extents) it's stored in;
- opening it and loading its schema, which every connection does;
- a write the way the bot writes, through SessionStore, into a scratch
  database beside it. Twice: as the only connection, which on closing
  checkpoints the WAL into the database and syncs it, and with another
  connection open, which doesn't. The bot opens a connection per read or
  write and keeps none open, so its writes are mostly the first kind;
- a bare fsync of a small file there;
- reading it end to end, twice: as cached as it is now (--cold drops its
  cached pages first, so the read comes from the disk), then warm.

And Python itself: a fresh interpreter importing the storage code, and a
fixed CPU workload, to compare with another machine.

It changes nothing: the scratch files are its own, in a directory under
sessions/ that it deletes. With --cold, the bot's next reads of memory.db
come from the disk too, until the cache fills again.
"""
import argparse
import json
import os
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.config.settings import Config  # noqa: E402
from core.storage.sessions import SessionStore  # noqa: E402
from profile_bot import find_bot  # noqa: E402

# `!python` runs this without `--yes` (core/runtime/commands/typed/python.py):
# it writes only its own scratch files, in a directory it deletes.
READ_ONLY = True

PROC = "/proc"
CGROUP = "/sys/fs/cgroup"
WINDOW_S = 2.0   # how long the host and the bot are watched
WRITES = 20      # timed writes, each way
FSYNCS = 20
OPENS = 10
UNLIMITED = 1 << 60  # cgroup v1 writes "no limit" as a number about this big
FS_IOC_FIEMAP = 0xC020660B
_FIEMAP = struct.Struct("=QQLLLL")  # struct fiemap: start, length, flags, mapped extents, extent count, reserved


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _ms(seconds: float) -> str:
    if seconds < 0.01:
        return f"{seconds * 1000:.1f} ms"
    return f"{seconds * 1000:.0f} ms" if seconds < 10 else f"{seconds:.1f} s"


def _spread(seconds: Sequence[float]) -> str:
    ordered = sorted(seconds)
    p90 = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
    return f"p50 {_ms(statistics.median(ordered))}, p90 {_ms(p90)}"


# --------------------------------------------------------------------------
# What /proc and the cgroup say
# --------------------------------------------------------------------------

def cpu_model(cpuinfo: str) -> Tuple[str, int, Optional[float]]:
    """(model, logical CPUs, their mean current MHz) from /proc/cpuinfo."""
    lines = cpuinfo.splitlines()
    models = [line.split(":", 1)[1].strip() for line in lines if line.startswith("model name")]
    mhz = [float(line.split(":", 1)[1]) for line in lines if line.startswith("cpu MHz")]
    cpus = sum(1 for line in lines if line.startswith("processor"))
    return (models[0] if models else "?"), cpus, (sum(mhz) / len(mhz) if mhz else None)


def meminfo(text: str) -> Dict[str, int]:
    """A /proc/meminfo or /proc/<pid>/status, its sizes in bytes."""
    values = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name] = int(parts[0]) * (1024 if parts[1:2] == ["kB"] else 1)
    return values


def cpu_times(stat: str) -> Tuple[int, int, int]:
    """(total, idle, iowait) jiffies of all CPUs, from /proc/stat."""
    fields = [int(value) for value in stat.splitlines()[0].split()[1:9]]
    return sum(fields), fields[3], fields[4]


def disk_ticks(diskstats: str) -> Dict[str, int]:
    """Milliseconds each block device has spent doing I/O, from /proc/diskstats."""
    ticks = {}
    for line in diskstats.splitlines():
        parts = line.split()
        if len(parts) >= 13 and not parts[2].startswith(("loop", "ram", "zram", "nbd")):
            ticks[parts[2]] = int(parts[12])
    return ticks


def process_cpu_ticks(stat: str) -> int:
    """utime + stime of a /proc/<pid>/stat."""
    fields = stat[stat.rindex(")") + 2:].split()
    return int(fields[11]) + int(fields[12])


def filesystem(path: str, mounts: str) -> Optional[str]:
    """The type of the filesystem `path` is on, from /proc/mounts."""
    real = os.path.realpath(path)
    best, kind = "", None
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        point = parts[1].replace("\\040", " ")
        if (real == point or real.startswith(point.rstrip("/") + "/")) and len(point) >= len(best):
            best, kind = point, parts[2]
    return kind


def open_files(pid: int, prefix: str, proc: str = PROC) -> Optional[int]:
    """How many of the process's descriptors are on paths starting with
    `prefix`; None when they can't be read."""
    fd_dir = os.path.join(proc, str(pid), "fd")
    try:
        names = os.listdir(fd_dir)
    except OSError:
        return None
    count = 0
    for name in names:
        try:
            count += os.readlink(os.path.join(fd_dir, name)).startswith(prefix)
        except OSError:
            pass
    return count


def _keyvals(text: Optional[str]) -> Dict[str, int]:
    values = {}
    for line in (text or "").splitlines():
        key, _, value = line.partition(" ")
        if value.strip().lstrip("-").isdigit():
            values[key] = int(value)
    return values


def _number(text: Optional[str], default: int) -> int:
    try:
        return int((text or "").strip())
    except ValueError:
        return default


def cgroup_line(root: str = CGROUP) -> Optional[str]:
    """The container's CPU limit and throttling, and its memory limit and
    use, from cgroup v2 or v1. None where there's no cgroup to read."""
    v2 = _read(os.path.join(root, "cpu.max"))
    if v2 is not None:
        quota, period = (v2.split() + ["100000"])[:2]
        cores = None if quota == "max" else int(quota) / int(period)
        stat = _keyvals(_read(os.path.join(root, "cpu.stat")))
        throttled, throttled_s = stat.get("nr_throttled", 0), stat.get("throttled_usec", 0) / 1e6
        weight = (_read(os.path.join(root, "cpu.weight")) or "").strip()
        share = f"weight {weight}" if weight else ""
        limit_text = (_read(os.path.join(root, "memory.max")) or "max").strip()
        memory_limit = None if limit_text == "max" else _number(limit_text, 0)
        memory_used = _number(_read(os.path.join(root, "memory.current")), 0)
    elif os.path.isdir(os.path.join(root, "cpu")):
        cpu, memory = os.path.join(root, "cpu"), os.path.join(root, "memory")
        quota = _number(_read(os.path.join(cpu, "cpu.cfs_quota_us")), -1)
        period = _number(_read(os.path.join(cpu, "cpu.cfs_period_us")), 100000)
        cores = None if quota < 0 else quota / period
        stat = _keyvals(_read(os.path.join(cpu, "cpu.stat")))
        throttled, throttled_s = stat.get("nr_throttled", 0), stat.get("throttled_time", 0) / 1e9
        shares = (_read(os.path.join(cpu, "cpu.shares")) or "").strip()
        share = f"shares {shares}" if shares else ""
        memory_limit = _number(_read(os.path.join(memory, "memory.limit_in_bytes")), UNLIMITED)
        memory_limit = None if memory_limit >= UNLIMITED else memory_limit
        memory_used = _number(_read(os.path.join(memory, "memory.usage_in_bytes")), 0)
    else:
        return None
    cpu_text = "no CPU limit" if cores is None else f"CPU limited to {cores:.2f} cores"
    if share:
        cpu_text += f" ({share})"
    throttling = f"throttled {throttled:,} times" + (f" ({throttled_s:.1f} s)" if throttled else "")
    memory_text = "no memory limit" if memory_limit is None else f"memory limited to {_size(memory_limit)}"
    return f"Container: {cpu_text}, {throttling}; {memory_text}, {_size(memory_used)} used"


def sample_window(bot: Optional[int], seconds: Optional[float] = None, proc: str = PROC,
                  sleep: Callable[[float], None] = time.sleep,
                  clock: Callable[[], float] = time.monotonic) -> Dict[str, object]:
    """How busy the CPUs and each disk were, host-wide, and the bot's CPU,
    over `seconds` (WINDOW_S)."""
    seconds = WINDOW_S if seconds is None else seconds

    def snapshot():
        stat = _read(os.path.join(proc, "stat"))
        disks = _read(os.path.join(proc, "diskstats"))
        bot_stat = _read(os.path.join(proc, str(bot), "stat")) if bot else None
        return (cpu_times(stat) if stat else None, disk_ticks(disks) if disks else {},
                process_cpu_ticks(bot_stat) if bot_stat else None, clock())

    before = snapshot()
    sleep(seconds)
    after = snapshot()
    elapsed = max(after[3] - before[3], 1e-6)
    window: Dict[str, object] = {}
    if before[0] and after[0]:
        total, idle, iowait = (b - a for a, b in zip(before[0], after[0]))
        if total > 0:
            window["cpu_busy"] = (total - idle - iowait) / total
            window["iowait"] = iowait / total
    if before[1] and after[1]:
        busy = {name: (after[1][name] - ticks) / (elapsed * 1000) for name, ticks in before[1].items() if name in after[1]}
        window["disks"] = sorted(((name, b) for name, b in busy.items() if b >= 0.01), key=lambda item: -item[1])[:5]
    if before[2] is not None and after[2] is not None:
        window["bot_cpu"] = (after[2] - before[2]) / os.sysconf("SC_CLK_TCK") / elapsed
    return window


# --------------------------------------------------------------------------
# The file and its disk
# --------------------------------------------------------------------------

def cached_fraction(path: str) -> Optional[float]:
    """How much of the file the page cache holds now (mincore), or None where
    that can't be asked. Linux only: elsewhere mincore describes the mapping,
    not the file's cached pages."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        import ctypes
        import ctypes.util
        import mmap
        size = os.path.getsize(path)
        if not size:
            return None
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        pages = (size + mmap.PAGESIZE - 1) // mmap.PAGESIZE
        resident = (ctypes.c_ubyte * pages)()
        with open(path, "rb") as f:
            mapped = mmap.mmap(f.fileno(), size, access=mmap.ACCESS_COPY)  # private: nothing reaches the file
            try:
                view = (ctypes.c_char * size).from_buffer(mapped)
                try:
                    failed = libc.mincore(ctypes.c_void_p(ctypes.addressof(view)), ctypes.c_size_t(size), resident)
                finally:
                    del view
            finally:
                mapped.close()
        return None if failed else sum(page & 1 for page in resident) / pages
    except Exception:
        return None


def extent_count(path: str) -> Optional[int]:
    """How many extents the file is stored in (FIEMAP), or None where the
    filesystem can't say."""
    try:
        import fcntl
        request = bytearray(_FIEMAP.pack(0, 0xFFFFFFFFFFFFFFFF, 0, 0, 0, 0))  # no extent array: only count them
        with open(path, "rb") as f:
            fcntl.ioctl(f.fileno(), FS_IOC_FIEMAP, request)
        return _FIEMAP.unpack(bytes(request))[3]
    except (ImportError, OSError, OverflowError, ValueError):
        return None


def open_times(db: str, n: Optional[int] = None) -> Tuple[List[float], int]:
    """Seconds to open the database read-only and load its schema, each of
    `n` (OPENS) times, and how many objects the schema has."""
    store = SessionStore(db)
    took, objects = [], 0
    for _ in range(n or OPENS):
        started = time.perf_counter()
        with store.read_only() as conn:
            objects = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        took.append(time.perf_counter() - started)
    return took, objects


def write_cycles(directory: str, n: Optional[int] = None) -> Tuple[List[float], List[float]]:
    """Seconds per SessionStore write into a scratch database in `directory`,
    `n` (WRITES) of each: as the only connection, whose close checkpoints the
    WAL and syncs, then with another connection open, whose close doesn't."""
    n = n or WRITES
    store = SessionStore(os.path.join(directory, "scratch.db"))
    store.append_message("host_health", "user", "warm-up")  # makes the file and its table

    def timed() -> List[float]:
        took = []
        for i in range(n):
            started = time.perf_counter()
            store.append_message("host_health", "user", f"probe {i} " + "x" * 200)
            took.append(time.perf_counter() - started)
        return took

    alone = timed()
    with store.connect() as holder:
        holder.execute("SELECT count(*) FROM sqlite_master").fetchall()  # now it holds the WAL open
        shared = timed()
    return alone, shared


def fsync_times(directory: str, n: Optional[int] = None) -> List[float]:
    """Seconds per fsync of a 4 KB write to a scratch file in `directory`."""
    took = []
    with open(os.path.join(directory, "fsync.probe"), "wb") as f:
        for _ in range(n or FSYNCS):
            f.write(os.urandom(4096))
            f.flush()
            started = time.perf_counter()
            os.fsync(f.fileno())
            took.append(time.perf_counter() - started)
    return took


def read_speed(path: str, cold: bool = False) -> Tuple[float, float]:
    """(seconds, MB/s) to read the file end to end. `cold` asks the kernel to
    drop its cached pages first."""
    size = os.path.getsize(path)
    with open(path, "rb", buffering=0) as f:
        if cold and hasattr(os, "posix_fadvise"):
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        started = time.perf_counter()
        while f.read(1 << 20):
            pass
        took = time.perf_counter() - started
    return took, (size / 1e6 / took if took > 0 else float("inf"))


# --------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------

def import_seconds() -> Optional[float]:
    started = time.perf_counter()
    try:
        subprocess.run([sys.executable, "-c", "import core.storage.sessions"], cwd=PROJECT_ROOT, check=True,
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return time.perf_counter() - started


def cpu_workload_seconds() -> float:
    """A fixed pure-Python workload: JSON round trips of a chat history, and arithmetic."""
    history = [{"role": "user" if i % 2 else "assistant", "content": "word " * 60, "n": i} for i in range(400)]
    started = time.perf_counter()
    for _ in range(100):
        json.loads(json.dumps(history))
    total = 0
    for i in range(2_000_000):
        total += i * i % 7
    return time.perf_counter() - started


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

def host_lines(window: Dict[str, object], proc: str = PROC) -> List[str]:
    lines = []
    cpuinfo = _read(os.path.join(proc, "cpuinfo"))
    if cpuinfo:
        model, cpus, mhz = cpu_model(cpuinfo)
        lines.append(f"CPU: {model}, {cpus} logical CPUs" + (f", running at {mhz:.0f} MHz" if mhz else ""))
    loadavg = _read(os.path.join(proc, "loadavg"))
    if loadavg:
        lines.append(f"Load: {' / '.join(loadavg.split()[:3])} (1 / 5 / 15 min)")
    memory = meminfo(_read(os.path.join(proc, "meminfo")) or "")
    if memory:
        swap_used = memory.get("SwapTotal", 0) - memory.get("SwapFree", 0)
        lines.append(f"Memory: {_size(memory.get('MemAvailable', 0))} available of {_size(memory.get('MemTotal', 0))}, "
                     f"page cache {_size(memory.get('Cached', 0))}, swap {_size(swap_used)} used "
                     f"of {_size(memory.get('SwapTotal', 0))}")
    if "cpu_busy" in window:
        lines.append(f"Over {WINDOW_S:g} s: CPU {window['cpu_busy']:.0%} busy, {window['iowait']:.0%} waiting on disks")
    if "disks" in window:
        disks = window["disks"]
        lines.append("Disks busy: " + (", ".join(f"{name} {min(busy, 1.0):.0%}" for name, busy in disks)
                                       if disks else "none over 1%"))
    container = cgroup_line()
    if container:
        lines.append(container)
    return lines or ["Nothing to read here: no /proc (not Linux)."]


def bot_lines(bot: Optional[int], why: str, window: Dict[str, object], db: str, proc: str = PROC) -> List[str]:
    if bot is None:
        return [why]
    status = meminfo(_read(os.path.join(proc, str(bot), "status")) or "")
    facts = []
    if "bot_cpu" in window:
        facts.append(f"CPU {window['bot_cpu']:.0%} of one core over {WINDOW_S:g} s")
    if "Threads" in status:
        facts.append(f"{status['Threads']} threads")
    if "VmRSS" in status:
        facts.append(f"{_size(status['VmRSS'])} resident")
    lines = [", ".join(facts)] if facts else []
    holding = open_files(bot, os.path.realpath(db), proc)
    if holding is not None:
        lines.append(f"Has memory.db open right now: {f'yes, {holding} descriptors' if holding else 'no'}")
    return lines or ["Nothing to read about it here: no /proc (not Linux)."]


def file_lines(db: str, proc: str = PROC) -> List[str]:
    if not os.path.isfile(db):
        return [f"There's no {db}."]
    directory = os.path.dirname(db)
    size = os.path.getsize(db)
    sizes = [_size(size)] + [f"{suffix} {_size(os.path.getsize(db + suffix))}"
                             for suffix in ("-wal", "-shm") if os.path.exists(db + suffix)]
    lines = ["Size: " + ", ".join(sizes)]
    free = os.statvfs(directory)
    kind = filesystem(directory, _read(os.path.join(proc, "mounts")) or "")
    lines.append(f"Filesystem: {kind}, {_size(free.f_bavail * free.f_frsize)} free" if kind
                 else f"Free space: {_size(free.f_bavail * free.f_frsize)}")
    others = sorted(name for name in os.listdir(directory) if name.endswith(".db") and name != os.path.basename(db))
    if others:
        lines.append("Other databases beside it: " + ", ".join(
            f"{name} {_size(os.path.getsize(os.path.join(directory, name)))}" for name in others))
    cached = cached_fraction(db)
    if cached is not None:
        lines.append(f"In the page cache now: {cached:.0%}")
    extents = extent_count(db)
    if extents:
        lines.append(f"Stored in {extents:,} extents, {_size(size / extents)} each on average")
    return lines


def timing_lines(db: str, cold: bool) -> List[str]:
    lines = []
    exists = os.path.isfile(db)
    if exists:
        took, objects = open_times(db)
        lines.append(f"Open memory.db and load its schema ({objects:,} objects): {_spread(took)}")
    directory = os.path.dirname(db)
    if os.path.isdir(directory):
        with tempfile.TemporaryDirectory(prefix=".host_health-", dir=directory) as scratch:
            alone, shared = write_cycles(scratch)
            lines.append(f"A write like the bot's, as the only connection (its close checkpoints and syncs): "
                         f"{_spread(alone)}")
            lines.append(f"The same with another connection open (no checkpoint on close): {_spread(shared)}")
            lines.append(f"fsync of a small file: {_spread(fsync_times(scratch))}")
    if exists:
        first_s, first_rate = read_speed(db, cold)
        again_s, again_rate = read_speed(db)
        if cold and hasattr(os, "posix_fadvise"):
            how = "from the disk (--cold)"
        else:
            how = "as cached now" + (" (--cold needs Linux)" if cold else "")
        lines.append(f"Read memory.db end to end, {how}: {_ms(first_s)} ({first_rate:,.0f} MB/s); "
                     f"again: {_ms(again_s)} ({again_rate:,.0f} MB/s)")
    return lines


def python_lines() -> List[str]:
    lines = []
    seconds = import_seconds()
    if seconds is not None:
        lines.append(f"A fresh interpreter importing core.storage.sessions: {_ms(seconds)}")
    lines.append(f"A fixed CPU workload (JSON and arithmetic): {_ms(cpu_workload_seconds())}")
    return lines


def _guarded(section: Callable[[], List[str]]) -> List[str]:
    """A section's lines; one that fails reports it rather than ending the report."""
    try:
        return section()
    except Exception as e:
        return [f"failed: {type(e).__name__}: {e}"]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="host_health.py",
                                     description="How the NAS is doing, and how fast its disk is for memory.db.")
    parser.add_argument("--cold", action="store_true",
                        help="drop memory.db's cached pages before the first read, so it comes from the disk "
                             "(the bot's next reads of it do too)")
    args = parser.parse_args(argv)

    db = Config().memory_db_path
    bot, why = find_bot()
    print(f"Host health at {time.strftime('%H:%M:%S %Z on %Y-%m-%d')}")
    try:
        window = sample_window(bot)
    except Exception as e:
        window = {}
        print(f"(watching the host failed: {type(e).__name__}: {e})")
    sections = (
        ("Host", lambda: host_lines(window)),
        (f"The bot (PID {bot})" if bot else "The bot", lambda: bot_lines(bot, why, window, db)),
        (f"memory.db ({db})", lambda: file_lines(db)),
        ("Timings on its disk", lambda: timing_lines(db, args.cold)),
        ("Python", python_lines),
    )
    for title, section in sections:
        print(f"\n{title}")
        for line in _guarded(section):
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
