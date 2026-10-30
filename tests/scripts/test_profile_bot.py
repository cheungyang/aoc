"""profile_bot: which process it profiles, whether py-spy can attach there, and what it posts."""
import gzip
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

import profile_bot as pb  # noqa: E402
from core.runtime.commands.typed.python import attachment  # noqa: E402
from core.scheduler.script_runner import write_reason  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY_SPY = "/usr/local/bin/py-spy"
DOCKER_DEFAULT_CAPS = 0x00000000A80425FB          # what a container gets without cap_add
WITH_SYS_PTRACE = DOCKER_DEFAULT_CAPS | 1 << 19   # ... and with `cap_add: [SYS_PTRACE]`


def test_python_runs_it_without_yes():
    assert write_reason(pb.__file__, ["record", "--duration", "60"]) == ""


# --- which process is the bot -------------------------------------------------

@pytest.mark.parametrize("argv, expected", [
    (["python", "main.py"], True),
    (["/usr/local/bin/python3.11", "main.py", "--dev"], True),
    (["python3", "/app/main.py"], True),
    (["python", "scripts/profile_bot.py"], False),
    (["/bin/sh", "-c", "python main.py"], False),
    ([], False),
    (None, False),
])
def test_the_bot_is_python_running_main(argv, expected):
    assert pb.is_bot(argv) is expected


@pytest.fixture
def processes(monkeypatch):
    """A /proc holding the given processes; this script's parent is PID 100."""
    def install(table):
        monkeypatch.setattr(pb, "_has_proc", lambda: True)
        monkeypatch.setattr(pb.os, "getppid", lambda: 100)
        monkeypatch.setattr(pb, "_cmdline", table.get)
        monkeypatch.setattr(pb, "_pids", lambda: sorted(table))
    return install


def test_under_python_the_parent_is_the_bot(processes):
    processes({7: ["python", "main.py"], 100: ["python", "main.py"]})
    assert pb.bot_pid() == 100


def test_from_a_shell_the_one_process_running_main_is_the_bot(processes):
    processes({1: ["docker-init", "--", "entrypoint.sh"], 7: ["python", "main.py"], 100: ["bash"]})
    assert pb.bot_pid() == 7
    assert pb.bot_pid(42) == 42, "--pid wins"


def test_when_there_is_no_telling_it_asks_for_the_pid(processes):
    processes({7: ["python", "main.py"], 8: ["python", "main.py", "--dev"], 100: ["bash"]})
    with pytest.raises(SystemExit, match=r"isn't running main.py, and 2 processes are \(7, 8\)\. Give it with --pid"):
        pb.bot_pid()
    pid, why = pb.find_bot()
    assert pid is None and why.endswith("and 2 processes are (7, 8).") and "--pid" not in why
    processes({100: ["bash"]})
    with pytest.raises(SystemExit, match="and no process is"):
        pb.bot_pid()


def test_without_proc_the_parent_is_trusted(monkeypatch):
    monkeypatch.setattr(pb, "_has_proc", lambda: False)
    monkeypatch.setattr(pb.os, "getppid", lambda: 100)
    assert pb.bot_pid() == 100


# --- whether py-spy can attach ------------------------------------------------

@pytest.fixture
def host(monkeypatch, tmp_path):
    """The facts /proc gives about attaching, from files the test writes; none to begin with."""
    status, scope = tmp_path / "status", tmp_path / "ptrace_scope"
    monkeypatch.setattr(pb, "PROC_STATUS", str(status))
    monkeypatch.setattr(pb, "PTRACE_SCOPE", str(scope))
    monkeypatch.setattr(pb, "has_file_capability", lambda path: None)

    class Host:
        def caps(self, bounding):
            status.write_text(f"Name:\tpython\nCapEff:\t0000000000000000\nCapBnd:\t{bounding:016x}\n")

        def yama(self, value):
            scope.write_text(f"{value}\n")

        def user(self, uid, file_capability):
            monkeypatch.setattr(pb.os, "geteuid", lambda: uid)
            monkeypatch.setattr(pb, "has_file_capability", lambda path: file_capability)
    return Host()


def test_without_py_spy_it_says_to_rebuild(host):
    assert pb.blockers(None) == [f"py-spy isn't installed: the image predates it. {pb.REBUILD}"]


def test_where_there_is_nothing_to_check_it_goes_ahead(host):
    assert pb.blockers(PY_SPY) == []


def test_a_container_without_sys_ptrace_must_be_recreated(host):
    host.caps(DOCKER_DEFAULT_CAPS)
    [problem] = pb.blockers(PY_SPY)
    assert "doesn't have SYS_PTRACE" in problem and "docker compose up -d --build" in problem
    host.caps(WITH_SYS_PTRACE)
    assert pb.blockers(PY_SPY) == []


def test_appuser_needs_the_file_capability_where_yama_limits_attaching(host):
    host.caps(WITH_SYS_PTRACE)
    host.yama(1)
    host.user(1026, file_capability=False)
    [problem] = pb.blockers(PY_SPY)
    assert "lacks its SYS_PTRACE file capability" in problem and "ptrace_scope = 1" in problem
    host.user(1026, file_capability=True)
    assert pb.blockers(PY_SPY) == []
    host.user(0, file_capability=False)
    assert pb.blockers(PY_SPY) == [], "root holds SYS_PTRACE itself"
    host.yama(0)
    host.user(1026, file_capability=False)
    assert pb.blockers(PY_SPY) == [], "any process of the same user may attach"


def test_scope_3_forbids_attaching_at_all(host):
    host.caps(WITH_SYS_PTRACE)
    host.yama(3)
    [problem] = pb.blockers(PY_SPY)
    assert "ptrace_scope = 3" in problem


def test_a_failure_report_lists_what_decides_attaching(host):
    host.caps(WITH_SYS_PTRACE)
    host.yama(1)
    host.user(1026, file_capability=True)
    assert pb.environment(PY_SPY) == ("running as uid 1026, container SYS_PTRACE: yes, "
                                      "py-spy file capability: yes, ptrace_scope: 1")


# --- py-spy's command lines -----------------------------------------------------

def test_a_recording_is_wall_clock_and_does_not_pause_the_bot():
    assert pb.record_command("py-spy", 7, "/p.json", "speedscope", 60, 50) == [
        "py-spy", "record", "--pid", "7", "--output", "/p.json", "--format", "speedscope",
        "--duration", "60", "--rate", "50", "--idle", "--nonblocking", "--hideprogress"]


def test_record_options():
    assert "--threads" in pb.record_command("py-spy", 7, "/p.svg", "flamegraph", 60, 50)
    gil = pb.record_command("py-spy", 7, "/p", "speedscope", 60, 50, gil=True)
    assert "--gil" in gil and "--idle" not in gil
    native = pb.record_command("py-spy", 7, "/p", "speedscope", 60, 50, native=True)
    assert "--native" in native and "--nonblocking" not in native, "C frames can't be read without pausing"


def test_dump_command():
    assert pb.dump_command("py-spy", 7) == ["py-spy", "dump", "--pid", "7"]
    assert pb.dump_command("py-spy", 7, native=True) == ["py-spy", "dump", "--pid", "7", "--native"]


def test_a_bare_command_or_option_means_dump():
    assert pb.parse_args([]).mode == "dump"
    args = pb.parse_args(["--pid", "7"])
    assert (args.mode, args.pid) == ("dump", 7)


def test_record_defaults_and_bounds():
    args = pb.parse_args(["record"])
    assert (args.duration, args.rate, args.format, args.gil, args.native) == (60, 50, "speedscope", False, False)
    for bad in (["record", "--duration", "0"], ["record", "--duration", "601"], ["record", "--rate", "x"],
                ["record", "--format", "svg"], ["dump", "--duration", "5"], ["recrod"]):
        with pytest.raises(SystemExit):
            pb.parse_args(bad)


# --- the files it leaves ----------------------------------------------------------

def test_only_the_newest_profiles_are_kept(tmp_path):
    profiles = tmp_path / "profiles"
    (profiles / "a-directory").mkdir(parents=True)
    for i in range(5):
        path = profiles / f"{i}.svg"
        path.write_text("x")
        os.utime(path, (1000 + i, 1000 + i))
    pb.prune(str(profiles), 2)
    assert sorted(os.listdir(profiles)) == ["3.svg", "4.svg", "a-directory"]


def test_a_profile_too_big_for_discord_is_gzipped(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "MAX_ATTACH_BYTES", 1000)
    small, big = tmp_path / "small.json", tmp_path / "big.json"
    small.write_text("x" * 1000)
    big.write_text("x" * 5000)
    assert pb.attachable(str(small)) == str(small)
    assert pb.attachable(str(big)) == str(big) + ".gz" and not big.exists()
    with gzip.open(str(big) + ".gz", "rt") as f:
        assert f.read() == "x" * 5000


def test_a_profile_too_big_even_gzipped_is_not_attached(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "MAX_ATTACH_BYTES", 100)
    path = tmp_path / "noise.json"
    path.write_bytes(os.urandom(5000))
    assert pb.attachable(str(path)) is None


# --- end to end, with a py-spy that acts its part ---------------------------------

FAKE_PY_SPY = '''\
#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_PY_SPY_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if os.environ.get("FAKE_PY_SPY_FAIL"):
    print("Permission Denied: Try running again with elevated permissions", file=sys.stderr)
    sys.exit(1)
if args[0] == "dump":
    print('Process 7: python main.py\\n\\nThread 7 (idle): "MainThread"\\n    select (selectors.py:468)')
else:
    out = args[args.index("--output") + 1]
    with open(out, "w") as f:
        f.write("x" * int(os.environ.get("FAKE_PY_SPY_BYTES", "100")))
    if os.environ.get("FAKE_PY_SPY_LATE"):
        print("py-spy> 1.21s behind in sampling, results may be inaccurate. Try reducing the sampling rate")
    print(f"py-spy> Wrote speedscope file to '{{out}}'. Samples: 3000 Errors: 2")
'''


@pytest.fixture
def py_spy(monkeypatch, tmp_path, host):
    """A py-spy that logs its arguments and acts out dump and record. The
    project root, where profiles go, is tmp_path."""
    path = tmp_path / "py-spy"
    path.write_text(FAKE_PY_SPY.format(python=sys.executable))
    path.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_PY_SPY_LOG", str(log))
    monkeypatch.setattr(pb, "find_py_spy", lambda: str(path))
    monkeypatch.setattr(pb, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(pb, "PROFILES_DIR", str(tmp_path / "sessions" / "profiles"))

    class Calls:
        def __call__(self):
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return Calls()


def test_dump_prints_every_thread(py_spy, capsys):
    assert pb.main(["--pid", "7"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("The bot's threads at ")
    assert 'Thread 7 (idle): "MainThread"\n    select (selectors.py:468)' in out
    assert py_spy() == [["dump", "--pid", "7"]]


def test_record_writes_the_profile_and_python_attaches_it(py_spy, capsys):
    assert pb.main(["record", "--pid", "7", "--duration", "5"]) == 0
    out = capsys.readouterr().out
    assert "every thread, waiting ones included, without pausing it." in out
    assert "py-spy: 3,000 samples, 2 errors." in out and "speedscope.app" in out
    assert "fell behind" not in out
    last = out.splitlines()[-1]
    assert re.fullmatch(r"ATTACH: sessions/profiles/\d{8}-\d{6}\.speedscope\.json", last)
    full, why_not = attachment(last[len("ATTACH: "):], pb.PROJECT_ROOT)
    assert full and not why_not, why_not
    [call] = py_spy()
    assert call[:3] == ["record", "--pid", "7"] and {"--idle", "--nonblocking", "--hideprogress"} <= set(call)


def test_a_sampler_that_fell_behind_is_mentioned(py_spy, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_PY_SPY_LATE", "1")
    assert pb.main(["record", "--pid", "7", "--duration", "5"]) == 0
    assert "py-spy fell behind at times, so the timeline is less exact: a lower --rate helps." in capsys.readouterr().out


def test_a_failure_shows_py_spys_error_and_what_decides_attaching(py_spy, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_PY_SPY_FAIL", "1")
    assert pb.main(["record", "--pid", "7", "--duration", "5"]) == 1
    out = capsys.readouterr().out
    assert "py-spy couldn't record PID 7 (exit 1):\nPermission Denied" in out
    assert "\nWhat decides attaching here: running as " in out and "ATTACH:" not in out


def test_a_host_that_would_refuse_says_why_and_runs_nothing(py_spy, host, capsys):
    host.caps(DOCKER_DEFAULT_CAPS)
    assert pb.main(["record", "--pid", "7"]) == 1
    assert "doesn't have SYS_PTRACE" in capsys.readouterr().out
    assert py_spy() == []


def test_a_recording_prunes_old_profiles(py_spy, monkeypatch, capsys):
    monkeypatch.setattr(pb, "KEEP_PROFILES", 2)
    os.makedirs(pb.PROFILES_DIR)
    for i in range(3):
        path = os.path.join(pb.PROFILES_DIR, f"old{i}.svg")
        open(path, "w").close()
        os.utime(path, (1000 + i, 1000 + i))
    assert pb.main(["record", "--pid", "7", "--duration", "5"]) == 0
    kept = sorted(os.listdir(pb.PROFILES_DIR))
    assert len(kept) == 2 and kept[1] == "old2.svg" and kept[0].endswith(".speedscope.json")


def test_a_big_recording_is_gzipped_to_attach(py_spy, monkeypatch, capsys):
    monkeypatch.setattr(pb, "MAX_ATTACH_BYTES", 1000)
    monkeypatch.setenv("FAKE_PY_SPY_BYTES", "50000")
    assert pb.main(["record", "--pid", "7", "--duration", "5"]) == 0
    out = capsys.readouterr().out
    assert "gzipped to fit in Discord" in out
    assert out.splitlines()[-1].endswith(".speedscope.json.gz")


# --- the image and the container ---------------------------------------------------

def test_the_image_and_the_container_let_py_spy_attach():
    """Without any one of these, `!python profile_bot.py` can't attach on the NAS."""
    with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as f:
        dockerfile = f.read()
    with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as f:
        compose = f.read()
    assert re.search(r'pip3 install --no-cache-dir "py-spy==\$\{PY_SPY_VERSION\}"', dockerfile)
    assert 'setcap cap_sys_ptrace+ep "$(command -v py-spy)"' in dockerfile
    assert re.search(r"^\s+cap_add:\n\s+- SYS_PTRACE$", compose, re.MULTILINE)
