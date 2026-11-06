"""The watchers over GitHub's Deployments API.

What has to hold: a deployment is found by commit *and* environment, so a
preview or another environment's deploy never counts; GitHub's states map onto
pending / live / failed, with a superseded deployment counted as having been
live; "couldn't ask GitHub" is `infra_error`, never a failed deploy; and
`github_deployment` refuses to guess an environment, since watching the wrong
one would report a deploy that never happened.
"""
import json
import unittest
from unittest.mock import AsyncMock, patch

from graphs.coding.utils.deploy import DeployTarget
from graphs.coding.utils.deploy import github_deployments
from graphs.coding.utils.deploy.github_deployments import GitHubDeploymentWatcher, GitHubPagesWatcher

SHA = "abc1234def5678"
PAGES = {"backend": "github_pages"}


class WatcherCase(unittest.IsolatedAsyncioTestCase):
    """The watchers against canned GitHub API answers, keyed by the path asked for."""

    def answer(self, deployments=None, statuses=None, error=""):
        async def gh_api(path, push_identity=None):
            if error:
                return None, error
            if "/statuses" in path:
                return statuses if statuses is not None else [], ""
            return deployments if deployments is not None else [], ""

        self.calls = []

        async def recorded(path, push_identity=None):
            self.calls.append(path)
            return await gh_api(path, push_identity)

        patcher = patch.object(github_deployments, "gh_api", side_effect=recorded)
        patcher.start()
        self.addCleanup(patcher.stop)

    def target(self, config=PAGES, repo="org/site"):
        return DeployTarget(repo=repo, sha=SHA, branch="main", config=config)


class TestGitHubPagesWatcher(WatcherCase):
    async def test_no_deployment_yet_is_pending(self):
        self.answer(deployments=[])

        status = await GitHubPagesWatcher().advance(self.target())

        self.assertEqual(status.state, "pending")
        self.assertIn("github-pages", status.detail)
        self.assertIn(f"sha={SHA}", self.calls[0])
        self.assertIn("environment=github-pages", self.calls[0])

    async def test_a_successful_deployment_is_live_at_its_url(self):
        self.answer(deployments=[{"id": 7}],
                    statuses=[{"state": "success", "environment_url": "https://org.github.io/site/",
                               "log_url": "https://github.com/org/site/actions/runs/9"}])

        status = await GitHubPagesWatcher().advance(self.target())

        self.assertEqual((status.state, status.url), ("live", "https://org.github.io/site/"))
        self.assertEqual(status.log_url, "https://github.com/org/site/actions/runs/9")
        self.assertIn("deployments/7/statuses", self.calls[1])

    async def test_the_newest_deployment_of_the_commit_is_the_one_read(self):
        """A re-run of the deploy is a new deployment of the same commit."""
        self.answer(deployments=[{"id": 9}, {"id": 7}], statuses=[{"state": "success"}])

        await GitHubPagesWatcher().advance(self.target())

        self.assertIn("deployments/9/statuses", self.calls[1])

    async def test_a_failed_deployment_says_why(self):
        self.answer(deployments=[{"id": 7}],
                    statuses=[{"state": "failure", "description": "Deployment failed"}])

        status = await GitHubPagesWatcher().advance(self.target())

        self.assertEqual((status.state, status.detail), ("failed", "Deployment failed"))

    async def test_an_error_state_is_a_failed_deployment(self):
        self.answer(deployments=[{"id": 7}], statuses=[{"state": "error"}])

        status = await GitHubPagesWatcher().advance(self.target())

        self.assertEqual(status.state, "failed")
        self.assertIn("error", status.detail)

    async def test_a_deployment_in_progress_is_pending(self):
        self.answer(deployments=[{"id": 7}], statuses=[{"state": "in_progress"}])

        status = await GitHubPagesWatcher().advance(self.target())

        self.assertEqual(status.state, "pending")
        self.assertIn("in_progress", status.detail)

    async def test_a_deployment_with_no_status_yet_is_pending(self):
        self.answer(deployments=[{"id": 7}], statuses=[])

        status = await GitHubPagesWatcher().advance(self.target())

        self.assertEqual(status.state, "pending")
        self.assertIsNone(status.infra_error)

    async def test_a_superseded_deployment_was_live(self):
        self.answer(deployments=[{"id": 7}], statuses=[{"state": "inactive"}])

        status = await GitHubPagesWatcher().advance(self.target())

        self.assertEqual(status.state, "live")

    async def test_an_api_failure_is_infra_not_a_failed_deploy(self):
        self.answer(error="HTTP 502")

        status = await GitHubPagesWatcher().advance(self.target())

        self.assertEqual(status.state, "pending")
        self.assertIn("HTTP 502", status.infra_error)

    async def test_the_url_falls_back_to_the_pages_address(self):
        self.answer(deployments=[{"id": 7}], statuses=[{"state": "success"}])

        project_site = await GitHubPagesWatcher().advance(self.target(repo="Org/site"))
        user_site = await GitHubPagesWatcher().advance(self.target(repo="org/org.github.io"))
        configured = await GitHubPagesWatcher().advance(
            self.target(config={"backend": "github_pages", "url": "https://cards.example.com/"}))

        self.assertEqual(project_site.url, "https://org.github.io/site/")
        self.assertEqual(user_site.url, "https://org.github.io/")
        self.assertEqual(configured.url, "https://cards.example.com/")


class TestGitHubDeploymentWatcher(WatcherCase):
    async def test_it_watches_the_configured_environment(self):
        """Fly.io deployed by an Actions job with `environment: production` lands here."""
        self.answer(deployments=[{"id": 3}], statuses=[{"state": "success",
                                                        "environment_url": "https://site.fly.dev"}])

        status = await GitHubDeploymentWatcher().advance(
            self.target(config={"backend": "github_deployment", "environment": "production"}))

        self.assertEqual((status.state, status.url), ("live", "https://site.fly.dev"))
        self.assertIn("environment=production", self.calls[0])

    async def test_without_an_environment_it_fails_as_config(self):
        self.answer()

        status = await GitHubDeploymentWatcher().advance(self.target(config={"backend": "github_deployment"}))

        self.assertEqual(status.state, "failed")
        self.assertIn("deploy.environment", status.detail)
        self.assertEqual(self.calls, [])

    async def test_the_environment_name_is_url_encoded(self):
        self.answer()

        await GitHubDeploymentWatcher().advance(
            self.target(config={"backend": "github_deployment", "environment": "prod eu"}))

        self.assertIn("environment=prod%20eu", self.calls[0])


class TestBranchHead(unittest.IsolatedAsyncioTestCase):
    async def test_it_reads_the_branchs_commit(self):
        with patch.object(github_deployments, "gh_api", AsyncMock(return_value=({"sha": "f00d"}, ""))) as api:
            self.assertEqual(await github_deployments.branch_head("org/site", "main"), ("f00d", ""))
        self.assertEqual(api.await_args.args[0], "repos/org/site/commits/main")

    async def test_an_error_is_returned_not_raised(self):
        with patch.object(github_deployments, "gh_api", AsyncMock(return_value=(None, "HTTP 404"))):
            self.assertEqual(await github_deployments.branch_head("org/site", "main"), ("", "HTTP 404"))

    async def test_an_answer_without_a_commit_is_an_error(self):
        with patch.object(github_deployments, "gh_api", AsyncMock(return_value=({}, ""))):
            sha, error = await github_deployments.branch_head("org/site", "main")
        self.assertEqual(sha, "")
        self.assertIn("main", error)


class TestGhApi(unittest.IsolatedAsyncioTestCase):
    """`gh api` through the same runner as every other GitHub call, never raising."""

    def run_cmd(self, code, out="", err=""):
        patcher = patch.object(github_deployments.git_ops, "run_cmd_async",
                               AsyncMock(return_value=(code, out, err)))
        self.addCleanup(patcher.stop)
        return patcher.start()

    async def test_an_answer_is_parsed(self):
        run = self.run_cmd(0, json.dumps([{"id": 7}]))

        data, error = await github_deployments.gh_api("repos/org/site/deployments")

        self.assertEqual((data, error), ([{"id": 7}], ""))
        self.assertEqual(run.await_args.args[0][:2], ["gh", "api"])
        self.assertEqual(run.await_args.args[0][-1], "repos/org/site/deployments")

    async def test_a_failed_call_returns_its_error(self):
        self.run_cmd(1, err="HTTP 404: Not Found\n")

        data, error = await github_deployments.gh_api("repos/org/missing/deployments")

        self.assertIsNone(data)
        self.assertEqual(error, "HTTP 404: Not Found")

    async def test_an_unreadable_answer_is_an_error(self):
        self.run_cmd(0, "<html>rate limited</html>")

        data, error = await github_deployments.gh_api("repos/org/site/deployments")

        self.assertIsNone(data)
        self.assertIn("unreadable", error)


if __name__ == "__main__":
    unittest.main()
