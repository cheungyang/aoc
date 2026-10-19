"""PKM paths for agent tools: resolved under the user's PKM, shown the way the agent names them."""
import os
import tempfile
import unittest

from core.tooling import display_path, pkm_path
from core.util.config import Config


class TestPaths(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pkm = os.path.join(os.path.realpath(tmp.name), "vault")
        os.makedirs(self.pkm)
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)

    def test_pkm_path_joins_under_the_pkm(self):
        self.assertEqual(pkm_path("wallet", "kitchen", "INVENTORY.md"),
                         os.path.join(self.pkm, "wallet", "kitchen", "INVENTORY.md"))
        self.assertEqual(pkm_path("wallet/finance/2026-10.md"), os.path.join(self.pkm, "wallet", "finance", "2026-10.md"))
        self.assertEqual(pkm_path(), self.pkm)

    def test_pkm_path_refuses_to_leave_the_pkm(self):
        for parts in (("..", "secrets"), ("/etc/passwd",), ("wallet", "../../x")):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                pkm_path(*parts)

    def test_pkm_files_are_named_as_the_agent_names_them(self):
        self.assertEqual(display_path(os.path.join(self.pkm, "wallet", "a.md")), "pkm/wallet/a.md")
        self.assertEqual(display_path(self.pkm), "pkm")

    def test_a_symlinked_spelling_reads_the_same(self):
        link = os.path.join(os.path.dirname(self.pkm), "pkm-link")
        os.symlink(self.pkm, link)
        self.assertEqual(display_path(os.path.join(link, "wallet", "a.md")), "pkm/wallet/a.md")

    def test_repo_paths_are_relative_and_labels_pass_through(self):
        self.assertEqual(display_path(os.path.abspath(__file__)), "tests/core/tooling/test_paths.py")
        self.assertEqual(display_path("Google Health (sleep)"), "Google Health (sleep)")
        self.assertEqual(display_path("pkm/wallet/a.md"), "pkm/wallet/a.md")
        self.assertEqual(display_path("/elsewhere/a.md"), "/elsewhere/a.md")


if __name__ == "__main__":
    unittest.main()
