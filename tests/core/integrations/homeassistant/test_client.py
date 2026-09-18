"""Tests for the Home Assistant REST client.

The acceptance criterion for Phase 1 is "unit tests green; token never appears in
any log", so the redaction tests here are load-bearing rather than decorative.
No test touches the network: every request goes through an injected fake session.
"""
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

import requests

from core.integrations.homeassistant import client as ha


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeSession:
    """Returns queued responses (or raises queued exceptions) in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _write_token_file(contents="secret-token-value", mode=0o600):
    handle = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
    handle.write(contents)
    handle.close()
    os.chmod(handle.name, mode)
    return handle.name


class TestReadToken(unittest.TestCase):
    def test_reads_and_strips_the_token(self):
        path = _write_token_file("  abc123\n")
        try:
            self.assertEqual(ha.read_token(path), "abc123")
        finally:
            os.unlink(path)

    def test_missing_file_explains_how_to_create_it(self):
        with self.assertRaises(ha.HomeAssistantError) as ctx:
            ha.read_token("/nonexistent/home_assistant_token")
        message = str(ctx.exception)
        self.assertIn("not found", message)
        # The message has to be actionable on its own: it is the only thing the
        # operator sees when preflight fails.
        self.assertIn("install -m 600", message)
        self.assertIn(ha.TOKEN_PATH_ENV_VAR, message)

    def test_world_readable_file_is_refused(self):
        path = _write_token_file(mode=0o644)
        try:
            with self.assertRaises(ha.HomeAssistantError) as ctx:
                ha.read_token(path)
            self.assertIn("readable by other users", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_empty_file_is_refused(self):
        path = _write_token_file("   \n")
        try:
            with self.assertRaises(ha.HomeAssistantError) as ctx:
                ha.read_token(path)
            self.assertIn("empty", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_env_var_overrides_the_default_path(self):
        path = _write_token_file("from-env")
        try:
            with patch.dict(os.environ, {ha.TOKEN_PATH_ENV_VAR: path}):
                self.assertEqual(ha.read_token(), "from-env")
        finally:
            os.unlink(path)


class TestRedact(unittest.TestCase):
    def test_removes_the_known_token(self):
        self.assertNotIn("sekret", ha.redact("failed with sekret in it", "sekret"))

    def test_removes_bearer_credentials_it_does_not_know(self):
        out = ha.redact("Authorization: Bearer some.other.jwt", token=None)
        self.assertNotIn("some.other.jwt", out)
        self.assertIn(ha.REDACTED, out)

    def test_removes_every_bearer_occurrence(self):
        out = ha.redact("Bearer aaa retried with Bearer bbb", token=None)
        self.assertNotIn("aaa", out)
        self.assertNotIn("bbb", out)

    def test_handles_none(self):
        self.assertEqual(ha.redact(None), "")


class TestClientConstruction(unittest.TestCase):
    def test_requires_a_base_url(self):
        with patch.dict(os.environ, {ha.BASE_URL_ENV_VAR: ""}):
            with self.assertRaises(ha.HomeAssistantError) as ctx:
                ha.HomeAssistantClient(token="t")
            self.assertIn(ha.BASE_URL_ENV_VAR, str(ctx.exception))

    def test_strips_trailing_slash_from_base_url(self):
        c = ha.HomeAssistantClient(base_url="https://ha.example.com/", token="t")
        self.assertEqual(c.base_url, "https://ha.example.com")
        self.assertEqual(c._url("/api/"), "https://ha.example.com/api/")

    def test_repr_and_describe_never_contain_the_token(self):
        c = ha.HomeAssistantClient(base_url="https://ha.example.com", token="sekret")
        self.assertNotIn("sekret", repr(c))
        self.assertNotIn("sekret", c.describe())


class TestRequestBehaviour(unittest.TestCase):
    def _client(self, responses):
        return ha.HomeAssistantClient(
            base_url="https://ha.example.com",
            token="sekret",
            session=FakeSession(responses),
        )

    def test_sends_bearer_token_and_returns_json(self):
        c = self._client([FakeResponse(200, {"message": "API running."})])
        self.assertEqual(c.ping(), "API running.")
        sent = c._session.calls[0]
        self.assertEqual(sent["headers"]["Authorization"], "Bearer sekret")
        self.assertEqual(sent["url"], "https://ha.example.com/api/")

    def test_falls_back_to_text_when_body_is_not_json(self):
        c = self._client([FakeResponse(200, None, "plain log line")])
        self.assertEqual(c.get("/api/error_log"), "plain log line")

    def test_401_names_the_token_file_and_does_not_retry(self):
        c = self._client([FakeResponse(401, None, "Unauthorized")])
        with self.assertRaises(ha.HomeAssistantError) as ctx:
            c.get("/api/")
        self.assertIn("401", str(ctx.exception))
        self.assertIn("home_assistant_token", str(ctx.exception))
        self.assertEqual(len(c._session.calls), 1)

    def test_403_suggests_the_admin_requirement(self):
        c = self._client([FakeResponse(403, None, "Forbidden")])
        with self.assertRaises(ha.HomeAssistantError) as ctx:
            c.get("/api/config")
        self.assertIn("administrator", str(ctx.exception))

    def test_4xx_is_not_retried(self):
        c = self._client([FakeResponse(400, None, "Bad Request")])
        with self.assertRaises(ha.HomeAssistantError):
            c.post("/api/services/light/turn_on", json={})
        self.assertEqual(len(c._session.calls), 1)

    @patch("core.integrations.homeassistant.client.time.sleep", lambda _: None)
    def test_retries_transient_status_then_succeeds(self):
        c = self._client([
            FakeResponse(503, None, "unavailable"),
            FakeResponse(200, {"message": "API running."}),
        ])
        self.assertEqual(c.ping(), "API running.")
        self.assertEqual(len(c._session.calls), 2)

    @patch("core.integrations.homeassistant.client.time.sleep", lambda _: None)
    def test_retries_connection_errors_then_gives_up(self):
        c = self._client([
            requests.ConnectionError("boom"),
            requests.ConnectionError("boom"),
            requests.ConnectionError("boom"),
        ])
        with self.assertRaises(ha.HomeAssistantError) as ctx:
            c.get("/api/")
        self.assertIn("Could not reach", str(ctx.exception))
        self.assertEqual(len(c._session.calls), ha.MAX_ATTEMPTS)

    def test_error_bodies_are_redacted(self):
        # A misconfigured proxy echoing the request back is the realistic way a
        # token reaches an error string.
        c = self._client([FakeResponse(500, None, "upstream sent Bearer sekret")])
        with self.assertRaises(ha.HomeAssistantError) as ctx:
            c.get("/api/")
        self.assertNotIn("sekret", str(ctx.exception))


class TestWriteEnabled(unittest.TestCase):
    def test_defaults_to_false(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ha.write_enabled())

    def test_accepts_common_truthy_spellings(self):
        for value in ["true", "True", "1", "yes", "on"]:
            with patch.dict(os.environ, {ha.WRITE_ENABLED_ENV_VAR: value}):
                self.assertTrue(ha.write_enabled(), value)

    def test_anything_else_is_false(self):
        for value in ["false", "0", "no", "", "maybe"]:
            with patch.dict(os.environ, {ha.WRITE_ENABLED_ENV_VAR: value}):
                self.assertFalse(ha.write_enabled(), value)


if __name__ == "__main__":
    unittest.main()
