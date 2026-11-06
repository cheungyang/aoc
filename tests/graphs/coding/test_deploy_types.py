"""What a deployer is, independent of where it deploys.

What has to hold: every registered backend satisfies the one interface the node
calls, so the node never needs to know which it has; a new destination needs
nothing but a `name` and an `advance()`; a target can't be changed under a
deployer's feet; and a status says nothing went wrong with the asking unless
it says so.
"""
import dataclasses
import inspect
import unittest

from graphs.coding.utils.deploy import BACKENDS, FAILED, LIVE, PENDING, Deployer, DeployStatus, DeployTarget
from graphs.coding.utils.deploy.deploy_types import STATES


class TestTheInterface(unittest.TestCase):
    def test_every_registered_backend_is_a_deployer_under_its_own_name(self):
        for key, cls in BACKENDS.items():
            with self.subTest(backend=key):
                deployer = cls()
                self.assertIsInstance(deployer, Deployer)
                self.assertEqual(deployer.name, key)
                self.assertTrue(inspect.iscoroutinefunction(deployer.advance),
                                "advance() is awaited by the node; a sync one would hold the tick")

    def test_a_new_destination_needs_only_a_name_and_advance(self):
        class FlyDriver:
            name = "fly"

            async def advance(self, target):
                return DeployStatus(PENDING, handle="fly-release-1")

        self.assertIsInstance(FlyDriver(), Deployer)

    def test_something_without_advance_is_not_a_deployer(self):
        class NotADeployer:
            name = "nothing"

        self.assertNotIsInstance(NotADeployer(), Deployer)


class TestTargetAndStatus(unittest.TestCase):
    def test_the_states(self):
        self.assertEqual(STATES, (PENDING, LIVE, FAILED))
        self.assertEqual(STATES, ("pending", "live", "failed"))

    def test_a_target_cannot_be_changed_once_made(self):
        target = DeployTarget(repo="org/site", sha="abc1234", branch="main")

        with self.assertRaises(dataclasses.FrozenInstanceError):
            target.sha = "def5678"
        self.assertEqual((target.config, target.handle, target.push_identity), ({}, "", None))

    def test_targets_do_not_share_their_config(self):
        first = DeployTarget(repo="org/a", sha="1", branch="main")
        second = DeployTarget(repo="org/b", sha="2", branch="main")

        first.config["backend"] = "github_pages"

        self.assertEqual(second.config, {})

    def test_a_status_reports_no_infra_error_unless_given_one(self):
        status = DeployStatus(LIVE, url="https://org.github.io/site/")

        self.assertIsNone(status.infra_error)
        self.assertEqual((status.detail, status.log_url, status.handle), ("", "", ""))


if __name__ == "__main__":
    unittest.main()
