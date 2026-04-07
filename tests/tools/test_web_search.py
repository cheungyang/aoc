import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.web_search import web_search

class TestWebSearchTool(unittest.IsolatedAsyncioTestCase):

    @patch.dict(os.environ, {}, clear=True)
    async def test_missing_api_key(self):
        result = await web_search.ainvoke({"query": "test"})
        self.assertIn("Error: BRAVE_API_KEY environment variable not set", result)

    @patch.dict(os.environ, {"BRAVE_API_KEY": "test_key"})
    @patch('tools.web_search.requests.get')
    async def test_successful_search(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text = '{"results": []}'
        mock_get.return_value = mock_response
        
        result = await web_search.ainvoke({"query": "test"})
        
        mock_get.assert_called_once_with(
            "https://api.search.brave.com/res/v1/llm/context",
            params={"q": "test"},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": "test_key"
            }
        )
        self.assertEqual(result, '{"results": []}')

    @patch.dict(os.environ, {"BRAVE_API_KEY": "test_key"})
    @patch('tools.web_search.requests.get')
    async def test_failed_search(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        
        result = await web_search.ainvoke({"query": "test"})
        
        self.assertIn("Error performing search: Network error", result)

if __name__ == '__main__':
    unittest.main()
