"""!python: runs a script from scripts/ for the bot's owner and posts what it printed."""
import os
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.runtime.commands.base import TypedContext
from core.runtime.commands.registry import TYPED
from core.runtime.commands.typed import python as py
from core.scheduler.script_runner import ProjectScripts


class TestParse(unittest.TestCase):

    def test_options_go_before_the_script_and_the_rest_is_the_scripts_own(self):
        self.assertEqual(py.parse("r.py a 'b c'"), py.Invocation("r.py", ("a", "b c")))
        self.assertEqual(py.parse("--yes --timeout 60 r.py --x"),
                         py.Invocation("r.py", ("--x",), confirmed=True, timeout=60))
        self.assertEqual(py.parse("--timeout=5 r.py").timeout, 5)
        self.assertEqual(py.parse("r.py a 'b c'").label, "r.py a b c")
        # coding_admin.py has a --yes of its own: after the name, it's the script's.
        invocation = py.parse("coding_admin.py reset 7 --yes")
        self.assertFalse(invocation.confirmed)
        self.assertEqual(invocation.args, ("reset", "7", "--yes"))

    def test_bad_input_says_whats_wrong(self):
        for args, says in (("", "Usage"), ("--yes", "Usage"), ("--nope r.py", "Unknown option `--nope`"),
                           ("--timeout r.py", "whole seconds"), ("--timeout 0 r.py", "1 to 10800"),
                           ("--timeout 99999 r.py", "1 to 10800"), ("r.py 'unclosed", "Can't read that")):
            with self.subTest(args=args), self.assertRaises(ValueError) as caught:
                py.parse(args)
            self.assertIn(says, str(caught.exception))


class TestPythonCommand(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.scripts = tempfile.TemporaryDirectory()
        self.addCleanup(self.scripts.cleanup)
        self.status = MagicMock()
        self.status.edit = AsyncMock()
        self.channel = AsyncMock()
        self.channel.name = "general"
        self.channel.send.return_value = self.status

    def script(self, name, body, read_only=True):
        source = ("READ_ONLY = True\n" if read_only else "") + textwrap.dedent(body)
        with open(os.path.join(self.scripts.name, name), "w", encoding="utf-8") as f:
            f.write(source)

    def ctx(self, owner=True, answers_here=True, **kwargs):
        is_owner = kwargs.pop("is_owner", AsyncMock(return_value=owner))
        scripts = kwargs.pop("scripts", ProjectScripts(self.scripts.name))
        return TypedContext(channel=self.channel, author=MagicMock(), answers_here=answers_here, is_owner=is_owner,
                            scripts=scripts)

    async def answer(self, text, ctx=None):
        return await TYPED.answer(text, ctx or self.ctx())

    def posts(self):
        """What was posted, as text, in order (files by their name)."""
        out = []
        for call in self.channel.send.call_args_list:
            file = call.kwargs.get("file")
            out.append(f"[file {file.filename}]" if file is not None else call.args[0])
        return out

    def edited(self):
        return [call.kwargs["content"] for call in self.status.edit.call_args_list]

    async def test_only_the_owner_runs_scripts(self):
        marker = os.path.join(self.scripts.name, "ran")
        self.script("w.py", f"open({marker!r}, 'w').close()\n")
        for ctx in (self.ctx(owner=False), self.ctx(is_owner=None)):
            self.assertIsNotNone(await self.answer("!python w.py", ctx))
        self.assertEqual(self.posts(), ["Only the bot's owner can run scripts."] * 2)
        self.assertFalse(os.path.exists(marker))

    async def test_a_failing_owner_check_refuses(self):
        await self.answer("!python w.py", self.ctx(is_owner=AsyncMock(side_effect=RuntimeError("no app info"))))
        self.assertEqual(self.posts(), ["Only the bot's owner can run scripts."])

    async def test_without_scripts_in_the_context_nothing_runs(self):
        await self.answer("!python r.py", self.ctx(scripts=None))
        self.assertEqual(self.posts(), ["Scripts can't be run from here."])

    async def test_only_the_bot_that_answers_the_channel_answers(self):
        self.assertIsNone(await self.answer("!python r.py", self.ctx(answers_here=False)))
        self.channel.send.assert_not_called()

    async def test_names_must_be_scripts_in_scripts(self):
        await self.answer("!python ../x.py")
        await self.answer("!python x.sh")
        await self.answer("!python nope.py")
        posts = self.posts()
        self.assertIn("must be a bare file name inside scripts/", posts[0])
        self.assertIn("is not a Python script", posts[1])
        self.assertEqual(posts[2], "There's no `scripts/nope.py`.")

    async def test_a_script_that_may_change_things_needs_yes(self):
        marker = os.path.join(self.scripts.name, "ran")
        self.script("w.py", f"open({marker!r}, 'w').close()\n", read_only=False)
        await self.answer("!python w.py a")
        self.assertEqual(self.posts(), ["⚠️ `w.py a` may change things: it doesn't declare READ_ONLY = True. "
                                        "Nothing ran. To run it: `!python --yes w.py a`"])
        self.assertFalse(os.path.exists(marker))
        await self.answer("!python --yes w.py a")
        self.assertTrue(os.path.exists(marker))
        self.assertTrue(self.edited()[0].startswith("✅ `w.py a` finished in "))
        self.assertTrue(self.edited()[0].endswith("It printed nothing."))

    async def test_a_flag_that_makes_a_read_only_script_write_needs_yes(self):
        self.script("stats.py", "WRITES_WITH = ('--archive-all',)\nprint('report')\n")
        await self.answer("!python stats.py --voice")
        self.assertIn("```\nreport\n```", self.posts())
        self.channel.send.reset_mock()
        await self.answer("!python stats.py --archive-all")
        self.assertEqual(self.posts(), ["⚠️ `stats.py --archive-all` may change things: --archive-all makes it write. "
                                        "Nothing ran. To run it: `!python --yes stats.py --archive-all`"])

    async def test_output_is_posted_as_a_code_block_under_the_edited_status(self):
        self.script("r.py", "import sys\nprint('hello', *sys.argv[1:])\n")
        await self.answer("!python r.py a 'b c'")
        self.assertEqual(self.posts(), ["⏳ Running `r.py a b c`…", "```\nhello a b c\n```"])
        self.assertRegex(self.edited()[0], r"^✅ `r.py a b c` finished in \d+\.\d s\.$")

    async def test_a_status_that_cant_be_edited_is_posted_instead(self):
        self.channel.send.return_value = object()
        self.script("r.py", "print('hi')\n")
        await self.answer("!python r.py")
        posts = self.posts()
        self.assertTrue(posts[1].startswith("✅ `r.py` finished in "))
        self.assertEqual(posts[2], "```\nhi\n```")

    async def test_a_failure_shows_its_exit_code_and_stderr(self):
        self.script("f.py", "import sys\nprint('out')\nprint('bad', file=sys.stderr)\nsys.exit(3)\n")
        await self.answer("!python f.py")
        self.assertRegex(self.edited()[0], r"^❌ `f.py` exited with 3 after ")
        self.assertEqual(self.posts()[-1], "```\nout\n\nstderr:\nbad\n```")

    async def test_a_timeout_keeps_what_was_printed(self):
        self.script("s.py", "import time\nprint('started', flush=True)\ntime.sleep(30)\n")
        await self.answer("!python --timeout 1 s.py")
        self.assertEqual(self.edited(), ["⌛ `s.py` was stopped at its 1 s timeout."])
        self.assertEqual(self.posts()[-1], "```\nstarted\n```")

    async def test_long_output_shows_both_ends_and_attaches_all_of_it(self):
        self.script("long.py", "for i in range(2000):\n    print(f'line {i:04d}')\n")
        await self.answer("!python long.py")
        posts = self.posts()
        shown = "\n".join(p for p in posts if not p.startswith("[file"))
        self.assertIn("line 0000", shown)
        self.assertIn("line 1999", shown)
        self.assertNotIn("line 1000", shown)
        self.assertIn("characters not shown: the whole output is attached", shown)
        self.assertTrue(any(p.startswith("[file long-") and p.endswith(".txt]") for p in posts))

    async def test_attach_lines_attach_files_under_the_temp_directory_only(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{}")
        self.addCleanup(os.remove, f.name)
        self.script("a.py", f"print('done')\nprint('ATTACH: {f.name}')\nprint('ATTACH: /etc/hosts')\n")
        await self.answer("!python a.py")
        posts = self.posts()
        self.assertIn("```\ndone\n```", posts)
        self.assertIn(f"[file {os.path.basename(f.name)}]", posts)
        self.assertIn("Not attached: `/etc/hosts`: only files under sessions/ or the temp directory are attached.",
                      posts)

    async def test_one_script_at_a_time(self):
        self.script("r.py", "print('hi')\n")
        self.assertTrue(py._lock.acquire(blocking=False))
        py._running = "slow.py"
        try:
            await self.answer("!python r.py")
        finally:
            py._running = None
            py._lock.release()
        self.assertEqual(self.posts(), ["⏳ `slow.py` is still running. One at a time: try again when it's done."])

    async def test_the_lock_is_released_after_a_run(self):
        self.script("r.py", "print('hi')\n")
        await self.answer("!python r.py")
        self.assertIsInstance(py._lock, type(threading.Lock()))
        self.assertTrue(py._lock.acquire(blocking=False))
        py._lock.release()


if __name__ == "__main__":
    unittest.main()
