"""The re-exec shim every scheduled script depends on.

Scripts under `scripts/` are launched by cron and by the script-executor with
whatever PATH the caller happens to have. On macOS that is the system python,
which has none of this project's dependencies. Without this module a scheduled
script dies on its first import and posts a traceback on *every* run.

Three properties are load-bearing and each has a way of failing silently:

1. **It re-execs only when it has to.** An unnecessary exec is invisible when it
   works and baffling when it does not.
2. **It re-execs at most once.** The guard flag is the only thing standing
   between a misconfigured venv and an infinite process chain.
3. **It imports nothing from this project.** It runs *before* the interpreter is
   known to be correct, so a single project import would defeat its entire
   purpose -- and would do so only on the machine that lacks the dependency.
"""
import ast
import os
import sys
import unittest
from unittest.mock import patch

from scripts import _bootstrap

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SOURCE_PATH = os.path.join(PROJECT_ROOT, "scripts", "_bootstrap.py")

STDLIB_ONLY = {"os", "sys", "importlib", "importlib.util"}


class TestProjectRoot(unittest.TestCase):
    def test_it_resolves_the_repo_not_the_scripts_directory(self):
        """An off-by-one `dirname` here silently points every scheduled script at
        `scripts/`, which is not a repo -- the exact bug that shipped once in
        `graphs/coding/utils/repo.py`."""
        root = _bootstrap.project_root()

        self.assertTrue(os.path.isdir(os.path.join(root, ".git")))
        self.assertTrue(os.path.isdir(os.path.join(root, "scripts")))
        self.assertTrue(os.path.isdir(os.path.join(root, "graphs")))


class TestStdlibOnly(unittest.TestCase):
    def test_the_module_imports_nothing_from_this_project(self):
        """This is the module's whole contract. A project import would work fine
        on a developer machine and fail only under cron, where the traceback is
        hardest to see."""
        with open(SOURCE_PATH, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)

        self.assertTrue(
            imported <= STDLIB_ONLY,
            f"_bootstrap.py must stay stdlib-only; found {sorted(imported - STDLIB_ONLY)}"
        )


class EnsureInterpreterTestCase(unittest.TestCase):
    """`ensure_project_interpreter` either calls `os.execv` or it does not; every
    test here is about which."""

    def setUp(self):
        self.execv = patch("os.execv")
        self.mock_execv = self.execv.start()
        self.addCleanup(self.execv.stop)

        # The flag is process-global and leaks between tests otherwise.
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("AOC_SCRIPT_REEXEC", None)


class TestSkipsReexec(EnsureInterpreterTestCase):
    def test_it_does_nothing_when_the_probe_module_is_already_importable(self):
        """The common case: we are already running under the right interpreter."""
        _bootstrap.ensure_project_interpreter(probe_module="os")

        self.mock_execv.assert_not_called()

    def test_the_guard_flag_prevents_a_second_attempt(self):
        """Without this, a venv that exists but still cannot import the probe
        module spawns processes until the machine gives up.

        `realpath` is patched to the identity function on purpose: the suite runs
        under the repo's own `.venv`, so otherwise the "already the right
        interpreter" branch returns first and this passes without ever consulting
        the flag.
        """
        os.environ["AOC_SCRIPT_REEXEC"] = "1"

        with patch("importlib.util.find_spec", return_value=None), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.realpath", side_effect=lambda p: p), \
             patch.object(sys, "argv", ["scripts/coding_tick.py"]):
            _bootstrap.ensure_project_interpreter(probe_module="definitely_not_installed")

        self.mock_execv.assert_not_called()

    def test_it_gives_up_quietly_when_there_is_no_venv(self):
        """Not every checkout has one. Failing here would break the scripts that
        genuinely run under a system interpreter with the deps installed."""
        with patch("importlib.util.find_spec", return_value=None), \
             patch("os.path.exists", return_value=False):
            _bootstrap.ensure_project_interpreter(probe_module="definitely_not_installed")

        self.mock_execv.assert_not_called()

    def test_it_refuses_to_re_exec_into_the_interpreter_already_running(self):
        """The venv python failing to import the probe module is a broken venv.
        Re-execing into it is the infinite chain the flag also guards."""
        with patch("importlib.util.find_spec", return_value=None), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.realpath", side_effect=lambda p: "/same/python"):
            _bootstrap.ensure_project_interpreter(probe_module="definitely_not_installed")

        self.mock_execv.assert_not_called()


class TestPerformsReexec(EnsureInterpreterTestCase):
    def _run(self, argv):
        """Stands the caller up as a *different* interpreter from the venv.

        `realpath` is the identity here so the comparison in
        `ensure_project_interpreter` is over the literal paths, and
        `sys.executable` is pinned to a system python: without that pin the
        suite's own interpreter *is* `<root>/.venv/bin/python`, the
        "already the right interpreter" guard fires, and every assertion below
        would be about a re-exec that never happened.
        """
        with patch("importlib.util.find_spec", return_value=None), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.realpath", side_effect=lambda p: p), \
             patch.object(sys, "executable", "/usr/bin/python3"), \
             patch.object(sys, "argv", argv):
            _bootstrap.ensure_project_interpreter(probe_module="definitely_not_installed")

    def test_it_re_execs_into_the_venv_interpreter(self):
        self._run(["scripts/coding_tick.py"])

        self.mock_execv.assert_called_once()
        venv_python, args = self.mock_execv.call_args.args
        self.assertEqual(venv_python, os.path.join(_bootstrap.project_root(), ".venv", "bin", "python"))
        self.assertEqual(args[0], venv_python)

    def test_it_forwards_the_original_arguments(self):
        """Dropping these would turn `coding_tick.py --manifest X` into a run
        against the real manifest."""
        self._run(["scripts/coding_tick.py", "--manifest", "/tmp/x.json", "--dry-run"])

        _, args = self.mock_execv.call_args.args
        self.assertEqual(args[2:], ["--manifest", "/tmp/x.json", "--dry-run"])

    def test_it_re_execs_the_script_by_absolute_path(self):
        """The re-exec inherits the caller's cwd, which under cron is not the
        repo, so a relative script path would not resolve."""
        self._run(["scripts/coding_tick.py"])

        _, args = self.mock_execv.call_args.args
        self.assertTrue(os.path.isabs(args[1]))

    def test_it_sets_the_guard_flag_before_handing_over(self):
        """`execv` replaces the process, so anything set after it never runs."""
        self._run(["scripts/coding_tick.py"])

        self.assertEqual(os.environ.get("AOC_SCRIPT_REEXEC"), "1")


class TestEnterProjectRoot(unittest.TestCase):
    def setUp(self):
        self.original_cwd = os.getcwd()
        self.original_path = list(sys.path)
        self.addCleanup(os.chdir, self.original_cwd)
        self.addCleanup(setattr, sys, "path", self.original_path)

    def test_it_makes_the_repo_the_working_directory(self):
        """Graph and manifest paths are resolved relative to the cwd, so a cron
        invocation from anywhere must still act on the repo."""
        root = _bootstrap.enter_project_root()

        self.assertEqual(os.path.realpath(os.getcwd()), os.path.realpath(root))

    def test_it_puts_the_repo_on_the_import_path(self):
        sys.path = [p for p in sys.path if p != _bootstrap.project_root()]

        root = _bootstrap.enter_project_root()

        self.assertEqual(sys.path[0], root)

    def test_it_does_not_add_the_repo_twice(self):
        """Called from two scripts in one process, a naive insert grows sys.path
        on every call.

        The root is stripped first because pytest puts it on `sys.path` too;
        counting globally would make this pass or fail on runner behaviour
        rather than on the function under test.
        """
        root = _bootstrap.project_root()
        sys.path = [p for p in sys.path if p != root]

        _bootstrap.enter_project_root()
        _bootstrap.enter_project_root()

        self.assertEqual(sys.path.count(root), 1)


if __name__ == "__main__":
    unittest.main()
