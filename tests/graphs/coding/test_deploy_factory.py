"""Which deployer a project's `deploy` block asks for.

What has to hold: a destination is a key in one registry, so adding one is a
class and a line, not a change to the node; a project that asked to be deployed
under a name nobody knows is told so rather than quietly not deployed; and no
block at all (or `none`) means the project isn't deployed.
"""
import unittest

from graphs.coding.utils.deploy import (
    BACKENDS,
    Deployer,
    UnknownDeployBackendError,
    deploy_backend,
    deploy_configured,
    deploy_label,
    get_deployer,
)
from graphs.coding.utils.deploy.github_deployments import GitHubDeploymentWatcher, GitHubPagesWatcher

PAGES = {"backend": "github_pages"}


class TestFactory(unittest.TestCase):
    def test_the_backends(self):
        self.assertEqual(set(BACKENDS), {"github_pages", "github_deployment"})
        self.assertIsInstance(get_deployer(PAGES), GitHubPagesWatcher)
        self.assertIsInstance(get_deployer({"backend": "GitHub_Deployment"}), GitHubDeploymentWatcher)
        self.assertIsInstance(get_deployer(PAGES), Deployer)

    def test_an_unknown_backend_raises_instead_of_doing_nothing(self):
        with self.assertRaises(UnknownDeployBackendError) as ctx:
            get_deployer({"backend": "netlfy"})
        self.assertIn("github_pages", str(ctx.exception))

    def test_no_block_or_none_means_not_deployed(self):
        for config in (None, {}, {"backend": ""}, {"backend": "none"}, {"backend": " None "}, "github_pages"):
            with self.subTest(config=config):
                self.assertFalse(deploy_configured(config))
        self.assertTrue(deploy_configured(PAGES))

    def test_the_backend_name_is_normalised(self):
        self.assertEqual(deploy_backend({"backend": "  GitHub_Pages "}), "github_pages")
        self.assertEqual(deploy_backend(None), "")

    def test_labels(self):
        self.assertEqual(deploy_label(PAGES), "GitHub Pages")
        self.assertEqual(deploy_label({"backend": "github_deployment", "environment": "production"}),
                         "the `production` environment")
        self.assertEqual(deploy_label({"backend": "netlfy"}), "`netlfy`")


if __name__ == "__main__":
    unittest.main()
