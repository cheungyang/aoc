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
from core.tooling import format_tool_response


def _history(done=True, urls=("https://resy.com/", "https://resy.com/checkout"), result="Staged", errors=(), steps=4):
    history = MagicMock()
    history.is_done.return_value = done
    history.urls.return_value = list(urls)
    history.final_result.return_value = result
    history.number_of_steps.return_value = steps
    history.errors.return_value = list(errors)
    return history


def _ok():
    response = MagicMock()
    response.ok = True
    return response


class BrowserTestCase(unittest.TestCase):
    """Writes a CDP URL file and points the tool at it; the desktop is up by default."""

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
        probe = patch("tools.browser.requests.get", return_value=_ok())
        self.mock_probe = probe.start()
        self.addCleanup(probe.stop)

    def write_info(self, data):
        with open(self.info_file, "w") as handle:
            json.dump(data, handle)

    def desktop_goes_offline(self):
        self.mock_probe.side_effect = requests.ConnectionError("refused")


@patch("tools.browser.Agent")
@patch("tools.browser.ChatGoogle")
@patch("tools.browser.BrowserSession")
@patch("tools.browser.BrowserProfile")
class TestBrowserRun(BrowserTestCase):

    def _wire(self, mock_session, mock_agent, history=None, error=None):
        session = mock_session.return_value
        session.stop = AsyncMock()
        session.kill = AsyncMock()
        agent = mock_agent.return_value
        agent.run = AsyncMock(return_value=history or _history(), side_effect=error)
        agent.history = history or _history()
        return session

    def test_connects_to_desktop_and_never_kills_it(self, mock_profile, mock_session, _llm, mock_agent):
        session = self._wire(mock_session, mock_agent)

        result = browser.func(goal="Book table", max_steps=10)

        mock_profile.assert_called_once_with(cdp_url="http://192.168.1.20:9223", is_local=False, keep_alive=True)
        self.mock_probe.assert_called_with("http://192.168.1.20:9223/json/version",
                                           timeout=browser_mod.PROBE_TIMEOUT_SECONDS)
        run_kwargs = mock_agent.return_value.run.await_args.kwargs
        self.assertEqual(run_kwargs["max_steps"], 10)
        self.assertTrue(callable(run_kwargs["on_step_end"]))
        session.stop.assert_awaited_once()
        session.kill.assert_not_called()
        expected = ("Browsing completed after 4 steps.\n"
                    "Final URL: https://resy.com/checkout\n"
                    "Final Result: Staged")
        self.assertEqual(result, format_tool_response("browser", payload=expected, errors="None"))

    def test_system_prompt_has_handoff_and_fill_policy(self, _profile, mock_session, _llm, mock_agent):
        self._wire(mock_session, mock_agent)
        browser.func(goal="x")
        kwargs = mock_agent.call_args.kwargs
        self.assertIsNone(kwargs["sensitive_data"])
        prompt = kwargs["extend_system_message"]
        for rule in ("STOP", "REQUIRED field has no matching placeholder", "Never invent",
                     "Never enter payment card numbers"):
            self.assertIn(rule, prompt)

    def test_failure_with_desktop_up_is_a_plain_error(self, _profile, mock_session, _llm, mock_agent):
        session = self._wire(mock_session, mock_agent, error=Exception("Page crashed"))
        result = browser.func(goal="x")
        self.assertIn("Error performing browser action: Page crashed", result)
        session.stop.assert_awaited_once()
        session.kill.assert_not_called()
        self.assertFalse(browser_mod._session_lock.locked())

    def test_failure_after_desktop_vanished_is_disconnected(self, _profile, mock_session, _llm, mock_agent):
        self._wire(mock_session, mock_agent, history=_history(steps=3))

        async def die(**_):
            self.desktop_goes_offline()
            raise ConnectionError("websocket closed")
        mock_agent.return_value.run = AsyncMock(side_effect=die)

        result = browser.func(goal="x")
        self.assertIn("Status: DESKTOP_DISCONNECTED", result)
        self.assertIn("after step 3 on https://resy.com/checkout", result)
        self.assertIn("Nothing was submitted", result)
        self.assertIn("Do not retry", result)

    def test_step_watch_stops_agent_when_desktop_drops(self, _profile, mock_session, _llm, mock_agent):
        self._wire(mock_session, mock_agent)
        agent = mock_agent.return_value

        async def run(max_steps, on_step_end):
            self.desktop_goes_offline()
            await on_step_end(agent)  # the hook notices and stops the agent
            return _history(done=False, steps=2)
        agent.run = AsyncMock(side_effect=run)

        result = browser.func(goal="x")
        agent.stop.assert_called_once()
        self.assertIn("Status: DESKTOP_DISCONNECTED", result)
        self.assertIn("after step 2", result)

    def test_hung_disconnect_is_bounded(self, _profile, mock_session, _llm, mock_agent):
        session = self._wire(mock_session, mock_agent)

        async def hang():
            import asyncio
            await asyncio.sleep(60)
        session.stop = AsyncMock(side_effect=hang)
        with patch.object(browser_mod, "DISCONNECT_TIMEOUT_SECONDS", 0.05):
            result = browser.func(goal="x")
        self.assertIn("Browsing completed", result)

    def test_max_steps_is_clamped(self, _profile, mock_session, _llm, mock_agent):
        self._wire(mock_session, mock_agent)
        browser.func(goal="x", max_steps=999)
        self.assertEqual(mock_agent.return_value.run.await_args.kwargs["max_steps"], browser_mod.MAX_MAX_STEPS)

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
        self.assertIn("Personal fields available: full_name, phone", result)
        self.assertNotIn("555-0100", result)

    def test_hostname_is_resolved_to_ip(self, mock_profile, mock_session, _llm, mock_agent):
        self._wire(mock_session, mock_agent)
        with open(self.url_file, "w") as handle:
            handle.write("http://winpc.lan:9223")
        with patch("tools.browser.socket.gethostbyname", return_value="192.168.1.30"):
            browser.func(goal="x")
        self.assertEqual(mock_profile.call_args.kwargs["cdp_url"], "http://192.168.1.30:9223")


class TestBrowserStatuses(BrowserTestCase):

    @patch("tools.browser.Agent")
    def test_offline_desktop_reports_and_does_nothing(self, mock_agent):
        self.desktop_goes_offline()
        result = browser.func(goal="x")
        self.assertIn("Status: DESKTOP_OFFLINE", result)
        self.assertIn("Nothing was done", result)
        self.assertIn("Do not retry", result)
        self.assertIn("Agent Chrome (AOC)", result)
        mock_agent.assert_not_called()
        self.assertEqual(self.mock_probe.call_count, 1)  # one check, no retries

    def test_unresolvable_desktop_name_is_offline(self):
        with open(self.url_file, "w") as handle:
            handle.write("http://winpc.lan:9223")
        with patch("tools.browser.socket.gethostbyname", side_effect=OSError("no such host")):
            result = browser.func(goal="x")
        self.assertIn("Status: DESKTOP_OFFLINE", result)

    def test_check_only_online(self):
        self.write_info({"fields": {"email": "a@b.c"}})
        result = browser.func(check_only=True)
        self.assertIn("Status: ONLINE", result)
        self.assertIn("Personal fields available: email", result)
        self.assertNotIn("a@b.c", result)

    def test_check_only_offline(self):
        self.desktop_goes_offline()
        result = browser.func(check_only=True)
        self.assertIn("Status: DESKTOP_OFFLINE", result)

    def test_missing_url_file_is_not_configured(self):
        os.remove(self.url_file)
        result = browser.func(goal="x")
        self.assertIn("Status: NOT_CONFIGURED", result)

    def test_malformed_personal_info_is_not_configured(self):
        self.write_info({"fields": {"phone": 5550100}})
        result = browser.func(goal="x")
        self.assertIn("Status: NOT_CONFIGURED", result)
        self.assertIn("'fields' must map names to strings", result)

    def test_goal_required_without_check_only(self):
        self.assertIn("goal is required", browser.func())

    def test_second_concurrent_task_is_refused(self):
        self.assertTrue(browser_mod._session_lock.acquire(blocking=False))
        try:
            result = browser.func(goal="x")
        finally:
            browser_mod._session_lock.release()
        self.assertIn("another browser task is already running", result)


if __name__ == "__main__":
    unittest.main()
