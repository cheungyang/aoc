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


class TestUnreachableLocalLlm(unittest.TestCase):
    """A refused socket on this machine is a configuration fault, not an outage.

    The generic path classifies anything from openai/httpx as a service error
    and suggests trying again later. For a server that is simply not running,
    or is bound to 127.0.0.1 while the caller is in a container, that advice is
    wrong -- retrying never fixes either one.

    These build real `openai.APIConnectionError` objects rather than Exceptions
    carrying lookalike text. That distinction caught a bug: `str()` of the real
    exception is only "Connection error.", with neither host nor URL in it, so
    an earlier message-matching implementation passed synthetic tests and did
    nothing at all against the live server.
    """

    def setUp(self):
        from core.util.config import Config

        self.config = Config()
        self.config.local_llm_base_url = "http://localhost:9379/v1"

    def tearDown(self):
        self.config.reset()

    @staticmethod
    def _connection_error(url="http://localhost:9379/v1/chat/completions"):
        import httpx
        import openai

        return openai.APIConnectionError(request=httpx.Request("POST", url))

    def test_connection_error_names_the_endpoint_and_the_cause(self):
        message = format_error_message(self._connection_error())
        self.assertIn("http://localhost:9379/v1", message)
        self.assertIn("configuration problem", message)
        self.assertIn("127.0.0.1", message)

    def test_it_does_not_advise_retrying(self):
        """The whole reason this branch exists."""
        message = format_error_message(self._connection_error())
        self.assertNotIn("try again later", message.lower())

    def test_the_bare_message_alone_would_be_useless(self):
        """Pins the reason detection cannot be message-based: the exception
        says nothing about where it was going."""
        self.assertNotIn("9379", str(self._connection_error()))

    def test_a_wrapped_connection_error_is_still_recognised(self):
        """LangGraph wraps failures raised inside a node."""
        outer = RuntimeError("Error in node 'agent'")
        outer.__cause__ = self._connection_error()
        self.assertIn("on-device model server", format_error_message(outer))

    def test_a_different_endpoint_is_left_alone(self):
        """The same exception type covers every OpenAI-compatible endpoint, so
        the request URL has to be checked before claiming the failure."""
        err = self._connection_error("https://api.openai.com/v1/chat/completions")
        self.assertNotIn("on-device model server", format_error_message(err))

    def test_a_non_connection_failure_is_left_alone(self):
        """A 500 from a server that answered is a real service error; only an
        unreachable socket is a configuration fault."""
        err = Exception('500 Internal Server Error. {"error": {"code": 500, "message": "model failed to load"}}')
        self.assertNotIn("on-device model server", format_error_message(err))

    def test_an_unrelated_exception_is_left_alone(self):
        self.assertNotIn("on-device model server", format_error_message(KeyError("nope")))


class TestStringErrorBodyIsSurfaced(unittest.TestCase):
    """A server diagnostic must not be replaced by the client's placeholder.

    openai's SSE reader raises `APIError(message="An error occurred during
    streaming")` whenever the error payload is not a mapping carrying a
    "message" key. A server answering `{"error": "<text>"}` hits exactly that,
    and the only copy of what went wrong survives on `.body`.

    This is not hypothetical: it is how an exceeded context window on the
    on-device server reached the user as six words that said nothing, while the
    exception was carrying "Input token ids are too long. Exceeding the maximum
    number of tokens allowed: 7142 >= 4096".
    """

    @staticmethod
    def _streaming_api_error(body):
        import httpx
        import openai

        return openai.APIError(
            message="An error occurred during streaming",
            request=httpx.Request("POST", "http://localhost:9379/v1/chat/completions"),
            body=body,
        )

    def test_a_string_body_replaces_the_placeholder(self):
        err = self._streaming_api_error(
            "RuntimeError: INVALID_ARGUMENT: Input token ids are too long. "
            "Exceeding the maximum number of tokens allowed: 7142 >= 4096\n"
        )
        message = format_error_message(err)
        self.assertIn("Input token ids are too long", message)
        self.assertIn("7142", message)
        self.assertNotEqual(message, "An error occurred during streaming")

    def test_the_placeholder_survives_when_there_is_nothing_better(self):
        """No body means no diagnostic to recover; the placeholder is all there
        is, and inventing something would be worse."""
        self.assertEqual(
            format_error_message(self._streaming_api_error(None)),
            "An error occurred during streaming",
        )

    def test_a_structured_body_still_wins(self):
        """The usual shape keeps taking the documented path."""
        err = self._streaming_api_error({"error": {"code": 400, "message": "bad request"}})
        self.assertEqual(format_error_message(err), "[400] bad request")

    def test_an_informative_message_is_not_overridden_by_its_body(self):
        """Only the known placeholder is treated as worthless."""
        import httpx
        import openai

        err = openai.APIError(
            message="Model is overloaded",
            request=httpx.Request("POST", "http://localhost:9379/v1/chat/completions"),
            body="some less useful context",
        )
        self.assertEqual(format_error_message(err), "Model is overloaded")


if __name__ == "__main__":
    unittest.main()
