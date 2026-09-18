"""The gates that must hold before a tick is allowed to spend anything.

Preflight exists to convert an expensive, late, confusing failure into a cheap,
early, actionable one. Two properties carry that:

1. **Order.** The free local checks run before anything that touches the
   network, and a failing gate stops the ones after it. If that inverts, a
   missing tool costs a round trip to GitHub before anyone notices.
2. **Naming.** A failure says which tool or which credential, because the
   original symptom this replaced was "Deterministic tester gate failed: pr_url
   is missing from state" — true, and useless.

If these tests go quiet, the pipeline still works; it just stops failing
cheaply, and that regression is invisible until a run burns a full
implementation before dying at the push.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.util.push_identity import PushIdentity
from graphs.coding.utils.preflight import (
    WORKER_AGENT_ID,
    check_required_tools,
    preflight_tick,
)


def _tool(name):
    """A stand-in for a loaded tool; only `.name` is read."""
    tool = MagicMock()
    tool.name = name
    return tool


def _identity(login="build-bot"):
    return PushIdentity(login=login, email=f"{login}@users.noreply.github.com", token="t0ken")


class ToolCheckTestCase(unittest.TestCase):
    """`check_required_tools` imports its collaborators lazily, inside the
    function, so they have to be patched where they are defined rather than on
    the preflight module."""

    def patch_roster(self, tool_names, side_effect=None):
        loader = MagicMock()
        if side_effect is not None:
            loader.get_tools.side_effect = side_effect
        else:
            loader.get_tools.return_value = [_tool(n) for n in tool_names]

        loader_patch = patch("core.loaders.tools_loader.ToolsLoader", return_value=loader)
        session_patch = patch("core.runtime.session_manager.SessionManager.get_session",
                              return_value=MagicMock())
        self.mock_loader_cls = loader_patch.start()
        self.mock_get_session = session_patch.start()
        self.addCleanup(loader_patch.stop)
        self.addCleanup(session_patch.stop)
        return loader


class TestCheckRequiredTools(ToolCheckTestCase):
    def test_no_required_tools_short_circuits_before_building_a_session(self):
        """A graph that grants nothing must not pay to discover that."""
        loader = self.patch_roster(["bash"])

        ok, message = check_required_tools("coding", [])

        self.assertTrue(ok)
        self.assertEqual(message, "")
        self.mock_get_session.assert_not_called()
        loader.get_tools.assert_not_called()

    def test_a_fully_granted_roster_passes(self):
        self.patch_roster(["bash", "filesystem", "git"])

        ok, message = check_required_tools("coding", ["bash", "git"])

        self.assertTrue(ok)
        self.assertEqual(message, "")

    def test_a_missing_tool_is_named_in_the_failure(self):
        """The whole point of the gate: say which tool, not that something broke."""
        self.patch_roster(["bash"])

        ok, message = check_required_tools("coding", ["bash", "filesystem"])

        self.assertFalse(ok)
        self.assertIn("filesystem", message)
        self.assertIn(WORKER_AGENT_ID, message)
        self.assertIn("coding", message)

    def test_every_missing_tool_is_listed_not_just_the_first(self):
        """Reporting one at a time turns one fix into three round trips."""
        self.patch_roster(["bash"])

        ok, message = check_required_tools("coding", ["filesystem", "git", "gh"])

        self.assertFalse(ok)
        for name in ("filesystem", "git", "gh"):
            self.assertIn(name, message)

    def test_the_failure_reports_what_the_worker_actually_has(self):
        """Without the actual roster the reader cannot tell a typo from a real gap."""
        self.patch_roster(["bash", "git"])

        _, message = check_required_tools("coding", ["filesystem"])

        self.assertIn("bash", message)
        self.assertIn("git", message)

    def test_an_empty_roster_is_described_rather_than_left_blank(self):
        self.patch_roster([])

        ok, message = check_required_tools("coding", ["bash"])

        self.assertFalse(ok)
        self.assertIn("nothing", message)

    def test_a_loader_explosion_becomes_a_failed_gate_not_a_crash(self):
        """Preflight runs inside a scheduled tick; raising here would post a
        traceback every five minutes instead of one diagnosable line."""
        self.patch_roster([], side_effect=RuntimeError("registry is corrupt"))

        ok, message = check_required_tools("coding", ["bash"])

        self.assertFalse(ok)
        self.assertIn("registry is corrupt", message)

    def test_the_session_is_stateless_and_scoped_to_the_graph(self):
        """A preflight probe must not create or mutate a durable session, and it
        has to ask under the graph whose grants it is checking."""
        self.patch_roster(["bash"])

        check_required_tools("content_creation", ["bash"])

        kwargs = self.mock_get_session.call_args.kwargs
        self.assertTrue(kwargs["stateless"])
        self.assertEqual(kwargs["graph_id"], "content_creation")
        self.assertEqual(kwargs["agent_id"], WORKER_AGENT_ID)


class TestPreflightTick(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tools = patch("graphs.coding.utils.preflight.check_required_tools",
                           return_value=(True, ""))
        self.identity = patch("graphs.coding.utils.preflight.resolve_push_identity",
                              return_value=(_identity(), ""))
        self.access = patch("graphs.coding.utils.preflight.git_ops.preflight_push_access",
                            AsyncMock(return_value=(True, "ok")))
        self.mock_tools = self.tools.start()
        self.mock_identity = self.identity.start()
        self.mock_access = self.access.start()
        self.addCleanup(self.tools.stop)
        self.addCleanup(self.identity.stop)
        self.addCleanup(self.access.stop)

    async def test_all_gates_green_returns_the_identity_to_push_with(self):
        ok, message, identity = await preflight_tick({"slug": "owner/repo"})

        self.assertTrue(ok)
        self.assertEqual(message, "")
        self.assertEqual(identity.login, "build-bot")

    async def test_a_missing_tool_stops_before_the_network(self):
        """Ordering is the contract: the free check must fail first, so a bad
        roster never costs a round trip to GitHub."""
        self.mock_tools.return_value = (False, "Preflight failed: missing `bash`")

        ok, message, identity = await preflight_tick({"slug": "owner/repo"})

        self.assertFalse(ok)
        self.assertIn("bash", message)
        self.assertIsNone(identity)
        self.mock_identity.assert_not_called()
        self.mock_access.assert_not_awaited()

    async def test_a_broken_credential_stops_before_the_network(self):
        self.mock_identity.return_value = (None, "token file is unreadable")

        ok, message, identity = await preflight_tick({"slug": "owner/repo"})

        self.assertFalse(ok)
        self.assertIn("token file is unreadable", message)
        self.assertIsNone(identity)
        self.mock_access.assert_not_awaited()

    async def test_no_machine_user_skips_the_push_probe_and_still_passes(self):
        """Running without a machine user is a supported mode -- the graph then
        pushes as the human. There is no bot credential to verify."""
        self.mock_identity.return_value = (None, "")

        ok, message, identity = await preflight_tick({})

        self.assertTrue(ok)
        self.assertEqual(message, "")
        self.assertIsNone(identity)
        self.mock_access.assert_not_awaited()

    async def test_a_rejected_push_fails_the_tick(self):
        """An expired PAT is the failure this whole module was built to catch
        before the LLM runs, not after the diff exists."""
        self.mock_access.return_value = (False, "bot cannot push to owner/repo")

        ok, message, identity = await preflight_tick({"slug": "owner/repo"})

        self.assertFalse(ok)
        self.assertIn("cannot push", message)
        self.assertIsNone(identity)

    async def test_the_push_probe_is_scoped_to_the_declared_repo_and_cwd(self):
        await preflight_tick({"slug": "owner/repo"}, cwd="/tmp/worktree")

        kwargs = self.mock_access.await_args.kwargs
        self.assertEqual(kwargs["target_repo"], "owner/repo")
        self.assertEqual(kwargs["cwd"], "/tmp/worktree")

    async def test_required_tools_defaults_to_an_empty_list(self):
        """`None` must not reach `check_required_tools`, which iterates it."""
        await preflight_tick({"slug": "owner/repo"}, required_tools=None)

        self.assertEqual(self.mock_tools.call_args.args[1], [])


if __name__ == "__main__":
    unittest.main()
