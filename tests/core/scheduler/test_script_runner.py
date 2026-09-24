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


if __name__ == "__main__":
    unittest.main()
