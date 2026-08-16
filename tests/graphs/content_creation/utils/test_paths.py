import unittest
from unittest.mock import patch, MagicMock
from graphs.content_creation.utils.paths import normalize_project_path

class TestPaths(unittest.TestCase):
    def test_normalize_project_path(self):
        self.assertEqual(normalize_project_path("foo/bar/"), "foo/bar")
        self.assertEqual(normalize_project_path(None), "")
        self.assertEqual(normalize_project_path(""), "")

    @patch('os.path.exists')
    @patch('core.util.config.Config')
    def test_normalize_project_path_with_pkm_dir(self, mock_config, mock_exists):
        # Mock Config to return a specific pkm_dir
        mock_instance = MagicMock()
        mock_instance.pkm_dir = "/mock/pkm"
        mock_config.return_value = mock_instance
        
        def exists_side_effect(path):
            if path == "wiki/software/test":
                return False
            if path == "/mock/pkm/wiki/software/test":
                return True
            return False
            
        mock_exists.side_effect = exists_side_effect
        
        # When passed a path that doesn't exist, it should prepend pkm_dir if that exists
        self.assertEqual(normalize_project_path("wiki/software/test"), "/mock/pkm/wiki/software/test")

if __name__ == "__main__":
    unittest.main()
