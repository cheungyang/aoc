import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.browser import browser
from core.util import format_tool_response

class TestBrowserTool(unittest.TestCase):

    @patch('tools.browser.Agent')
    @patch('tools.browser.ChatGoogle')
    @patch('tools.browser.BrowserSession')
    @patch('tools.browser.BrowserProfile')
    def test_browser_success(self, mock_profile, mock_session, mock_llm, mock_agent):
        # Setup mocks
        mock_agent_instance = MagicMock()
        mock_agent.return_value = mock_agent_instance
        
        mock_history = MagicMock()
        mock_history.final_result.return_value = "Successfully booked table"
        
        # Mock the async run method
        mock_agent_instance.run = AsyncMock(return_value=mock_history)
        
        # Call the tool (which is synchronous because it wraps async)
        result = browser.func(goal="Book table", port=9222)
        
        # Verify interactions
        mock_profile.assert_called_once_with(cdp_url="http://127.0.0.1:9222")
        mock_session.assert_called_once()
        mock_llm.assert_called_once()
        mock_agent.assert_called_once()
        mock_agent_instance.run.assert_called_once()
        
        # Verify result
        expected_payload = "Browsing completed. Final Result: Successfully booked table"
        expected_xml = format_tool_response("browser", payload=expected_payload, errors="None")
        self.assertEqual(result, expected_xml)

    @patch('tools.browser.Agent')
    def test_browser_failure(self, mock_agent):
        # Setup mock to raise exception
        mock_agent_instance = MagicMock()
        mock_agent.return_value = mock_agent_instance
        mock_agent_instance.run = AsyncMock(side_effect=Exception("Browser crashed"))
        
        result = browser.func(goal="Book table", port=9222)
        
        # Verify result contains error
        self.assertIn("Error performing browser action: Browser crashed", result)

if __name__ == '__main__':
    unittest.main()
