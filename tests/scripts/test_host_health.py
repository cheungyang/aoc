"""host_health: the host figures it reads, the disk timings it takes, and its report."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

import host_health as hh  # noqa: E402
from core.scheduler.script_runner import write_reason  # noqa: E402


def test_python_runs_it_without_yes():
    assert write_reason(hh.__file__, ["--cold"]) == ""


# --- what /proc and the cgroup say ----------------------------------------------

CPUINFO = """processor\t: 0
model name\t: Intel(R) Celeron(R) J4125 CPU @ 2.00GHz
cpu MHz\t\t: 1995.000

processor\t: 1
model name\t: Intel(R) Celeron(R) J4125 CPU @ 2.00GHz
cpu MHz\t\t: 2005.000
"""


def test_cpu_model():
    assert hh.cpu_model(CPUINFO) == ("Intel(R) Celeron(R) J4125 CPU @ 2.00GHz", 2, 2000.0)
    assert hh.cpu_model("") == ("?", 0, None)


def test_meminfo_and_status_sizes_are_in_bytes():
    assert hh.meminfo("MemTotal:   1000 kB\nThreads:\t41\nVmRSS:\t   2 kB\nName:\tpython\n") == {
        "MemTotal": 1024000, "Threads": 41, "VmRSS": 2048}


def test_cpu_times_add_up_the_first_eight_fields():
    assert hh.cpu_times("cpu  10 0 5 80 4 1 0 0 7 7\ncpu0 1 2 3 4 5 6 7 8\n") == (100, 80, 4)


def test_disk_ticks_skip_virtual_devices():
    diskstats = ("   8       0 sda 1 2 3 4 5 6 7 8 9 1200 11\n"
                 "   7       0 loop0 1 2 3 4 5 6 7 8 9 50 11\n"
                 "   9       2 md2 1 2 3 4 5 6 7 8 9 900 11\n")
    assert hh.disk_ticks(diskstats) == {"sda": 1200, "md2": 900}


def test_process_cpu_ticks_survive_a_name_with_spaces_and_parentheses():
    assert hh.process_cpu_ticks("7 (python (main) x) S 1 7 7 0 -1 0 0 0 0 0 250 50 0 0") == 300


def test_the_filesystem_is_the_longest_mount_holding_the_path():
    mounts = "overlay / overlay rw 0 0\n/dev/mapper/cachedev_0 /app btrfs rw 0 0\nproc /proc proc rw 0 0\n"
    assert hh.filesystem("/app/sessions", mounts) == "btrfs"
    assert hh.filesystem("/opt/aoc", mounts) == "overlay"
    assert hh.filesystem("/x", "") is None


def test_open_files_counts_the_descriptors_on_the_database(tmp_path):
    fd = tmp_path / "7" / "fd"
    fd.mkdir(parents=True)
    os.symlink("/app/sessions/memory.db", fd / "3")
    os.symlink("/app/sessions/memory.db-wal", fd / "4")
    os.symlink("/dev/null", fd / "5")
    assert hh.open_files(7, "/app/sessions/memory.db", proc=str(tmp_path)) == 2
    assert hh.open_files(8, "/app/sessions/memory.db", proc=str(tmp_path)) is None


def test_cgroup_v2(tmp_path):
    (tmp_path / "cpu.max").write_text("150000 100000\n")
    (tmp_path / "cpu.stat").write_text("usage_usec 5\nnr_throttled 12\nthrottled_usec 3000000\n")
    (tmp_path / "cpu.weight").write_text("100\n")
    (tmp_path / "memory.max").write_text("max\n")
    (tmp_path / "memory.current").write_text(str(512 * 1024 * 1024))
    assert hh.cgroup_line(str(tmp_path)) == ("Container: CPU limited to 1.50 cores (weight 100), throttled 12 times "
                                             "(3.0 s); no memory limit, 512.0 MB used")


def test_cgroup_v1(tmp_path):
    cpu, memory = tmp_path / "cpu", tmp_path / "memory"
    cpu.mkdir()
    memory.mkdir()
    (cpu / "cpu.cfs_quota_us").write_text("-1\n")
    (cpu / "cpu.cfs_period_us").write_text("100000\n")
    (cpu / "cpu.stat").write_text("nr_periods 0\nnr_throttled 0\nthrottled_time 0\n")
    (cpu / "cpu.shares").write_text("1024\n")
    (memory / "memory.limit_in_bytes").write_text(f"{(1 << 63) - 4096}\n")  # what cgroup v1 shows for no limit
    (memory / "memory.usage_in_bytes").write_text(str(2 * 1024 ** 3))
    assert hh.cgroup_line(str(tmp_path)) == ("Container: no CPU limit (shares 1024), throttled 0 times; "
                                             "no memory limit, 2.0 GB used")
    assert hh.cgroup_line(str(tmp_path / "missing")) is None


def test_the_window_measures_cpu_disks_and_the_bot(tmp_path):
    (tmp_path / "7").mkdir()

    def state(user, system, idle, iowait, disk_ms, bot_ticks):
        (tmp_path / "stat").write_text(f"cpu  {user} 0 {system} {idle} {iowait} 0 0 0 0 0\n")
        (tmp_path / "diskstats").write_text(f"   8 0 sda 1 2 3 4 5 6 7 8 9 {disk_ms} 11\n"
                                            f"   9 2 md2 1 2 3 4 5 6 7 8 9 0 11\n")
        (tmp_path / "7" / "stat").write_text(f"7 (python) S 1 7 7 0 -1 0 0 0 0 0 {bot_ticks} 0 0 0\n")

    state(100, 0, 800, 100, 1000, 0)
    clock = iter([0.0, 2.0])
    window = hh.sample_window(7, seconds=2.0, proc=str(tmp_path), clock=clock.__next__,
                              sleep=lambda s: state(150, 50, 850, 150, 2000, os.sysconf("SC_CLK_TCK")))
    assert window["cpu_busy"] == 0.5 and window["iowait"] == 0.25
    assert window["disks"] == [("sda", 0.5)], "md2 did nothing"
    assert window["bot_cpu"] == 0.5


def test_without_proc_the_window_is_empty(tmp_path):
    assert hh.sample_window(None, seconds=0, proc=str(tmp_path / "none")) == {}


# --- timings on the disk ----------------------------------------------------------

def test_writes_are_timed_both_ways_and_the_last_close_checkpoints(tmp_path):
    alone, shared = hh.write_cycles(str(tmp_path), n=3)
    assert len(alone) == len(shared) == 3 and min(alone + shared) > 0
    assert not os.path.exists(tmp_path / "scratch.db-wal"), "closing the last connection folded the WAL in"
    conn = sqlite3.connect(tmp_path / "scratch.db")
    assert conn.execute("SELECT count(*) FROM ctx_host_health").fetchone()[0] == 7, "the warm-up and 2 x 3 writes"
    conn.close()


def test_fsyncs_and_opens_are_timed(tmp_path):
    assert len(hh.fsync_times(str(tmp_path), n=3)) == 3
    db = tmp_path / "x.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE a (x); CREATE TABLE b (x); CREATE INDEX b_x ON b (x);")
    conn.close()
    took, objects = hh.open_times(str(db), n=2)
    assert len(took) == 2 and objects == 3


def test_reads_are_timed(tmp_path):
    path = tmp_path / "data"
    path.write_bytes(os.urandom(3 * 1024 * 1024))
    seconds, rate = hh.read_speed(str(path), cold=True)
    assert seconds > 0 and rate > 0


def test_cache_and_extent_figures_are_numbers_or_absent(tmp_path):
    path = tmp_path / "data"
    path.write_bytes(os.urandom(64 * 1024))
    cached = hh.cached_fraction(str(path))
    assert cached is None or 0 <= cached <= 1
    extents = hh.extent_count(str(path))
    assert extents is None or extents >= 0


# --- the report -------------------------------------------------------------------

def test_the_report(tmp_path, monkeypatch, capsys):
    db = tmp_path / "sessions" / "memory.db"  # where conftest points MEMORY_DB_PATH
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ctx_x (a)")
    conn.close()
    (tmp_path / "sessions" / "tasks.db").write_bytes(b"")
    monkeypatch.setattr(hh, "find_bot", lambda: (None, "No bot to watch here."))
    for name, value in (("WINDOW_S", 0.01), ("WRITES", 2), ("FSYNCS", 2), ("OPENS", 2)):
        monkeypatch.setattr(hh, name, value)
    assert hh.main(["--cold"]) == 0
    out = capsys.readouterr().out
    cold = "from the disk (--cold): " if hasattr(os, "posix_fadvise") else "as cached now (--cold needs Linux): "
    for expected in ("Host health at ", "\nHost\n", "\nThe bot\n  No bot to watch here.\n",
                     f"\nmemory.db ({db})\n  Size: ", "Other databases beside it: tasks.db 0 B",
                     "Open memory.db and load its schema (1 objects): p50 ",
                     "as the only connection (its close checkpoints and syncs): p50 ",
                     "with another connection open (no checkpoint on close): p50 ",
                     "fsync of a small file: p50 ", f"Read memory.db end to end, {cold}",
                     "\nPython\n", "A fresh interpreter importing core.storage.sessions: ",
                     "A fixed CPU workload (JSON and arithmetic): "):
        assert expected in out, expected
    assert not [name for name in os.listdir(db.parent) if name.startswith(".host_health-")], "its scratch is gone"


def test_a_failing_section_is_reported_and_the_rest_still_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hh, "find_bot", lambda: (None, "No bot."))
    monkeypatch.setattr(hh, "timing_lines", lambda db, cold: 1 / 0)
    monkeypatch.setattr(hh, "python_lines", lambda: ["still here"])
    monkeypatch.setattr(hh, "WINDOW_S", 0.01)
    assert hh.main([]) == 0
    out = capsys.readouterr().out
    assert "Timings on its disk\n  failed: ZeroDivisionError: division by zero" in out
    assert "\nPython\n  still here" in out


def test_the_bot_section(tmp_path):
    (tmp_path / "7" / "fd").mkdir(parents=True)
    (tmp_path / "7" / "status").write_text("Name:\tpython\nThreads:\t41\nVmRSS:\t  512000 kB\n")
    lines = hh.bot_lines(7, "", {"bot_cpu": 0.08}, "/app/sessions/memory.db", proc=str(tmp_path))
    assert lines == [f"CPU 8% of one core over {hh.WINDOW_S:g} s, 41 threads, 500.0 MB resident",
                     "Has memory.db open right now: no"]
