import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools import browser as browser_mod
from tools.browser import browser
from core.util import format_tool_response


def _history(done=True, urls=("https://resy.com/", "https://resy.com/checkout"), result="Staged", errors=()):
    history = MagicMock()
    history.is_done.return_value = done
    history.urls.return_value = list(urls)
    history.final_result.return_value = result
    history.number_of_steps.return_value = 4
    history.errors.return_value = list(errors)
    return history


class BrowserTestCase(unittest.TestCase):
    """Writes a CDP URL file and points the tool at it; probe succeeds by default."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.url_file = os.path.join(self.tmp.name, "browser_cdp_url")
        self.info_file = os.path.join(self.tmp.name, "browser_personal_info.json")
        with open(self.url_file, "w") as handle:
            handle.write("http://192.168.1.20:9223\n")
        env = patch.dict(os.environ, {
            "BROWSER_CDP_URL_FILE": self.url_file,
            "BROWSER_PERSONAL_INFO_FILE": self.info_file,
        })
        env.start()
        self.addCleanup(env.stop)
        probe = patch("tools.browser.requests.get")
        self.mock_probe = probe.start()
        self.addCleanup(probe.stop)

    def write_info(self, data):
        with open(self.info_file, "w") as handle:
            json.dump(data, handle)


@patch("tools.browser.Agent")
@patch("tools.browser.ChatGoogle")
@patch("tools.browser.BrowserSession")
@patch("tools.browser.BrowserProfile")
class TestBrowserRun(BrowserTestCase):

    def _wire(self, mock_session, mock_agent, history=None, error=None):
        session = mock_session.return_value
        session.stop = AsyncMock()
        session.kill = AsyncMock()
        mock_agent.return_value.run = AsyncMock(return_value=history or _history(), side_effect=error)
        return session

    def test_connects_to_desktop_and_never_kills_it(self, mock_profile, mock_session, _llm, mock_agent):
        session = self._wire(mock_session, mock_agent)

        result = browser.func(goal="Book table", max_steps=10)

        mock_profile.assert_called_once_with(cdp_url="http://192.168.1.20:9223", is_local=False, keep_alive=True)
        self.mock_probe.assert_called_once_with("http://192.168.1.20:9223/json/version",
                                                timeout=browser_mod.PROBE_TIMEOUT_SECONDS)
        mock_agent.return_value.run.assert_awaited_once_with(max_steps=10)
        session.stop.assert_awaited_once()
        session.kill.assert_not_called()
        expected = ("Browsing completed after 4 steps.\n"
                    "Final URL: https://resy.com/checkout\n"
                    "Final Result: Staged")
        self.assertEqual(result, format_tool_response("browser", payload=expected, errors="None"))

    def test_handoff_rules_and_no_secrets_by_default(self, _profile, mock_session, _llm, mock_agent):
        self._wire(mock_session, mock_agent)
        browser.func(goal="x")
        kwargs = mock_agent.call_args.kwargs
        self.assertIsNone(kwargs["sensitive_data"])
        self.assertIn("STOP", kwargs["extend_system_message"])
        self.assertIn("Never enter payment card numbers", kwargs["extend_system_message"])

    def test_failure_disconnects_without_killing(self, _profile, mock_session, _llm, mock_agent):
        session = self._wire(mock_session, mock_agent, error=Exception("Page crashed"))
        result = browser.func(goal="x")
        self.assertIn("Error performing browser action: Page crashed", result)
        session.stop.assert_awaited_once()
        session.kill.assert_not_called()
        self.assertFalse(browser_mod._session_lock.locked())

    def test_max_steps_is_clamped(self, _profile, mock_session, _llm, mock_agent):
        self._wire(mock_session, mock_agent)
        browser.func(goal="x", max_steps=999)
        mock_agent.return_value.run.assert_awaited_once_with(max_steps=browser_mod.MAX_MAX_STEPS)

    def test_personal_info_passed_as_sensitive_data(self, mock_profile, mock_session, _llm, mock_agent):
        self._wire(mock_session, mock_agent)
        self.write_info({
            "fields": {"full_name": "Ada Lovelace", "phone": "555-0100", "email": ""},
            "allowed_domains": ["*.resy.com"],
        })
        result = browser.func(goal="x")
        self.assertEqual(mock_agent.call_args.kwargs["sensitive_data"],
                         {"full_name": "Ada Lovelace", "phone": "555-0100"})
        self.assertEqual(mock_profile.call_args.kwargs["allowed_domains"], ["*.resy.com"])
        # Only the placeholder names are reported back, never the values.
        self.assertIn("Personal fields available: full_name, phone", result)
        self.assertNotIn("555-0100", result)

    def test_hostname_is_resolved_to_ip(self, mock_profile, mock_session, _llm, mock_agent):
        self._wire(mock_session, mock_agent)
        with open(self.url_file, "w") as handle:
            handle.write("http://mac.lan:9223")
        with patch("tools.browser.socket.gethostbyname", return_value="192.168.1.30"):
            browser.func(goal="x")
        self.assertEqual(mock_profile.call_args.kwargs["cdp_url"], "http://192.168.1.30:9223")


class TestBrowserSetupErrors(BrowserTestCase):

    def test_unreachable_desktop_explains_how_to_start(self):
        self.mock_probe.side_effect = requests.ConnectionError("refused")
        result = browser.func(goal="x")
        self.assertIn("Agent Chrome on the desktop is not reachable", result)
        self.assertIn("start_agent_chrome.sh", result)

    def test_missing_url_file(self):
        os.remove(self.url_file)
        result = browser.func(goal="x")
        self.assertIn("desktop browser is not configured", result)

    def test_malformed_personal_info(self):
        self.write_info({"fields": {"phone": 5550100}})
        result = browser.func(goal="x")
        self.assertIn("'fields' must map names to strings", result)

    def test_second_concurrent_task_is_refused(self):
        self.assertTrue(browser_mod._session_lock.acquire(blocking=False))
        try:
            result = browser.func(goal="x")
        finally:
            browser_mod._session_lock.release()
        self.assertIn("another browser task is already running", result)


if __name__ == "__main__":
    unittest.main()
