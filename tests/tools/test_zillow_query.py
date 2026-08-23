import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import json

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.zillow_query import zillow_query
from core.util import format_tool_response


class TestZillowQueryTool(unittest.TestCase):

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_api_key(self):
        result = zillow_query.invoke({"action": "search", "zipcode": "98109"})
        self.assertIn("RAPIDAPI_KEY environment variable not set", result)

    @patch.dict(os.environ, {"RAPIDAPI_KEY": "test_rapidapi_key"})
    @patch("tools.zillow_query.requests.get")
    @patch("tools.zillow_query.requests.post")
    def test_search_by_zipcode_success(self, mock_post, mock_get):
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.raise_for_status.return_value = None
        mock_post_resp.json.return_value = {
            "job_id": "test_job_123",
            "status": "processing",
            "results": [],
            "errors": []
        }
        mock_post.return_value = mock_post_resp

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.raise_for_status.return_value = None
        mock_get_resp.json.return_value = {
            "job_id": "test_job_123",
            "status": "complete",
            "results": [
                {
                    "url": "https://www.zillow.com/homedetails/441587555_zpid/",
                    "success": True,
                    "zpid": 441587555,
                    "status_code": 200,
                    "property": {
                        "zpid": 441587555,
                        "url": "https://www.zillow.com/homedetails/441587555_zpid/",
                        "street_address": "2144 N Westlake Ave N #01DO7",
                        "city": "Seattle",
                        "state": "WA",
                        "zipcode": "98109",
                        "price": 700000,
                        "living_area": 2000,
                        "bedrooms": 4,
                        "bathrooms": 2.0,
                        "hoa_fee": 100.0,
                        "rent_zestimate": 4000,
                        "description": "Lakefront living"
                    }
                }
            ],
            "errors": []
        }
        mock_get.return_value = mock_get_resp

        result = zillow_query.invoke({
            "action": "search",
            "zipcode": "98109",
            "poll_interval": 0.01,
            "timeout_seconds": 5
        })

        mock_post.assert_called_once()
        called_url = mock_post.call_args[0][0]
        called_payload = mock_post.call_args[1]["json"]
        called_headers = mock_post.call_args[1]["headers"]

        self.assertEqual(called_url, "https://zillow-property-data1.p.rapidapi.com/v1/properties")
        self.assertEqual(called_payload["urls"], ["https://www.zillow.com/homes/for_sale/98109_rb/"])
        self.assertEqual(called_headers["x-rapidapi-key"], "test_rapidapi_key")
        self.assertEqual(called_headers["x-rapidapi-host"], "zillow-property-data1.p.rapidapi.com")

        mock_get.assert_called_once_with(
            "https://zillow-property-data1.p.rapidapi.com/v1/results/test_job_123",
            headers=called_headers,
            timeout=15
        )

        self.assertIn("2144 N Westlake Ave N", result)
        self.assertIn("price_per_sqft", result)
        # Price 700000 / 2000 = 350.0
        self.assertIn("350.0", result)
        # Rent 4000 * 12 = 48000 - HOA 1200 = 46800 / 700000 = 6.69%
        self.assertIn("6.69", result)

    @patch.dict(os.environ, {"RAPIDAPI_KEY": "test_rapidapi_key"})
    @patch("tools.zillow_query.requests.get")
    @patch("tools.zillow_query.requests.post")
    def test_search_by_location_and_price_filters(self, mock_post, mock_get):
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.raise_for_status.return_value = None
        mock_post_resp.json.return_value = {
            "job_id": "job_austin",
            "status": "processing",
            "results": [],
            "errors": []
        }
        mock_post.return_value = mock_post_resp

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.raise_for_status.return_value = None
        mock_get_resp.json.return_value = {
            "job_id": "job_austin",
            "status": "complete",
            "results": [],
            "errors": []
        }
        mock_get.return_value = mock_get_resp

        result = zillow_query.invoke({
            "action": "search",
            "location": "Austin, TX",
            "min_price": 400000,
            "max_price": 900000,
            "poll_interval": 0.01,
            "timeout_seconds": 5
        })

        mock_post.assert_called_once()
        called_payload = mock_post.call_args[1]["json"]
        self.assertEqual(called_payload["urls"], ["https://www.zillow.com/homes/for_sale/Austin-TX/400000-900000_price/"])
        self.assertIn("job_austin", result)

    @patch.dict(os.environ, {"RAPIDAPI_KEY": "test_rapidapi_key"})
    def test_search_missing_criteria(self):
        result = zillow_query.invoke({"action": "search"})
        self.assertIn("No valid search target provided", result)

    @patch.dict(os.environ, {"RAPIDAPI_KEY": "test_rapidapi_key"})
    @patch("tools.zillow_query.requests.get")
    @patch("tools.zillow_query.requests.post")
    def test_property_details_by_zpid(self, mock_post, mock_get):
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.raise_for_status.return_value = None
        mock_post_resp.json.return_value = {
            "job_id": "job_detail_1",
            "status": "processing",
            "results": [],
            "errors": []
        }
        mock_post.return_value = mock_post_resp

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.raise_for_status.return_value = None
        mock_get_resp.json.return_value = {
            "job_id": "job_detail_1",
            "status": "complete",
            "results": [
                {
                    "url": "https://www.zillow.com/homedetails/48749425_zpid/",
                    "success": True,
                    "zpid": 48749425,
                    "status_code": 200,
                    "property": {
                        "zpid": 48749425,
                        "street_address": "2114 Bigelow Ave N",
                        "city": "Seattle",
                        "state": "WA",
                        "zipcode": "98109",
                        "price": 1250000,
                        "living_area": 2500,
                        "bedrooms": 4,
                        "bathrooms": 3.0,
                        "rent_zestimate": 5000
                    }
                }
            ],
            "errors": []
        }
        mock_get.return_value = mock_get_resp

        result = zillow_query.invoke({
            "action": "details",
            "zpid": 48749425,
            "poll_interval": 0.01,
            "timeout_seconds": 5
        })

        mock_post.assert_called_once()
        called_payload = mock_post.call_args[1]["json"]
        self.assertEqual(called_payload["zpids"], [48749425])
        self.assertIn("2114 Bigelow Ave N", result)
        self.assertIn("500.0", result) # 1250000 / 2500 = 500.0

    @patch.dict(os.environ, {"RAPIDAPI_KEY": "test_rapidapi_key"})
    def test_property_details_missing_args(self):
        result = zillow_query.invoke({"action": "details"})
        self.assertIn("Provide at least one 'zpid'", result)

    @patch.dict(os.environ, {"RAPIDAPI_KEY": "test_rapidapi_key"})
    @patch("tools.zillow_query.requests.get")
    def test_get_results_by_job_id(self, mock_get):
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.raise_for_status.return_value = None
        mock_get_resp.json.return_value = {
            "job_id": "job_query_99",
            "status": "complete",
            "results": [
                {
                    "url": "https://www.zillow.com/homedetails/123_zpid/",
                    "success": True,
                    "zpid": 123,
                    "status_code": 200,
                    "property": {
                        "zpid": 123,
                        "street_address": "123 Main St",
                        "price": 500000,
                        "living_area": 1000
                    }
                }
            ],
            "errors": []
        }
        mock_get.return_value = mock_get_resp

        result = zillow_query.invoke({
            "action": "results",
            "job_id": "job_query_99"
        })

        mock_get.assert_called_once_with(
            "https://zillow-property-data1.p.rapidapi.com/v1/results/job_query_99",
            headers={
                "x-rapidapi-host": "zillow-property-data1.p.rapidapi.com",
                "x-rapidapi-key": "test_rapidapi_key",
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            timeout=15
        )
        self.assertIn("123 Main St", result)

    @patch.dict(os.environ, {"RAPIDAPI_KEY": "test_rapidapi_key"})
    @patch("tools.zillow_query.requests.get")
    def test_get_results_not_found(self, mock_get):
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 404
        mock_get.return_value = mock_get_resp

        result = zillow_query.invoke({
            "action": "results",
            "job_id": "non_existent_job"
        })

        self.assertIn("not found or expired", result)

    @patch.dict(os.environ, {"RAPIDAPI_KEY": "test_rapidapi_key"})
    @patch("tools.zillow_query.requests.post")
    def test_submit_job_async(self, mock_post):
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.raise_for_status.return_value = None
        mock_post_resp.json.return_value = {
            "job_id": "async_job_1",
            "status": "processing",
            "results": [],
            "errors": []
        }
        mock_post.return_value = mock_post_resp

        result = zillow_query.invoke({
            "action": "submit_job",
            "zipcode": "98109"
        })

        self.assertIn("async_job_1", result)
        self.assertIn("processing", result)

    @patch.dict(os.environ, {"RAPIDAPI_KEY": "test_rapidapi_key"})
    @patch("tools.zillow_query.requests.post")
    def test_search_async_no_wait(self, mock_post):
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.raise_for_status.return_value = None
        mock_post_resp.json.return_value = {
            "job_id": "no_wait_job_2",
            "status": "processing",
            "results": [],
            "errors": []
        }
        mock_post.return_value = mock_post_resp

        result = zillow_query.invoke({
            "action": "search",
            "zipcode": "98109",
            "wait_for_results": False
        })

        self.assertIn("no_wait_job_2", result)
        self.assertIn("Job submitted successfully", result)

    @patch.dict(os.environ, {"RAPIDAPI_KEY": "test_rapidapi_key"})
    def test_unknown_action(self):
        result = zillow_query.invoke({"action": "unknown_action_xyz"})
        self.assertIn("Unknown action 'unknown_action_xyz'", result)


if __name__ == '__main__':
    unittest.main()
