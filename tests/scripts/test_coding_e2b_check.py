"""The E2B setup check.

Its exit code is the product: 0 must mean "this host can verify coding tasks on
E2B", because E2B is the default backend and a host that can't reach it halts
every task at verify. A false 0 surfaces later as a halted task; a false 1 has
someone chasing a problem that isn't there.

It also prints a credential's whereabouts, so it is exactly where a key could
leak into a terminal: only a masked form may ever be printed.

Everything here runs against a fake sandbox and a fake runner — no test starts
a real sandbox or reads the real key.
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from graphs.coding.utils.sandbox import RunResult

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, "scripts", "coding_e2b_check.py")

KEY = "e2b_thisfullkeymustnevershowup1234"
SCAFFOLDING_SETUP = (
    "rm -rf temp_app && npx --yes create-vite@latest temp_app --template react-ts --no-interactive "
    "&& cp -r temp_app/. . && rm -rf temp_app && npm install vitest jsdom --save-dev"
)
GOOD_PROBE = "\n".join([
    "git=git version 2.39.5", "curl=curl 7.88.1", "tar=tar (GNU tar) 1.34",
    "node=v22.11.0", "npm=10.9.0", "python3=Python 3.11.2", "cpus=2", "mem_mb=2048",
])


def _load_module():
    spec = importlib.util.spec_from_file_location("coding_e2b_check", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, stdout=""):
        self.stdout, self.stderr, self.exit_code = stdout, "", 0


def fake_sandbox_class(probe=GOOD_PROBE, create_error=None):
    class FakeSandbox:
        instances = []

        def __init__(self):
            self.sandbox_id = "sbx_fake01"
            self.killed = False
            self.commands = self
            self.calls = []

        @classmethod
        async def create(cls, **kwargs):
            if create_error:
                raise create_error
            sbx = cls()
            sbx.create_kwargs = kwargs
            cls.instances.append(sbx)
            return sbx

        async def run(self, cmd, timeout=None, **_kwargs):
            self.calls.append(cmd)
            return _Result(probe)

        async def kill(self):
            self.killed = True

    return FakeSandbox


class FakeRunner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def run(self, steps, *, workspace_path, timeout, meta=None):
        self.calls.append({"steps": steps, "meta": meta, "timeout": timeout})
        return self.result


class CheckTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key_file = os.path.join(self.tmp.name, "e2b_api_key")
        with open(self.key_file, "w", encoding="utf-8") as f:
            f.write(KEY + "\n")
        os.chmod(self.key_file, 0o600)

        env = patch.dict(os.environ, {"E2B_API_KEY": "", "CODING_E2B_KEY_FILE": self.key_file,
                                      "CODING_VERIFY_BACKEND": "", "CODING_E2B_TEMPLATE": ""})
        env.start()
        self.addCleanup(env.stop)
        self.manifests = []
        discover = patch("graphs.coding.utils.dag.discover_manifests",
                         side_effect=lambda *_a, **_k: list(self.manifests))
        discover.start()
        self.addCleanup(discover.stop)

        self.module = _load_module()
        self.module.SANDBOX_CLASS = fake_sandbox_class()

    def write_manifest(self, name, **fields):
        folder = os.path.join(self.tmp.name, name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "build_request.json")
        manifest = {"version": "3.0", "project_name": name, "queue": [],
                    "repo": {"slug": f"example/{name}", "default_branch": "main"}}
        manifest.update(fields)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        return path

    async def run_main(self, *argv):
        """Returns (exit_code, stdout)."""
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["coding_e2b_check.py", *argv]), \
             redirect_stdout(out), redirect_stderr(err):
            code = await self.module.main(list(argv))
        return code, out.getvalue() + err.getvalue()


class TestConfiguration(CheckTestCase):
    async def test_a_configured_host_passes_offline(self):
        code, out = await self.run_main("--offline")

        self.assertEqual(code, 0)
        self.assertIn("verify backend on this host: e2b (default)", out)
        self.assertIn("e2b_…1234", out)

    async def test_the_key_is_never_printed_in_full(self):
        _, out = await self.run_main()

        self.assertNotIn(KEY, out)

    async def test_no_key_fails_and_says_where_to_put_it(self):
        os.remove(self.key_file)

        code, out = await self.run_main()

        self.assertEqual(code, 1)
        self.assertIn("No E2B API key", out)
        self.assertEqual(self.module.SANDBOX_CLASS.instances, [], "no key, no sandbox")

    async def test_a_key_in_the_environment_works_but_is_flagged(self):
        """Every subprocess inherits the environment, the worker's bash included."""
        with patch.dict(os.environ, {"E2B_API_KEY": KEY}):
            code, out = await self.run_main("--offline")

        self.assertEqual(code, 0)
        self.assertIn("in the environment", out)
        self.assertNotIn(KEY, out)

    async def test_a_readable_key_file_is_flagged(self):
        os.chmod(self.key_file, 0o644)

        code, out = await self.run_main("--offline")

        self.assertEqual(code, 0)
        self.assertIn("chmod 600", out)

    async def test_a_host_that_opted_out_to_local_is_warned_not_failed(self):
        with patch.dict(os.environ, {"CODING_VERIFY_BACKEND": "local"}):
            code, out = await self.run_main("--offline")

        self.assertEqual(code, 0)
        self.assertIn("local", out)

    async def test_an_unknown_backend_fails(self):
        with patch.dict(os.environ, {"CODING_VERIFY_BACKEND": "lcoal"}):
            code, out = await self.run_main("--offline")

        self.assertEqual(code, 1)
        self.assertIn("lcoal", out)


class TestProjects(CheckTestCase):
    def test_setup_smells(self):
        smells = self.module.setup_smells(SCAFFOLDING_SETUP, self.module.TREE_SMELLS)
        self.assertEqual(len(smells), 3)
        self.assertEqual(self.module.setup_smells("npm ci --no-audit --no-fund", self.module.TREE_SMELLS), [])
        self.assertEqual(len(self.module.setup_smells("npm install && npm test", self.module.TREE_SMELLS)), 1)
        venv_link = "[ -e .venv ] || ln -s \"$(python3 -c 'import sys; print(sys.prefix)')\" .venv"
        self.assertEqual(self.module.setup_smells(venv_link, self.module.TREE_SMELLS), [])
        self.assertEqual(len(self.module.setup_smells(venv_link, self.module.TEMPLATE_SMELLS)), 1)

    async def test_a_scaffolding_setup_is_flagged_for_an_e2b_project(self):
        self.manifests = [self.write_manifest("cards", setup_command=SCAFFOLDING_SETUP)]

        code, out = await self.run_main("--offline")

        self.assertEqual(code, 0, "a warning, not a failure: the host itself is fine")
        self.assertIn("cards: setup_command scaffolds the project", out)
        self.assertIn("npm ci", out)

    async def test_a_project_that_opted_out_is_not_judged_on_its_setup(self):
        self.manifests = [self.write_manifest("cards", setup_command=SCAFFOLDING_SETUP,
                                              environment={"backend": "local"})]

        _, out = await self.run_main("--offline")

        self.assertIn("cards: backend local (manifest)", out)
        self.assertNotIn("scaffolds the project", out)

    async def test_an_unreadable_manifest_fails(self):
        path = self.write_manifest("broken")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.manifests = [path]

        code, out = await self.run_main("--offline")

        self.assertEqual(code, 1)
        self.assertIn("broken", out)


class TestSandbox(CheckTestCase):
    async def test_a_working_sandbox_passes_and_is_killed(self):
        code, out = await self.run_main()

        self.assertEqual(code, 0)
        [sbx] = self.module.SANDBOX_CLASS.instances
        self.assertTrue(sbx.killed)
        self.assertEqual(sbx.create_kwargs["api_key"], KEY)
        self.assertIn("sandbox sbx_fake01 started", out)
        self.assertIn("curl: curl 7.88.1", out)

    async def test_the_configured_template_is_used(self):
        with patch.dict(os.environ, {"CODING_E2B_TEMPLATE": "coding-node"}):
            await self.run_main()

        self.assertEqual(self.module.SANDBOX_CLASS.instances[0].create_kwargs["template"], "coding-node")

    async def test_a_missing_fetch_tool_fails(self):
        self.module.SANDBOX_CLASS = fake_sandbox_class(GOOD_PROBE.replace("curl=curl 7.88.1", "curl=MISSING"))

        code, out = await self.run_main()

        self.assertEqual(code, 1)
        self.assertIn("curl is missing", out)
        self.assertTrue(self.module.SANDBOX_CLASS.instances[0].killed)

    async def test_little_memory_is_a_warning(self):
        self.module.SANDBOX_CLASS = fake_sandbox_class(GOOD_PROBE.replace("mem_mb=2048", "mem_mb=482"))

        code, out = await self.run_main()

        self.assertEqual(code, 0)
        self.assertIn("482 MiB", out)
        self.assertIn("137", out)

    async def test_a_sandbox_that_will_not_start_fails(self):
        self.module.SANDBOX_CLASS = fake_sandbox_class(create_error=RuntimeError("401: invalid API key"))

        code, out = await self.run_main()

        self.assertEqual(code, 1)
        self.assertIn("401: invalid API key", out)

    async def test_offline_starts_no_sandbox(self):
        await self.run_main("--offline")

        self.assertEqual(self.module.SANDBOX_CLASS.instances, [])


class TestSmoke(CheckTestCase):
    async def test_the_projects_setup_and_test_run_against_its_default_branch(self):
        path = self.write_manifest("cards", setup_command="npm ci", environment={"template": "coding-node"})
        runner = FakeRunner(RunResult(0, "14 passed", "", backend="e2b"))
        templates = []
        self.module.MAKE_RUNNER = lambda template: templates.append(template) or runner

        code, out = await self.run_main("--manifest", path, "--verify", "npx vitest run")

        self.assertEqual(code, 0)
        [call] = runner.calls
        self.assertEqual([(s.name, s.command) for s in call["steps"]],
                         [("setup", "npm ci"), ("verify", "npx vitest run")])
        self.assertEqual((call["meta"]["repo"], call["meta"]["head_sha"]), ("example/cards", "main"))
        self.assertEqual(templates, ["coding-node"])
        self.assertIn("setup + verify passed", out)

    async def test_a_ref_can_be_named(self):
        path = self.write_manifest("cards", setup_command="npm ci")
        runner = FakeRunner(RunResult(0, "", "", backend="e2b"))
        self.module.MAKE_RUNNER = lambda _template: runner

        await self.run_main("--manifest", path, "--ref", "abc1234")

        self.assertEqual(runner.calls[0]["meta"]["head_sha"], "abc1234")

    async def test_a_failing_setup_fails_and_shows_why(self):
        path = self.write_manifest("cards", setup_command=SCAFFOLDING_SETUP)
        runner = FakeRunner(RunResult(1, "", "`setup_command` changed or created files that are not ignored\n M package.json",
                                      failed_step="setup", backend="e2b"))
        self.module.MAKE_RUNNER = lambda _template: runner

        code, out = await self.run_main("--manifest", path)

        self.assertEqual(code, 1)
        self.assertIn("`setup` failed", out)
        self.assertIn("M package.json", out)

    async def test_nothing_to_run_is_not_a_failure(self):
        path = self.write_manifest("cards")
        runner = FakeRunner(RunResult(0, "", ""))
        self.module.MAKE_RUNNER = lambda _template: runner

        code, out = await self.run_main("--manifest", path)

        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [])
        self.assertIn("nothing to run", out)

    async def test_a_missing_manifest_fails(self):
        code, out = await self.run_main("--manifest", os.path.join(self.tmp.name, "nope", "build_request.json"))

        self.assertEqual(code, 1)
        self.assertIn("no manifest at", out)

    async def test_verify_without_a_project_is_a_usage_error(self):
        code, out = await self.run_main("--verify", "npm test")

        self.assertEqual(code, 2)
        self.assertIn("--project", out)


if __name__ == "__main__":
    unittest.main()
