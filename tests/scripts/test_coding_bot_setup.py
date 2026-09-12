"""The setup checker for the machine user the graph pushes as.

This script is the last thing standing between a misconfigured bot account and a
pipeline that fails halfway through its first real run. Its exit code is the
whole product: 0 must mean "the graph can push and open PRs as this account",
and nothing weaker. A false 0 here is expensive, because the next failure
surfaces after an LLM has already written a diff.

The other contract is secrecy. `push_identity` deliberately hides the token from
`repr()`; this script prints an identity summary, so it is exactly the place a
token would leak into a terminal or a scrollback buffer.
"""
import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, MagicMock, patch

from core.util.push_identity import PushIdentity

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, "scripts", "coding_bot_setup.py")

SECRET = "ghp_thismustnevershowup"


def _load_module():
    spec = importlib.util.spec_from_file_location("coding_bot_setup", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BotSetupTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = _load_module()
        self.identity = PushIdentity(
            login="build-bot", email="build-bot@users.noreply.github.com", token=SECRET
        )
        self.descriptor = {"slug": "owner/repo", "push_identity": "build-bot"}

        self.module.resolve_manifest_path = MagicMock(return_value="/tmp/build_request.json")
        self.module.load_manifest = MagicMock(return_value={})
        self.module.get_repo_descriptor = MagicMock(side_effect=lambda m: dict(self.descriptor))
        self.module.resolve_push_identity = MagicMock(return_value=(self.identity, ""))
        self.module.git_ops = MagicMock()
        self.module.git_ops.preflight_push_access = AsyncMock(return_value=(True, "bot can push"))
        self.module.git_ops.ensure_label = AsyncMock(return_value=(True, "label `approved` exists"))
        self.module.git_ops.discover_target_repo = AsyncMock(return_value="owner/repo")

    async def run_main(self, *argv):
        """Returns (exit_code, stdout)."""
        buffer = io.StringIO()
        with patch.object(sys, "argv", ["coding_bot_setup.py", *argv]), redirect_stdout(buffer):
            code = await self.module.main()
        return code, buffer.getvalue()


class TestExitCodes(BotSetupTestCase):
    async def test_a_fully_configured_bot_exits_zero(self):
        code, out = await self.run_main()

        self.assertEqual(code, 0)
        self.assertIn("build-bot", out)

    async def test_no_machine_user_exits_one_with_instructions(self):
        """The failure has to be actionable: this is the state a first-time user
        is in, and 'not configured' alone does not tell them what to write."""
        self.descriptor = {"slug": "owner/repo"}

        code, out = await self.run_main()

        self.assertEqual(code, 1)
        self.assertIn("push_identity", out)

    async def test_an_unresolvable_credential_exits_one(self):
        self.module.resolve_push_identity.return_value = (None, "token file is not readable")

        code, out = await self.run_main()

        self.assertEqual(code, 1)
        self.assertIn("token file is not readable", out)

    async def test_a_bot_that_cannot_push_exits_one(self):
        """The single most important check -- an expired PAT looks fine locally."""
        self.module.git_ops.preflight_push_access.return_value = (False, "403 from GitHub")

        code, out = await self.run_main()

        self.assertEqual(code, 1)
        self.assertIn("403 from GitHub", out)

    async def test_a_failed_label_creation_exits_one(self):
        """`approved` is one of the four approval signals; without it that whole
        path is silently dead."""
        self.module.git_ops.ensure_label.return_value = (False, "insufficient scope")

        code, out = await self.run_main()

        self.assertEqual(code, 1)
        self.assertIn("insufficient scope", out)


class TestOrdering(BotSetupTestCase):
    async def test_it_does_not_probe_github_without_an_identity(self):
        self.descriptor = {"slug": "owner/repo"}

        await self.run_main()

        self.module.git_ops.preflight_push_access.assert_not_awaited()

    async def test_it_does_not_create_a_label_when_the_bot_cannot_push(self):
        """Creating a label with a credential that has already failed just
        produces a second, more confusing error."""
        self.module.git_ops.preflight_push_access.return_value = (False, "403 from GitHub")

        await self.run_main()

        self.module.git_ops.ensure_label.assert_not_awaited()


class TestOverrides(BotSetupTestCase):
    async def test_login_overrides_the_manifest(self):
        """So the setup can be checked against a candidate account before the
        manifest is edited."""
        await self.run_main("--login", "other-bot")

        descriptor = self.module.resolve_push_identity.call_args.args[0]
        self.assertEqual(descriptor["push_identity"], "other-bot")

    async def test_repo_overrides_the_manifest(self):
        await self.run_main("--repo", "someone/else")

        descriptor = self.module.resolve_push_identity.call_args.args[0]
        self.assertEqual(descriptor["slug"], "someone/else")
        self.assertEqual(
            self.module.git_ops.preflight_push_access.await_args.kwargs["target_repo"],
            "someone/else"
        )

    async def test_no_label_skips_label_creation_but_still_verifies_push(self):
        code, _ = await self.run_main("--no-label")

        self.assertEqual(code, 0)
        self.module.git_ops.ensure_label.assert_not_awaited()
        self.module.git_ops.preflight_push_access.assert_awaited_once()

    async def test_a_missing_slug_is_discovered_from_the_git_remote(self):
        """Declaring the slug is optional; the label still has to land somewhere."""
        self.descriptor = {"push_identity": "build-bot"}

        code, _ = await self.run_main()

        self.assertEqual(code, 0)
        self.module.git_ops.discover_target_repo.assert_awaited_once()
        self.assertEqual(
            self.module.git_ops.ensure_label.await_args.kwargs["target_repo"], "owner/repo"
        )


class TestTokenSecrecy(BotSetupTestCase):
    async def test_the_token_is_never_printed_on_success(self):
        _, out = await self.run_main()

        self.assertNotIn(SECRET, out)

    async def test_the_token_is_never_printed_on_failure(self):
        """Error paths are where secrets usually escape, because the natural fix
        is to dump more context."""
        self.module.git_ops.preflight_push_access.return_value = (False, "403 from GitHub")

        _, out = await self.run_main()

        self.assertNotIn(SECRET, out)


if __name__ == "__main__":
    unittest.main()
