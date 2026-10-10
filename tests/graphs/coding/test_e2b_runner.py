"""The E2B backend, against a fake sandbox.

What matters is not the SDK calls but the guarantees: exit codes are never
masked, a runner failure is never reported as a code failure, the only
credential that reaches the sandbox is a one-download URL, a setup that edits
tracked files is caught, and the sandbox is always killed.
"""
import unittest
from unittest.mock import patch

from e2b import CommandExitException, TimeoutException

from graphs.coding.utils.sandbox import Step
from graphs.coding.utils.sandbox import e2b_runner
from graphs.coding.utils.sandbox.e2b_runner import E2BRunner

META = {"task_id": "T1", "head_sha": "abc1234def", "repo": "org/repo"}
URL = "https://codeload.github.com/org/repo/legacy.tar.gz/abc?token=ONE_DOWNLOAD"


class _Result:
    def __init__(self, exit_code=0, stdout="", stderr=""):
        self.exit_code, self.stdout, self.stderr = exit_code, stdout, stderr


class FakeSandbox:
    """Records every command; `script` maps a substring to a result or exception."""

    instances = []
    create_error = None

    def __init__(self, script):
        self.script = script
        self.calls = []
        self.killed = False
        self.commands = self

    @classmethod
    def factory(cls, script=None, create_error=None):
        class _Bound(cls):
            instances = []

            @classmethod
            async def create(klass, **kwargs):
                if create_error:
                    raise create_error
                sbx = cls(script or {})
                sbx.create_kwargs = kwargs
                klass.instances.append(sbx)
                return sbx

        return _Bound

    async def run(self, cmd, cwd=None, timeout=None, envs=None):
        self.calls.append({"cmd": cmd, "cwd": cwd, "timeout": timeout, "envs": envs})
        for needle, outcome in self.script.items():
            if needle in cmd:
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
        return _Result()

    async def kill(self):
        self.killed = True


def _exit(code, stdout="", stderr=""):
    return CommandExitException(stderr=stderr, stdout=stdout, exit_code=code, error=None)


class E2BCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.urls = []

        async def source_url(repo, sha, token):
            self.urls.append((repo, sha))
            return URL

        self.source_url = source_url
        patcher = patch.object(e2b_runner, "github_token", return_value="ghp_SECRET")
        patcher.start()
        self.addCleanup(patcher.stop)

    def runner(self, script=None, create_error=None, source_url=None):
        self.cls = FakeSandbox.factory(script, create_error)
        return E2BRunner(api_key="e2b_key", sandbox_cls=self.cls,
                         source_url=source_url or self.source_url)

    @property
    def sbx(self):
        return self.cls.instances[-1]

    async def run_steps(self, runner, steps=None, timeout=600, meta=META):
        steps = steps or [Step("verify", "pytest -q")]
        return await runner.run(steps, workspace_path="/ignored", timeout=timeout, meta=meta)


class TestHappyPath(E2BCase):
    async def test_fetches_the_pushed_commit_and_runs_the_steps(self):
        result = await self.run_steps(self.runner({"pytest": _Result(0, "3 passed")}))

        self.assertTrue(result.passed)
        self.assertEqual(result.backend, "e2b")
        self.assertIn("3 passed", result.stdout)
        self.assertEqual(self.urls, [("org/repo", "abc1234def")])
        self.assertIn("curl", self.sbx.calls[0]["cmd"])
        self.assertTrue(self.sbx.killed)

    async def test_every_step_runs_under_pipefail(self):
        """`cmd | tail` reported success for a failing run in the spike."""
        await self.run_steps(self.runner())

        verify = self.sbx.calls[-1]["cmd"]
        self.assertTrue(verify.startswith("bash -o pipefail -c "))
        self.assertIn("pytest -q", verify)

    async def test_sandbox_is_tagged_and_expires_after_the_budget(self):
        await self.run_steps(self.runner(), timeout=600)

        kwargs = self.sbx.create_kwargs
        self.assertEqual(kwargs["metadata"],
                         {"task_id": "T1", "head_sha": "abc1234def", "repo": "org/repo"})
        self.assertEqual(kwargs["timeout"], 600 + e2b_runner.SANDBOX_GRACE_SECONDS)
        self.assertEqual(kwargs["api_key"], "e2b_key")


class TestCredentials(E2BCase):
    async def test_only_the_one_download_url_reaches_the_sandbox(self):
        await self.run_steps(self.runner(), steps=[Step("setup", "npm ci"), Step("verify", "npm test")])

        self.assertEqual(self.sbx.calls[0]["envs"], {"SRC_URL": URL})
        everything = repr(self.sbx.create_kwargs) + repr(self.sbx.calls)
        self.assertNotIn("ghp_SECRET", everything)
        # The URL travels in an env var, not on the command line.
        self.assertNotIn("ONE_DOWNLOAD", self.sbx.calls[0]["cmd"])


class TestExitCodes(E2BCase):
    async def test_a_failing_step_reports_its_exit_code_and_name(self):
        runner = self.runner({"pytest": _exit(1, "1 failed", "AssertionError")})

        result = await self.run_steps(runner)

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.failed_step, "verify")
        self.assertIsNone(result.infra_error)
        self.assertIn("AssertionError", result.stderr)
        self.assertTrue(self.sbx.killed)

    async def test_steps_stop_at_the_first_failure(self):
        runner = self.runner({"npm ci": _exit(1, "", "ERESOLVE")})

        result = await self.run_steps(runner, steps=[Step("setup", "npm ci"), Step("verify", "npm test")])

        self.assertEqual(result.failed_step, "setup")
        self.assertFalse(any("npm test" in c["cmd"] for c in self.sbx.calls))

    async def test_a_timeout_is_124(self):
        runner = self.runner({"pytest": TimeoutException("deadline")})

        result = await self.run_steps(runner)

        self.assertEqual(result.exit_code, 124)
        self.assertIsNone(result.infra_error)
        self.assertTrue(self.sbx.killed)

    async def test_long_output_is_truncated_after_the_exit_code_is_known(self):
        runner = self.runner({"pytest": _exit(1, "x" * (e2b_runner.OUTPUT_LIMIT * 2))})

        result = await self.run_steps(runner)

        self.assertEqual(result.exit_code, 1)
        self.assertLess(len(result.stdout), e2b_runner.OUTPUT_LIMIT + 100)


class TestRunnerFailuresAreInfra(E2BCase):
    async def test_no_download_url_is_infra(self):
        async def broken(*_a):
            raise RuntimeError("GitHub returned 404")

        result = await self.run_steps(self.runner(source_url=broken))

        self.assertIsNotNone(result.infra_error)
        self.assertIn("404", result.infra_error)

    async def test_a_sandbox_that_will_not_start_is_infra(self):
        result = await self.run_steps(self.runner(create_error=RuntimeError("quota")))

        self.assertIn("did not start", result.infra_error)

    async def test_a_failed_download_is_infra_and_still_kills(self):
        result = await self.run_steps(self.runner({"curl": _exit(22, "", "403")}))

        self.assertIn("fetching the source failed", result.infra_error)
        self.assertTrue(self.sbx.killed)

    async def test_a_sandbox_crash_mid_step_is_infra(self):
        result = await self.run_steps(self.runner({"pytest": ConnectionError("gone")}))

        self.assertIn("during `verify`", result.infra_error)
        self.assertTrue(self.sbx.killed)


class TestConfiguration(E2BCase):
    async def test_missing_repo_or_commit_is_unrunnable(self):
        result = await self.run_steps(self.runner(), meta={"task_id": "T1"})

        self.assertEqual(result.exit_code, 126)
        self.assertIsNone(result.infra_error)

    async def test_missing_api_key_is_unrunnable(self):
        import os

        runner = E2BRunner(sandbox_cls=FakeSandbox.factory(), source_url=self.source_url)
        with patch.dict(os.environ, {e2b_runner.API_KEY_ENV: "",
                                     e2b_runner.API_KEY_FILE_ENV: "/nonexistent/key"}):
            result = await self.run_steps(runner)

        self.assertEqual(result.exit_code, 126)
        self.assertIn("No E2B API key", result.stderr)

    async def test_api_key_file_is_read(self):
        import os
        import tempfile

        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("  e2b_from_file\n")
        self.addCleanup(os.unlink, f.name)
        with patch.dict(os.environ, {e2b_runner.API_KEY_ENV: "",
                                     e2b_runner.API_KEY_FILE_ENV: f.name}):
            self.assertEqual(e2b_runner.read_api_key(), "e2b_from_file")


class TestSetupPurity(E2BCase):
    STEPS = [Step("setup", "npm ci"), Step("verify", "npm test")]

    async def test_a_setup_that_edits_tracked_files_fails(self):
        runner = self.runner({"git status": _Result(0, " M src/App.tsx\n?? src/new.ts\n")})

        result = await self.run_steps(runner, steps=self.STEPS)

        self.assertEqual(result.failed_step, "setup")
        self.assertIn("scaffold_command", result.stderr)
        self.assertIn("src/App.tsx", result.stderr)
        self.assertFalse(any("npm test" in c["cmd"] for c in self.sbx.calls))

    async def test_a_clean_setup_proceeds(self):
        result = await self.run_steps(self.runner(), steps=self.STEPS)

        self.assertTrue(result.passed)
        cmds = [c["cmd"] for c in self.sbx.calls]
        self.assertTrue(any("git init" in c for c in cmds))
        self.assertTrue(any("npm test" in c for c in cmds))

    async def test_no_setup_step_means_no_baseline(self):
        await self.run_steps(self.runner())

        self.assertFalse(any("git init" in c["cmd"] for c in self.sbx.calls))


class TestFactory(unittest.TestCase):
    def test_the_manifest_can_select_e2b(self):
        from graphs.coding.utils.sandbox import get_runner

        runner = get_runner({"backend": "e2b", "template": "aoc-python"})

        self.assertIsInstance(runner, E2BRunner)
        self.assertTrue(runner.runs_setup)
        self.assertEqual(runner._template, "aoc-python")


if __name__ == "__main__":
    unittest.main()
