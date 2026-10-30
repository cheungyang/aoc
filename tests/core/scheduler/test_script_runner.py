import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from core.scheduler import script_runner as sr
from core.scheduler.spec import ScheduleContext, ScriptStep, WorkDecision

GOOD = textwrap.dedent('''
    """Doc."""
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import json
    except ImportError:
        json = None
    CONSTANT = 1

    def has_work(ctx):
        return (CONSTANT == 1, "ready")

    def main():
        print("ran")

    if __name__ == "__main__":
        main()
''')


def ctx(last=None):
    return ScheduleContext(schedule_id="s", agent_id="a", now=0.0, last_success_at=last)


class TestParsing(unittest.TestCase):
    def test_parse_step(self):
        self.assertEqual(sr.parse_step("x.py --a 'b c'"), ScriptStep("x.py", ("--a", "b c")))
        with self.assertRaises(ValueError):
            sr.parse_step("   ")

    def test_resolve_script_path(self):
        self.assertTrue(sr.resolve_script_path("x.py", "/s").endswith("/s/x.py"))
        for bad in ("../x.py", "sub/x.py", "x.sh", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sr.resolve_script_path(bad, "/s")


class TestAstCheck(unittest.TestCase):
    def test_good_script_passes(self):
        self.assertEqual(sr.check_script_source(GOOD), [])

    def test_missing_has_work(self):
        src = GOOD.replace("def has_work(ctx):", "def other(ctx):")
        self.assertIn("defines no module-level has_work(ctx)", sr.check_script_source(src))

    def test_async_or_argless_has_work(self):
        errs = sr.check_script_source(GOOD.replace("def has_work(ctx):", "async def has_work(ctx):"))
        self.assertTrue(any("not async" in e for e in errs))
        errs = sr.check_script_source(GOOD.replace("def has_work(ctx):", "def has_work():"))
        self.assertTrue(any("ctx argument" in e for e in errs))

    def test_missing_main_guard(self):
        src = GOOD.split('if __name__')[0]
        self.assertTrue(any("guard" in e for e in sr.check_script_source(src)))

    def test_top_level_side_effect(self):
        src = GOOD.replace("CONSTANT = 1", "CONSTANT = 1\nmain()")
        errs = sr.check_script_source(src)
        self.assertTrue(any("runs on import" in e for e in errs), errs)

    def test_side_effect_nested_in_if(self):
        src = GOOD.replace("CONSTANT = 1", "CONSTANT = 1\nif CONSTANT:\n    os.chdir('/')")
        self.assertTrue(any("runs on import" in e for e in sr.check_script_source(src)))

    def test_top_level_loop_rejected(self):
        src = GOOD.replace("CONSTANT = 1", "CONSTANT = 1\nfor _ in []:\n    pass")
        self.assertTrue(any("For" in e for e in sr.check_script_source(src)))

    def test_reversed_main_guard(self):
        src = GOOD.replace('if __name__ == "__main__":', 'if "__main__" == __name__:')
        self.assertEqual(sr.check_script_source(src), [])

    def test_syntax_error(self):
        self.assertTrue(sr.check_script_source("def (")[0].startswith("does not parse"))

    def test_check_script_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("does not exist", sr.check_script("x.py", d)[0])
            with open(os.path.join(d, "x.py"), "w") as f:
                f.write(GOOD)
            self.assertEqual(sr.check_script("x.py", d), [])
            self.assertIn("bare file name", sr.check_script("../x.py", d)[0])


class TestHasWork(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        sr._module_cache.clear()

    def tearDown(self):
        self._tmp.cleanup()
        sr._module_cache.clear()

    def write(self, name, src):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            f.write(src)
        return path

    def test_calls_has_work_in_process_without_running_main(self):
        self.write("x.py", GOOD.replace('print("ran")', 'raise SystemExit("main ran")'))
        decision = sr.call_has_work(ScriptStep("x.py"), ctx(), scripts_dir=self.dir)
        self.assertEqual(decision, WorkDecision(True, "ready"))

    def test_bool_return_is_accepted(self):
        self.write("x.py", GOOD.replace('return (CONSTANT == 1, "ready")', "return False"))
        self.assertFalse(sr.call_has_work(ScriptStep("x.py"), ctx(), scripts_dir=self.dir))

    def test_crash_fails_open(self):
        self.write("x.py", GOOD.replace('return (CONSTANT == 1, "ready")', "raise ValueError('bad')"))
        decision = sr.call_has_work(ScriptStep("x.py"), ctx(), scripts_dir=self.dir)
        self.assertTrue(decision)
        self.assertIn("bad", decision.reason)

    def test_module_reloaded_when_file_changes(self):
        path = self.write("x.py", GOOD)
        first = sr.load_script_module(path)
        self.assertIs(sr.load_script_module(path), first)
        self.write("x.py", GOOD.replace("CONSTANT = 1", "CONSTANT = 2"))
        os.utime(path, (os.path.getmtime(path) + 5,) * 2)
        self.assertEqual(sr.load_script_module(path).CONSTANT, 2)


class TestExecution(unittest.IsolatedAsyncioTestCase):
    async def test_execute_command_success_and_errors(self):
        ok = await sr.execute_command([sys.executable, "-c", "print(' hi ')"], "lbl")
        self.assertEqual((ok.ok, ok.output), (True, "hi"))

        bad = await sr.execute_command(
            [sys.executable, "-c", "import sys; sys.stderr.write('oops'); sys.exit(2)"], "lbl"
        )
        self.assertFalse(bad.ok)
        self.assertEqual(bad.output, "Error executing script 'lbl': oops")

        silent = await sr.execute_command([sys.executable, "-c", "import sys; sys.exit(3)"], "lbl")
        self.assertIn("exited with code 3", silent.output)

    async def test_execute_command_timeout(self):
        with patch("core.scheduler.script_runner.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("x", 1)):
            res = await sr.execute_command(["x"], "lbl", timeout=1)
        self.assertFalse(res.ok)
        self.assertIn("timed out after 1s", res.output)

    async def test_run_step_runs_from_root_with_args(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "x.py"), "w") as f:
                f.write("import os, sys\nprint(os.getcwd(), sys.argv[1:])\n")
            res = await sr.run_step(ScriptStep("x.py", ("--a",)), scripts_dir=d)
        self.assertTrue(res.ok, res.output)
        self.assertIn(os.path.realpath(sr.PROJECT_ROOT), os.path.realpath(res.output.split()[0]))
        self.assertIn("'--a'", res.output)

    async def test_run_step_rejects_bad_name(self):
        res = await sr.run_step(ScriptStep("../x.py"))
        self.assertFalse(res.ok)

    async def test_execute_command_closes_stdin(self):
        res = await sr.execute_command(
            [sys.executable, "-c", "import sys; print(sys.stdin.isatty(), repr(sys.stdin.read()))"],
            "stdin_check",
            timeout=2,
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.output, "False ''")


class TestReadOnly(unittest.TestCase):
    def check(self, source):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write(textwrap.dedent(source))
            return sr.declares_read_only(path)

    def test_a_module_level_true_declares_it(self):
        self.assertTrue(self.check("READ_ONLY = True\n"))
        self.assertTrue(self.check("READ_ONLY: bool = True\n"))

    def test_anything_else_does_not(self):
        for source in ("", "READ_ONLY = False\n", "READ_ONLY = 1\n", "READ_ONLY = 'yes'\n",
                       "def f():\n    READ_ONLY = True\n", "if x:\n    READ_ONLY = True\n", "READ_ONLY = (\n"):
            with self.subTest(source=source):
                self.assertFalse(self.check(source))

    def test_the_last_assignment_wins(self):
        self.assertFalse(self.check("READ_ONLY = True\nREAD_ONLY = False\n"))

    def test_it_is_parsed_not_imported(self):
        self.assertTrue(self.check("READ_ONLY = True\nraise SystemExit('imported')\n"))

    def test_a_missing_file_declares_nothing(self):
        self.assertFalse(sr.declares_read_only("/nonexistent/x.py"))


class TestWriteReason(unittest.TestCase):
    def reason(self, source, *args):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write(textwrap.dedent(source))
            return sr.write_reason(path, args)

    def test_a_script_must_declare_read_only(self):
        self.assertEqual(self.reason("print(1)\n"), "it doesn't declare READ_ONLY = True")
        self.assertEqual(self.reason("READ_ONLY = True\n", "--anything"), "")

    def test_flags_that_make_it_write(self):
        src = "READ_ONLY = True\nWRITES_WITH = ('--archive-all', '--fix')\n"
        self.assertEqual(self.reason(src, "--voice", "--days", "7"), "")
        self.assertEqual(self.reason(src, "--voice", "--archive-all"), "--archive-all makes it write")
        self.assertEqual(self.reason(src, "--fix=all"), "--fix makes it write")
        self.assertEqual(self.reason(src, "--fixture"), "")
        self.assertEqual(self.reason("READ_ONLY = True\nWRITES_WITH = '--x'\n", "--x"), "--x makes it write")

    def test_what_cant_be_read_counts_as_writing(self):
        for writes_with in ("FLAGS", "('--a', 1)", "{'--a': 1}"):
            with self.subTest(writes_with=writes_with):
                self.assertEqual(self.reason(f"READ_ONLY = True\nWRITES_WITH = {writes_with}\n"),
                                 "its WRITES_WITH isn't a list of flags")
        self.assertEqual(self.reason("READ_ONLY = SOMETHING\n"), "it doesn't declare READ_ONLY = True")


class TestRunScript(unittest.IsolatedAsyncioTestCase):
    async def run_source(self, source, *args, timeout=None):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "x.py"), "w", encoding="utf-8") as f:
                f.write(textwrap.dedent(source))
            return await sr.run_script(ScriptStep("x.py", tuple(args)), scripts_dir=d, timeout=timeout)

    async def test_keeps_both_streams_and_the_exit_status(self):
        run = await self.run_source("import sys\nprint('out', *sys.argv[1:])\nprint('err', file=sys.stderr)\n", "a")
        self.assertEqual((run.returncode, run.ok, run.timed_out), (0, True, False))
        self.assertEqual((run.stdout, run.stderr, run.label), ("out a\n", "err\n", "x.py a"))
        self.assertGreater(run.seconds, 0)

    async def test_a_failure_keeps_its_output(self):
        run = await self.run_source("import sys\nprint('partial')\nsys.exit(4)\n")
        self.assertEqual((run.returncode, run.ok, run.stdout), (4, False, "partial\n"))

    async def test_a_timeout_keeps_what_was_printed(self):
        run = await self.run_source("import time\nprint('started', flush=True)\ntime.sleep(30)\n", timeout=1)
        self.assertTrue(run.timed_out)
        self.assertEqual((run.stdout, run.timeout), ("started\n", 1))

    async def test_runs_from_the_repo_root_with_stdin_closed(self):
        run = await self.run_source("import os, sys\nprint(os.getcwd())\nprint(repr(sys.stdin.read()))\n")
        cwd, stdin = run.stdout.splitlines()
        self.assertEqual(os.path.realpath(cwd), os.path.realpath(sr.PROJECT_ROOT))
        self.assertEqual(stdin, "''")

    async def test_a_bad_name_raises(self):
        with self.assertRaises(ValueError):
            await sr.run_script(ScriptStep("../x.py"))


class TestProjectScripts(unittest.IsolatedAsyncioTestCase):
    async def test_it_is_the_script_runner_behind_the_scripts_port(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "x.py"), "w", encoding="utf-8") as f:
                f.write("READ_ONLY = True\nWRITES_WITH = ('--w',)\nimport sys\nprint(*sys.argv[1:])\n")
            scripts = sr.ProjectScripts(d)
            self.assertEqual(scripts.root, sr.PROJECT_ROOT)
            self.assertEqual(scripts.path("x.py"), os.path.join(d, "x.py"))
            self.assertEqual(scripts.writes(scripts.path("x.py"), ["a"]), "")
            self.assertEqual(scripts.writes(scripts.path("x.py"), ["--w"]), "--w makes it write")
            run = await scripts.run("x.py", ["a", "b"], timeout=10)
        self.assertEqual((run.ok, run.stdout, run.label, run.timeout), (True, "a b\n", "x.py a b", 10))
        with self.assertRaises(ValueError):
            scripts.path("../x.py")


if __name__ == "__main__":
    unittest.main()
