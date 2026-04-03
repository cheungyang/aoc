import unittest
import os
import sys
from unittest.mock import patch, MagicMock, mock_open

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.hook_loader import HookLoader

class TestHookLoader(unittest.TestCase):

    def setUp(self):
        self.loader = HookLoader()

    @patch('core.hook_loader.os.listdir')
    @patch('core.hook_loader.os.path.exists')
    @patch('core.hook_loader.importlib')
    def test_load_hooks_success(self, mock_importlib, mock_exists, mock_listdir):
        # Setup mocks
        mock_listdir.return_value = ['dummy_folder']
        mock_exists.return_value = True
        
        # Mock module and register_hooks
        mock_module = MagicMock()
        mock_register = MagicMock()
        mock_register.return_value = {
            "pre_message": "pre_hook_func",
            "post_message": "post_hook_func"
        }
        mock_module.register_hooks = mock_register
        mock_importlib.import_module.return_value = mock_module

        # Run
        self.loader.load_hooks()

        # Assertions
        mock_importlib.import_module.assert_called_once_with("core.dummy_folder.hooks")
        self.assertIn("pre_hook_func", self.loader.pre_message_hooks)
        self.assertIn("post_hook_func", self.loader.post_message_hooks)

    # test_load_skills_success moved to tests/core/test_skills_loader.py

    # test_run_mcp_server moved to tests/core/test_mcp_server_manager.py

if __name__ == "__main__":
    unittest.main()
