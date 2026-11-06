"""The deploy node: one deployment per project, following the default branch.

What has to hold:

- a merged task is `done` whatever its deploy does, and a deploy is asked about
  at most once per tick, so it can neither hold the queue nor loop it;
- only changes are reported — a build still running every five minutes is not
  news — and a commit that never goes live times out instead of keeping ticks
  coming forever;
- a slow answer about an older commit never overwrites a newer request;
- "couldn't find out" (GitHub unreachable) is not "the deploy failed";
- the destination is a backend behind one interface, so adding Fly.io is a
  class, not a change to the node.

The watchers, the factory and the manifest helpers have their own test modules
(test_github_deployments.py, test_deploy_factory.py, test_deploy_types.py,
test_manifest.py).
"""
import time
import unittest
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.deploy import DEFAULT_TIMEOUT_MINUTES, deploy_node
from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.deploy import BACKENDS, DeployStatus
from tests.graphs.coding.fixtures import ManifestFixture

SHA = "abc1234def5678"
PAGES = {"backend": "github_pages"}
REPO = {"slug": "org/site", "default_branch": "main"}


class DeployNodeCase(ManifestFixture):
    def setUp(self):
        super().setUp()
        self.deployer = AsyncMock()
        self.deployer.advance = AsyncMock(return_value=DeployStatus("pending", detail="building"))
        patch("graphs.coding.nodes.deploy.get_deployer", return_value=self.deployer).start()
        patch("graphs.coding.nodes.deploy.get_push_identity", return_value=None).start()
        self.head = patch("graphs.coding.nodes.deploy.branch_head",
                          AsyncMock(return_value=("headsha9876543", ""))).start()
        self.addCleanup(patch.stopall)

    def write(self, deployment=None, deploy=PAGES, **fields):
        manifest = {"deploy": deploy, "repo": REPO}
        if deployment is not None:
            manifest["deployment"] = deployment
        manifest.update(fields)
        self.write_manifest([], **manifest)

    def pending(self, **fields):
        deployment = {"status": "pending", "backend": "github_pages", "target_sha": SHA,
                      "requested_at": time.time(), "checks": 0}
        deployment.update(fields)
        return deployment

    def deployment(self):
        return manifest_store.load_manifest(self.manifest_path).get("deployment") or {}

    async def run_node(self):
        return await deploy_node({"build_request_path": self.manifest_path, "tick_report": []})


class TestNothingToDo(DeployNodeCase):
    async def test_a_project_without_a_deploy_block_is_left_alone(self):
        self.write(deployment=self.pending(), deploy=None)

        result = await self.run_node()

        self.deployer.advance.assert_not_awaited()
        self.assertEqual((result["route"], result["deploy_checked"]), ("scheduler", True))

    async def test_a_deployment_that_is_not_pending_is_not_asked_about(self):
        for status in ("live", "failed"):
            with self.subTest(status=status):
                self.write(deployment=self.pending(status=status))
                await self.run_node()
        self.deployer.advance.assert_not_awaited()


class TestOutcomes(DeployNodeCase):
    async def test_the_first_check_of_a_pending_deploy_says_so_once(self):
        self.write(deployment=self.pending())

        first = await self.run_node()
        second = await self.run_node()

        self.assertEqual(first["tick_report"], ["🚚 `demo`: deploying `abc1234` to GitHub Pages."])
        self.assertEqual(second["tick_report"], [], "still building is not news")
        self.assertEqual(self.deployment()["checks"], 2)
        self.assertEqual(self.deployment()["detail"], "building")

    async def test_the_backend_is_asked_about_the_requested_commit(self):
        self.write(deployment=self.pending())

        await self.run_node()

        target = self.deployer.advance.await_args.args[0]
        self.assertEqual((target.repo, target.sha, target.branch), ("org/site", SHA, "main"))
        self.assertEqual(target.config, PAGES)

    async def test_live_is_recorded_and_reported(self):
        self.write(deployment=self.pending(checks=3))
        self.deployer.advance.return_value = DeployStatus(
            "live", url="https://org.github.io/site/", log_url="https://github.com/org/site/actions/runs/9")

        result = await self.run_node()

        stored = self.deployment()
        self.assertEqual((stored["status"], stored["deployed_sha"]), ("live", SHA))
        self.assertEqual(stored["url"], "https://org.github.io/site/")
        self.assertIsNone(stored["last_error"])
        self.assertEqual(result["tick_report"], ["🌐 `demo` is live at `abc1234`: https://org.github.io/site/"])

    async def test_a_failed_deploy_is_recorded_for_the_planner_with_its_log(self):
        self.write(deployment=self.pending())
        self.deployer.advance.return_value = DeployStatus(
            "failed", detail="build failed", log_url="https://github.com/org/site/actions/runs/9")

        result = await self.run_node()

        stored = self.deployment()
        self.assertEqual(stored["status"], "failed")
        self.assertEqual((stored["last_error"]["stage"], stored["last_error"]["kind"]), ("deploy", "deploy"))
        self.assertIn("build failed", stored["last_error"]["message"])
        self.assertEqual(stored["log_url"], "https://github.com/org/site/actions/runs/9")
        self.assertIn("actions/runs/9", result["tick_report"][0])
        self.assertEqual(result["error_message"], "", "a failed deploy is not a failed tick")

    async def test_what_was_live_stays_recorded_when_a_newer_deploy_fails(self):
        self.write(deployment=self.pending(deployed_sha="old0000", url="https://org.github.io/site/"))
        self.deployer.advance.return_value = DeployStatus("failed", detail="build failed")

        await self.run_node()

        self.assertEqual((self.deployment()["deployed_sha"], self.deployment()["url"]),
                         ("old0000", "https://org.github.io/site/"))


class TestTimeouts(DeployNodeCase):
    async def test_a_commit_that_never_goes_live_times_out(self):
        """Otherwise a workflow that never runs keeps ticks coming forever."""
        self.write(deployment=self.pending(requested_at=time.time() - (DEFAULT_TIMEOUT_MINUTES + 1) * 60,
                                           checks=5))
        self.deployer.advance.return_value = DeployStatus("pending", detail="no `github-pages` deployment yet")

        result = await self.run_node()

        stored = self.deployment()
        self.assertEqual(stored["status"], "failed")
        self.assertEqual(stored["last_error"]["kind"], "deploy")
        self.assertIn("did not go live", stored["last_error"]["message"])
        self.assertIn("no `github-pages` deployment yet", stored["last_error"]["message"])
        self.assertTrue(result["tick_report"])

    async def test_the_timeout_can_be_configured(self):
        self.write(deployment=self.pending(requested_at=time.time() - 11 * 60),
                   deploy={"backend": "github_pages", "timeout_minutes": 10})

        await self.run_node()

        self.assertEqual(self.deployment()["status"], "failed")

    async def test_not_being_able_to_find_out_is_not_a_failed_deploy(self):
        self.write(deployment=self.pending(checks=1))
        self.deployer.advance.return_value = DeployStatus("pending", infra_error="gh: 502")

        result = await self.run_node()

        self.assertEqual(self.deployment()["status"], "pending")
        self.assertEqual(result["tick_report"], [])

    async def test_an_answer_that_never_comes_times_out_as_infra(self):
        self.write(deployment=self.pending(requested_at=time.time() - (DEFAULT_TIMEOUT_MINUTES + 1) * 60))
        self.deployer.advance.return_value = DeployStatus("pending", infra_error="gh: 502")

        await self.run_node()

        self.assertEqual(self.deployment()["last_error"]["kind"], "infra")


class TestTargets(DeployNodeCase):
    async def test_no_commit_named_deploys_what_the_branch_points_at(self):
        self.write(deployment=self.pending(target_sha=None))

        await self.run_node()

        self.head.assert_awaited_once()
        self.assertEqual(self.deployer.advance.await_args.args[0].sha, "headsha9876543")
        self.assertEqual(self.deployment()["target_sha"], "headsha9876543")

    async def test_a_branch_that_cannot_be_read_is_asked_again_later(self):
        self.write(deployment=self.pending(target_sha=None))
        self.head.return_value = ("", "gh: 502")

        result = await self.run_node()

        self.deployer.advance.assert_not_awaited()
        self.assertEqual(self.deployment()["status"], "pending")
        self.assertEqual(result["tick_report"], [])

    async def test_a_slow_answer_about_an_older_commit_does_not_mark_a_newer_request(self):
        self.write(deployment=self.pending())

        async def newer_merge_lands_meanwhile(target):
            manifest_store.request_deploy(self.manifest_path, sha="newer000", backend="github_pages")
            return DeployStatus("live", url="https://org.github.io/site/")

        self.deployer.advance.side_effect = newer_merge_lands_meanwhile

        result = await self.run_node()

        stored = self.deployment()
        self.assertEqual((stored["status"], stored["target_sha"]), ("pending", "newer000"))
        self.assertEqual(result["tick_report"], [])


class TestConfiguration(DeployNodeCase):
    async def test_an_unknown_backend_fails_as_config(self):
        patch.stopall()
        patch("graphs.coding.nodes.deploy.get_push_identity", return_value=None).start()
        self.write(deployment=self.pending(), deploy={"backend": "netlfy"})

        result = await self.run_node()

        stored = self.deployment()
        self.assertEqual((stored["status"], stored["last_error"]["kind"]), ("failed", "config"))
        self.assertIn("netlfy", result["tick_report"][0])

    async def test_no_repo_slug_fails_as_config(self):
        self.write(deployment=self.pending(), repo={})

        await self.run_node()

        self.assertEqual(self.deployment()["last_error"]["kind"], "config")
        self.deployer.advance.assert_not_awaited()


class TestDrivers(DeployNodeCase):
    """A destination the graph deploys to itself (a future `flyctl deploy`) is one class in BACKENDS."""

    async def test_a_drivers_handle_is_passed_back_until_the_deploy_is_live(self):
        patch.stopall()
        patch("graphs.coding.nodes.deploy.get_push_identity", return_value=None).start()
        handles_seen = []

        class FlyDriver:
            name = "fly"

            async def advance(self, target):
                handles_seen.append(target.handle)
                if not target.handle:
                    return DeployStatus("pending", handle="fly-release-1", detail="release started")
                return DeployStatus("live", url="https://site.fly.dev")

        self.write(deployment=self.pending(backend="fly"), deploy={"backend": "fly"})
        with patch.dict(BACKENDS, {"fly": FlyDriver}):
            first = await self.run_node()
            second = await self.run_node()

        self.assertEqual(handles_seen, ["", "fly-release-1"], "starting must not repeat once started")
        self.assertEqual(first["tick_report"], ["🚚 `demo`: deploying `abc1234` to fly."])
        self.assertEqual(second["tick_report"], ["🌐 `demo` is live at `abc1234`: https://site.fly.dev"])
        stored = self.deployment()
        self.assertEqual((stored["status"], stored["handle"]), ("live", "fly-release-1"))


if __name__ == "__main__":
    unittest.main()
