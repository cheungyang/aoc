import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.provisioner import provisioner_node
from graphs.coding.utils.repo import (
    DEFAULT_REPO_DESCRIPTOR,
    cache_path_for,
    ensure_repo_available,
    get_push_identity,
    get_repo_descriptor,
    project_root,
    resolve_push_identity,
    resolve_repo_root,
)
from core.util import git_ops


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


class TestProjectRoot(unittest.TestCase):
    def test_the_root_is_the_repo_not_a_package_inside_it(self):
        # This was off by one directory and every worktree was provisioned
        # against `graphs/`, which is not a git repository.
        root = project_root()

        self.assertTrue(os.path.isdir(os.path.join(root, "graphs")))
        self.assertTrue(os.path.isdir(os.path.join(root, "core")))

    def test_the_root_does_not_depend_on_the_working_directory(self):
        original = os.getcwd()
        self.addCleanup(os.chdir, original)

        before = project_root()
        os.chdir(tempfile.mkdtemp())

        self.assertEqual(project_root(), before)


class TestRepoModes(unittest.IsolatedAsyncioTestCase):
    async def test_self_mode_uses_this_checkout(self):
        root, error = await ensure_repo_available({"mode": "self"})

        self.assertEqual(error, "")
        self.assertEqual(root, project_root())

    async def test_the_mode_defaults_to_self(self):
        root, error = await ensure_repo_available({})

        self.assertEqual(error, "")
        self.assertEqual(root, project_root())

    async def test_an_unknown_mode_is_an_error_not_a_silent_fallback(self):
        root, error = await ensure_repo_available({"mode": "teleport"})

        self.assertEqual(root, "")
        self.assertIn("teleport", error)

    async def test_existing_mode_without_a_slug_is_refused(self):
        root, error = await ensure_repo_available({"mode": "existing"})

        self.assertEqual(root, "")
        self.assertIn("slug", error)

    async def test_a_slug_that_is_not_owner_slash_repo_is_refused(self):
        root, error = await ensure_repo_available({"mode": "existing", "slug": "justrepo"})

        self.assertEqual(root, "")
        self.assertIn("slug", error)

    async def test_existing_mode_clones_when_the_cache_is_empty(self):
        cache = os.path.join(tempfile.mkdtemp(), "owner__repo")

        with patch("graphs.coding.utils.repo.cache_path_for", return_value=cache), \
             patch("core.util.git_ops.run_cmd_async",
                   new=AsyncMock(return_value=(0, "", ""))) as mock_run:
            root, error = await ensure_repo_available({"mode": "existing", "slug": "owner/repo"})

        self.assertEqual(error, "")
        self.assertEqual(root, cache)
        self.assertIn("clone", mock_run.await_args.args[0])

    async def test_existing_mode_fetches_instead_of_recloning(self):
        # A tick runs every five minutes; re-cloning each time would be both
        # slow and destructive of any local state.
        cache = tempfile.mkdtemp()
        os.makedirs(os.path.join(cache, ".git"))

        with patch("graphs.coding.utils.repo.cache_path_for", return_value=cache), \
             patch("core.util.git_ops.run_cmd_async",
                   new=AsyncMock(return_value=(0, "", ""))) as mock_run:
            root, error = await ensure_repo_available({"mode": "existing", "slug": "owner/repo"})

        self.assertEqual(root, cache)
        commands = [c.args[0] for c in mock_run.await_args_list]
        self.assertTrue(any("fetch" in cmd for cmd in commands))
        self.assertFalse(any("clone" in cmd for cmd in commands))

    async def test_a_failed_fetch_is_not_fatal(self):
        # The clone is still usable, just a few commits behind.
        cache = tempfile.mkdtemp()
        os.makedirs(os.path.join(cache, ".git"))

        with patch("graphs.coding.utils.repo.cache_path_for", return_value=cache), \
             patch("core.util.git_ops.run_cmd_async",
                   new=AsyncMock(return_value=(1, "", "no network"))):
            root, error = await ensure_repo_available({"mode": "existing", "slug": "owner/repo"})

        self.assertEqual(root, cache)
        self.assertEqual(error, "")

    async def test_a_failed_clone_is_reported(self):
        cache = os.path.join(tempfile.mkdtemp(), "owner__repo")

        with patch("graphs.coding.utils.repo.cache_path_for", return_value=cache), \
             patch("core.util.git_ops.run_cmd_async",
                   new=AsyncMock(return_value=(1, "", "repository not found"))):
            root, error = await ensure_repo_available({"mode": "existing", "slug": "owner/repo"})

        self.assertEqual(root, "")
        self.assertIn("repository not found", error)

    async def test_create_mode_skips_creation_when_the_repo_already_exists(self):
        cache = tempfile.mkdtemp()
        os.makedirs(os.path.join(cache, ".git"))
        # `gh repo view` succeeding means it is already there.
        with patch("graphs.coding.utils.repo.cache_path_for", return_value=cache), \
             patch("core.util.git_ops.run_cmd_async",
                   new=AsyncMock(return_value=(0, "", ""))) as mock_run:
            root, error = await ensure_repo_available({"mode": "create", "slug": "owner/repo"})

        self.assertEqual(error, "")
        commands = [c.args[0] for c in mock_run.await_args_list]
        self.assertFalse(any("create" in cmd for cmd in commands))

    async def test_create_mode_creates_the_repo_when_it_is_missing(self):
        cache = os.path.join(tempfile.mkdtemp(), "owner__repo")
        # First call is `gh repo view` and must fail; everything after succeeds.
        results = [(1, "", "not found")] + [(0, "", "")] * 5

        with patch("graphs.coding.utils.repo.cache_path_for", return_value=cache), \
             patch("core.util.git_ops.run_cmd_async",
                   new=AsyncMock(side_effect=results)) as mock_run:
            root, error = await ensure_repo_available({"mode": "create", "slug": "owner/repo"})

        self.assertEqual(error, "")
        create_cmd = mock_run.await_args_list[1].args[0]
        self.assertIn("create", create_cmd)
        # Without a seed commit there is no default branch to base work on.
        self.assertIn("--add-readme", create_cmd)

    async def test_create_mode_defaults_to_private(self):
        cache = os.path.join(tempfile.mkdtemp(), "owner__repo")
        results = [(1, "", "not found")] + [(0, "", "")] * 5

        with patch("graphs.coding.utils.repo.cache_path_for", return_value=cache), \
             patch("core.util.git_ops.run_cmd_async",
                   new=AsyncMock(side_effect=results)) as mock_run:
            await ensure_repo_available({"mode": "create", "slug": "owner/repo"})

        self.assertIn("--private", mock_run.await_args_list[1].args[0])

    async def test_a_failed_creation_stops_before_cloning(self):
        results = [(1, "", "not found"), (1, "", "name already taken")]

        with patch("core.util.git_ops.run_cmd_async", new=AsyncMock(side_effect=results)):
            root, error = await ensure_repo_available({"mode": "create", "slug": "owner/repo"})

        self.assertEqual(root, "")
        self.assertIn("name already taken", error)


class TestResolveRepoRoot(unittest.TestCase):
    def test_self_mode_resolves_without_the_network(self):
        self.assertEqual(resolve_repo_root({"mode": "self"}), project_root())

    def test_a_managed_clone_resolves_to_its_cache_path(self):
        root = resolve_repo_root({"mode": "existing", "slug": "owner/repo"})

        self.assertEqual(root, cache_path_for("owner/repo"))

    def test_an_unusable_descriptor_resolves_to_nothing(self):
        self.assertEqual(resolve_repo_root({"mode": "existing"}), "")

    def test_the_cache_path_is_one_directory_deep(self):
        path = cache_path_for("owner/repo")

        self.assertTrue(path.endswith("owner__repo"))
        self.assertEqual(os.path.basename(os.path.dirname(path)), "repos")


class TestRepoLocking(unittest.IsolatedAsyncioTestCase):
    async def test_the_same_repo_gets_the_same_lock(self):
        # `git worktree` and `git fetch` contend on .git/index.lock, so two
        # ticks against one repo must queue rather than fail.
        path = tempfile.mkdtemp()

        self.assertIs(git_ops.repo_lock(path), git_ops.repo_lock(path))

    async def test_the_lock_is_keyed_by_the_real_path(self):
        # A worktree reached via a symlink is still the same repository.
        real = tempfile.mkdtemp()
        link = os.path.join(tempfile.mkdtemp(), "link")
        os.symlink(real, link)

        self.assertIs(git_ops.repo_lock(real), git_ops.repo_lock(link))

    async def test_different_repos_get_different_locks(self):
        self.assertIsNot(
            git_ops.repo_lock(tempfile.mkdtemp()),
            git_ops.repo_lock(tempfile.mkdtemp())
        )

    async def test_provisioning_holds_the_repo_lock(self):
        repo = tempfile.mkdtemp()
        workspace = os.path.join(tempfile.mkdtemp(), "ws")
        held = []

        async def record(*args, **kwargs):
            held.append(git_ops.repo_lock(repo).locked())
            os.makedirs(workspace, exist_ok=True)
            return (0, "", "")

        with patch("core.util.git_ops.run_cmd_async", new=AsyncMock(side_effect=record)):
            await git_ops.provision_worktree(repo, workspace, "feat/x", base_ref="origin/main")

        self.assertTrue(all(held), "worktree commands ran outside the repo lock")


class TestProvisionHasNoHeadFallback(unittest.IsolatedAsyncioTestCase):
    async def test_a_missing_base_ref_fails_instead_of_branching_from_head(self):
        # F3: falling back to HEAD builds the work on whatever happened to be
        # checked out, and the result looks successful.
        repo = tempfile.mkdtemp()
        workspace = os.path.join(tempfile.mkdtemp(), "ws")

        async def fail_worktree_add(cmd, *args, **kwargs):
            if "worktree" in cmd and "add" in cmd:
                return (1, "", "invalid reference: origin/gone")
            return (0, "", "")

        with patch("core.util.git_ops.run_cmd_async",
                   new=AsyncMock(side_effect=fail_worktree_add)) as mock_run:
            success, message = await git_ops.provision_worktree(
                repo, workspace, "feat/x", base_ref="origin/gone"
            )

        self.assertFalse(success)
        self.assertIn("origin/gone", message)
        adds = [c.args[0] for c in mock_run.await_args_list
                if "worktree" in c.args[0] and "add" in c.args[0]]
        self.assertEqual(len(adds), 1, "a second attempt means the HEAD fallback is back")


if __name__ == "__main__":
    unittest.main()
