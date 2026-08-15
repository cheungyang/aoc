import unittest
from graphs.content_creation.utils.paths import normalize_project_path

class TestPaths(unittest.TestCase):
    def test_normalize_project_path(self):
        self.assertEqual(normalize_project_path("foo/bar/"), "foo/bar")

if __name__ == "__main__":
    unittest.main()
