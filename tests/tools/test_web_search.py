import asyncio
import time
import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.web_search import web_search
from core.tooling import format_tool_response

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
            },
            timeout=15,
        )
        self.assertEqual(result, format_tool_response("web_search", payload='{"results": []}', errors="None"))

    @patch.dict(os.environ, {"BRAVE_API_KEY": "test_key"})
    @patch('tools.web_search.requests.get')
    async def test_concurrent_searches_do_not_block_each_other(self, mock_get):
        """requests.get blocks, so it must not run on the event loop.

        Two searches awaited together overlap on worker threads: the pair
        takes about one request's time, not two.
        """
        delay = 0.4
        queries = []

        def slow_get(url, params, headers, timeout):
            time.sleep(delay)  # blocks its thread, like a real request
            queries.append(params["q"])
            response = MagicMock()
            response.raise_for_status.return_value = None
            response.text = '{"results": []}'
            return response

        mock_get.side_effect = slow_get

        start = time.perf_counter()
        results = await asyncio.gather(
            web_search.ainvoke({"query": "first"}),
            web_search.ainvoke({"query": "second"}),
        )
        elapsed = time.perf_counter() - start

        self.assertEqual(sorted(queries), ["first", "second"])
        expected = format_tool_response("web_search", payload='{"results": []}', errors="None")
        self.assertEqual(results, [expected, expected])
        self.assertLess(elapsed, delay * 1.5)

    @patch.dict(os.environ, {"BRAVE_API_KEY": "test_key"})
    @patch('tools.web_search.requests.get')
    async def test_large_search_results_truncated(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        
        # 20 results each with 1000 chars description (>20,000 chars total)
        big_results = [{"title": f"Result {i}", "url": f"https://example.com/{i}", "description": "D" * 1000} for i in range(20)]
        import json
        mock_response.text = json.dumps({"results": big_results})
        mock_get.return_value = mock_response
        
        result = await web_search.ainvoke({"query": "big search"})
        self.assertIn("Showing top 5 results to conserve context", result)
        self.assertIn("Result 0", result)
        self.assertIn("Result 4", result)
        self.assertNotIn("Result 15", result)

    @patch.dict(os.environ, {"BRAVE_API_KEY": "test_key"})
    @patch('tools.web_search.requests.get')
    async def test_failed_search(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        
        result = await web_search.ainvoke({"query": "test"})
        
        self.assertIn("Error performing search: Network error", result)

if __name__ == '__main__':
    unittest.main()
