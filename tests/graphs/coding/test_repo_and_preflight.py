import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.provisioner import provisioner_node
from graphs.coding.utils.repo import (
    DEFAULT_REPO_DESCRIPTOR,
    get_push_identity,
    get_repo_descriptor,
    resolve_push_identity,
)


class TestRepoDescriptor(unittest.TestCase):
    def test_defaults_when_absent(self):
        descriptor = get_repo_descriptor({})
        self.assertEqual(descriptor, DEFAULT_REPO_DESCRIPTOR)
        self.assertIsNone(descriptor["push_identity"])

    def test_reads_manifest_block(self):
        descriptor = get_repo_descriptor({
            "repo": {"mode": "existing", "slug": "owner/repo", "push_identity": "bot"}
        })
        self.assertEqual(descriptor["mode"], "existing")
        self.assertEqual(descriptor["slug"], "owner/repo")
        self.assertEqual(descriptor["push_identity"], "bot")
        self.assertEqual(descriptor["default_branch"], "main")

    def test_legacy_target_repo_still_honoured(self):
        descriptor = get_repo_descriptor({"target_repo": "owner/legacy"})
        self.assertEqual(descriptor["slug"], "owner/legacy")


class TestResolvePushIdentity(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.token_path = os.path.join(self.tmpdir, "bot_token")
        with open(self.token_path, "w", encoding="utf-8") as f:
            f.write("ghp_secret")
        os.chmod(self.token_path, 0o600)

    def test_no_identity_configured_is_not_an_error(self):
        identity, error = resolve_push_identity({"slug": "owner/repo"})
        self.assertIsNone(identity)
        self.assertEqual(error, "")

    def test_configured_and_readable(self):
        with patch.dict(os.environ, {"AOC_BOT_TOKEN_FILE": self.token_path}):
            identity, error = resolve_push_identity({"push_identity": "bot"})
        self.assertEqual(error, "")
        self.assertEqual(identity.login, "bot")

    def test_configured_but_token_missing_returns_reason(self):
        # Falling back to ambient credentials here would open a PR the reviewer
        # cannot approve, so this must surface as an error, not a None.
        with patch.dict(os.environ, {"AOC_BOT_TOKEN_FILE": os.path.join(self.tmpdir, "gone")}):
            identity, error = resolve_push_identity({"push_identity": "bot"})
        self.assertIsNone(identity)
        self.assertIn("not found", error)

    def test_get_push_identity_reads_state(self):
        with patch.dict(os.environ, {"AOC_BOT_TOKEN_FILE": self.token_path}):
            identity = get_push_identity({"repo": {"push_identity": "bot"}})
        self.assertEqual(identity.login, "bot")


class TestProvisionerPreflight(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.token_path = os.path.join(self.tmpdir, "bot_token")
        with open(self.token_path, "w", encoding="utf-8") as f:
            f.write("ghp_secret")
        os.chmod(self.token_path, 0o600)
        self.state = {
            "run_id": "run_TEST",
            "project_name": "proj",
            "current_task": {"task_id": "T-1", "feature_name": "auth", "project_name": "proj"},
            "repo": {"slug": "owner/repo", "push_identity": "bot"}
        }

    @patch('graphs.coding.nodes.provisioner.git_ops.provision_worktree', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.provisioner.git_ops.preflight_push_access', new_callable=AsyncMock)
    async def test_halts_before_provisioning_when_bot_cannot_push(self, mock_preflight, mock_provision):
        mock_preflight.return_value = (False, "Preflight failed: `bot` has no push permission on owner/repo.")
        with patch.dict(os.environ, {"AOC_BOT_TOKEN_FILE": self.token_path}):
            res = await provisioner_node(self.state)

        self.assertIn("no push permission", res["error_message"])
        # Nothing is provisioned and, crucially, the worker never runs.
        mock_provision.assert_not_called()

    @patch('graphs.coding.nodes.provisioner.git_ops.provision_worktree', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.provisioner.git_ops.preflight_push_access', new_callable=AsyncMock)
    async def test_missing_token_halts_without_touching_github(self, mock_preflight, mock_provision):
        with patch.dict(os.environ, {"AOC_BOT_TOKEN_FILE": os.path.join(self.tmpdir, "gone")}):
            res = await provisioner_node(self.state)

        self.assertIn("Preflight failed", res["error_message"])
        mock_preflight.assert_not_called()
        mock_provision.assert_not_called()

    @patch('graphs.coding.nodes.provisioner.git_ops.provision_worktree', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.provisioner.git_ops.preflight_push_access', new_callable=AsyncMock)
    async def test_proceeds_when_preflight_passes(self, mock_preflight, mock_provision):
        mock_preflight.return_value = (True, "Preflight OK")
        mock_provision.return_value = (True, "provisioned")
        with patch.dict(os.environ, {"AOC_BOT_TOKEN_FILE": self.token_path}):
            res = await provisioner_node(self.state)

        self.assertEqual(res["error_message"], "")
        mock_provision.assert_called_once()

    @patch('graphs.coding.nodes.provisioner.git_ops.provision_worktree', new_callable=AsyncMock)
    @patch('graphs.coding.nodes.provisioner.git_ops.preflight_push_access', new_callable=AsyncMock)
    async def test_no_machine_user_skips_the_network_check(self, mock_preflight, mock_provision):
        mock_provision.return_value = (True, "provisioned")
        state = dict(self.state)
        state.pop("repo")
        res = await provisioner_node(state)

        self.assertEqual(res["error_message"], "")
        mock_preflight.assert_not_called()
        mock_provision.assert_called_once()


if __name__ == "__main__":
    unittest.main()
