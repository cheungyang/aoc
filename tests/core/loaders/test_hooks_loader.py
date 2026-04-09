import unittest
import os
import sys
from unittest.mock import patch, MagicMock, mock_open

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.loaders.hooks_loader import HooksLoader

class TestHooksLoader(unittest.TestCase):

    def setUp(self):
        HooksLoader._instance = None
        self.loader = HooksLoader()

    @patch('core.loaders.hooks_loader.os.listdir')
    @patch('core.loaders.hooks_loader.os.path.isdir')
    @patch('core.loaders.hooks_loader.os.path.isfile')
    @patch('core.loaders.hooks_loader.importlib')
    def test_load_hooks_success(self, mock_importlib, mock_isfile, mock_isdir, mock_listdir):
        # Setup mocks
        def listdir_mock(path):
            if path == "core":
                return ["dummy_folder", "root_hook.py"]
            elif path == "core/dummy_folder":
                return ["folder_hook.py"]
            return []
        mock_listdir.side_effect = listdir_mock

        def isdir_mock(path):
            return path == "core/dummy_folder"
        mock_isdir.side_effect = isdir_mock

        def isfile_mock(path):
            return path in ["core/root_hook.py", "core/dummy_folder/folder_hook.py"]
        mock_isfile.side_effect = isfile_mock
        
        # Mock module and register_hooks
        mock_module = MagicMock()
        mock_register = MagicMock()
        mock_register.return_value = {
            "pre_message": "pre_hook_func",
            "post_message": "post_hook_func",
            "system_prompt": "prompt_hook_func"
        }
        mock_module.register_hooks = mock_register
        mock_importlib.import_module.return_value = mock_module

        # Run
        self.loader._load_hooks()

        # Assertions
        import_calls = [
            unittest.mock.call("core.dummy_folder.folder_hook"),
            unittest.mock.call("core.root_hook")
        ]
        mock_importlib.import_module.assert_has_calls(import_calls, any_order=True)
        
        self.assertIn("pre_hook_func", self.loader.pre_message_hooks)
        self.assertIn("post_hook_func", self.loader.post_message_hooks)
        self.assertIn("prompt_hook_func", self.loader.system_prompt_hooks)

    # test_load_skills_success moved to tests/core/test_skills_loader.py

    # test_run_mcp_server moved to tests/core/test_mcp_server_manager.py

if __name__ == "__main__":
    unittest.main()
