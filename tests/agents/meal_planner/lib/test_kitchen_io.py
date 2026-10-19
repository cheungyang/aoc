import os
import stat
import tempfile
import threading
import unittest

from core.util.config import Config
from agents.meal_planner.lib.kitchen_io import INVENTORY, kitchen_path, locked, read_text, write_atomic


class TestKitchenIo(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.path = os.path.join(self.dir, "INVENTORY.md")

    def test_paths_follow_the_configured_pkm_dir(self):
        Config().pkm_dir = self.dir
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.assertEqual(kitchen_path(INVENTORY), os.path.join(self.dir, "wallet", "kitchen", "INVENTORY.md"))

    def test_read_a_missing_file(self):
        self.assertIsNone(read_text(self.path))

    def test_an_empty_file_is_not_a_missing_one(self):
        write_atomic(self.path, "")
        self.assertEqual(read_text(self.path), "")

    def test_write_replaces_the_text_exactly_and_leaves_no_temp_file(self):
        write_atomic(self.path, "a\r\nb")
        write_atomic(self.path, "牛肉\n")
        self.assertEqual(read_text(self.path), "牛肉\n")
        self.assertEqual(os.listdir(self.dir), ["INVENTORY.md"])

    def test_write_keeps_the_files_permissions(self):
        write_atomic(self.path, "x")
        os.chmod(self.path, 0o755)
        write_atomic(self.path, "y")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o755)

    def test_write_goes_through_a_symlink(self):
        write_atomic(self.path, "x")
        link = os.path.join(self.dir, "link.md")
        os.symlink(self.path, link)
        write_atomic(link, "y")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(read_text(self.path), "y")

    def test_a_failed_write_keeps_the_old_file(self):
        write_atomic(self.path, "old")
        with self.assertRaises(TypeError):
            write_atomic(self.path, None)
        self.assertEqual(read_text(self.path), "old")
        self.assertEqual(os.listdir(self.dir), ["INVENTORY.md"])

    def test_locked_serialises_writers_of_one_file(self):
        write_atomic(self.path, "0")

        def bump():
            for _ in range(50):
                with locked(self.path):
                    write_atomic(self.path, str(int(read_text(self.path)) + 1))

        threads = [threading.Thread(target=bump) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(read_text(self.path), "200")


if __name__ == "__main__":
    unittest.main()
