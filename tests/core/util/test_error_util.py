import unittest
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.util.error_util import (
    format_tool_response,
    format_error_message,
    is_service_error,
)


class TestErrorUtil(unittest.TestCase):

    def test_format_tool_response(self):
        response = format_tool_response("test_tool", "test_payload", "test_errors")
        self.assertIn("<test_tool_response>", response)
        self.assertIn("<payload>test_payload</payload>", response)
        self.assertIn("<errors>test_errors</errors>", response)

    def test_format_error_message(self):
        # Google 503 UNAVAILABLE format
        err1 = Exception("503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.', 'status': 'UNAVAILABLE'}}")
        self.assertEqual(
            format_error_message(err1),
            "[503] This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later."
        )

        # Google 503 ServerError with nested JSON string inside message
        err_genai_nested = Exception("503 Service Unavailable. {'message': '{\\n  \"error\": {\\n    \"code\": 503,\\n    \"message\": \"This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.\",\\n    \"status\": \"UNAVAILABLE\"\\n  }\\n}\\n', 'status': 'Service Unavailable'}")
        self.assertEqual(
            format_error_message(err_genai_nested),
            "[503] This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later."
        )

        # Mock ServerError object with status_code and JSON string message
        class MockServerError(Exception):
            def __init__(self):
                self.status_code = 503
                self.message = '{\n  "error": {\n    "code": 503,\n    "message": "This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.",\n    "status": "UNAVAILABLE"\n  }\n}'

        self.assertEqual(
            format_error_message(MockServerError()),
            "[503] This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later."
        )

        # Exception wrapping a cause
        outer_err = Exception("During task with name 'agent' and id 'f227b23a-04c5-1944-91a6-43ec1fdf1461'")
        outer_err.__cause__ = MockServerError()
        self.assertEqual(
            format_error_message(outer_err),
            "[503] This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later."
        )

        # JSON formatted 429 error
        err2 = Exception('429 RESOURCE_EXHAUSTED. {"error": {"code": 429, "message": "Quota exceeded"}}')
        self.assertEqual(format_error_message(err2), "[429] Quota exceeded")

        # Plain Exception
        err3 = Exception("Simple invoke failure")
        self.assertEqual(format_error_message(err3), "Simple invoke failure")

        # None / Empty
        self.assertEqual(format_error_message(None), "Sorry, I encountered an error processing the request.")
        self.assertEqual(format_error_message(""), "Sorry, I encountered an error processing the request.")

    def test_is_service_error(self):
        # 503 error formats
        err1 = Exception("503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand.'}}")
        self.assertTrue(is_service_error(err1))

        err_genai_nested = Exception("503 Service Unavailable. {'message': '{\\n  \"error\": {\\n    \"code\": 503,\\n    \"message\": \"Spikes in demand\"\\n  }\\n}\\n'}")
        self.assertTrue(is_service_error(err_genai_nested))

        class MockServerError(Exception):
            def __init__(self):
                self.status_code = 503
                self.message = "503 Unavailable"

        self.assertTrue(is_service_error(MockServerError()))

        outer_err = Exception("Task failed")
        outer_err.__cause__ = MockServerError()
        self.assertTrue(is_service_error(outer_err))

        # 429 error
        err_429 = Exception('429 RESOURCE_EXHAUSTED. {"error": {"code": 429, "message": "Quota exceeded"}}')
        self.assertTrue(is_service_error(err_429))

        # None / Empty
        self.assertFalse(is_service_error(None))
        self.assertFalse(is_service_error(""))

        # Standard non-service exceptions should return False
        self.assertFalse(is_service_error(KeyError("missing_key")))
        self.assertFalse(is_service_error(TypeError("unsupported operand type")))
        self.assertFalse(is_service_error(AttributeError("object has no attribute 'foo'")))
        self.assertFalse(is_service_error(IndexError("list index out of range")))


if __name__ == "__main__":
    unittest.main()
