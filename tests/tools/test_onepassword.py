import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import json

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.onepassword import onepassword
from core.util import format_tool_response

class TestOnepasswordTool(unittest.TestCase):

    @patch('tools.onepassword.os.path.exists')
    def test_op_binary_not_found(self, mock_exists):
        mock_exists.return_value = False
        result = onepassword.func(search_term="github")
        self.assertIn("Error: 1Password CLI not found", result)

    @patch('tools.onepassword.os.path.exists')
    @patch('tools.onepassword.subprocess.run')
    def test_op_success(self, mock_run, mock_exists):
        mock_exists.return_value = True
        
        # Mock 3 calls
        res1 = MagicMock()
        res1.stdout = json.dumps({"id": "uuid-123", "fields": [{"purpose": "USERNAME", "value": "user"}]})
        res1.stderr = ""
        res1.returncode = 0
        
        res2 = MagicMock()
        res2.stdout = json.dumps({"fields": [{"purpose": "PASSWORD", "value": "pass"}]})
        res2.stderr = ""
        res2.returncode = 0
        
        res3 = MagicMock()
        res3.stdout = "123456\n"
        res3.stderr = ""
        res3.returncode = 0
        
        mock_run.side_effect = [res1, res2, res3]
        
        result = onepassword.func(search_term="github")
        
        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(result, format_tool_response("onepassword", payload="username: user\npassword: pass\notp: 123456", errors="None"))

    @patch('tools.onepassword.os.path.exists')
    @patch('tools.onepassword.subprocess.run')
    def test_op_missing_otp(self, mock_run, mock_exists):
        mock_exists.return_value = True
        
        res1 = MagicMock()
        res1.stdout = json.dumps({"id": "uuid-123", "fields": [{"purpose": "USERNAME", "value": "user"}]})
        res1.stderr = ""
        res1.returncode = 0
        
        res2 = MagicMock()
        res2.stdout = json.dumps({"fields": [{"purpose": "PASSWORD", "value": "pass"}]})
        res2.stderr = ""
        res2.returncode = 0
        
        res3 = MagicMock()
        res3.stdout = ""
        res3.stderr = "no otp"
        res3.returncode = 1
        
        mock_run.side_effect = [res1, res2, res3]
        
        result = onepassword.func(search_term="github")
        
        self.assertEqual(result, format_tool_response("onepassword", payload="username: user\npassword: pass", errors="None"))

    @patch('tools.onepassword.os.path.exists')
    @patch('tools.onepassword.subprocess.run')
    def test_op_error(self, mock_run, mock_exists):
        mock_exists.return_value = True
        
        res1 = MagicMock()
        res1.stdout = ""
        res1.stderr = "item not found"
        res1.returncode = 1
        
        mock_run.return_value = res1
        
        result = onepassword.func(search_term="unknown")
        
        self.assertIn("Error finding item", result)

if __name__ == '__main__':
    unittest.main()
