"""agents/home-steward/tools/device_list.py: the device list in the PKM, read, refreshed or failed.

Home Assistant is mocked (`fetch`) and the PKM is a temp folder. ToolsLoader's
answers are patched, except in the last test, which runs on home-steward's real
grants.
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest.mock import ANY, patch

from agents.home_steward.lib.homeassistant.client import HomeAssistantError
from agents.home_steward.lib.homeassistant.entity_index import NOTICE, Entry
from agents.home_steward.tools import device_list as module
from agents.home_steward.tools.device_list import device_list
from core.loaders.tools_loader import ToolsLoader
from core.config.settings import Config
from tests.helpers import envelope, execution_context

AGENT = "home-steward"
FETCHED = [
    Entry("Kitchen", "light.kitchen_ceiling", "Kitchen ceiling"),
    Entry("Kitchen", "switch.plug_3", "Coffee maker"),
    Entry("Living Room", "light.living_room_lamp", "Living room lamp"),
    Entry("Living Room", "media_player.living_room_tv", "Living room TV"),
    Entry("", "fan.attic_fan", "Attic fan"),
]
# What the patched grant lets the agent control: lights and switches.
CONTROLLED = ("light", "switch")
LISTED = ['Kitchen: light.kitchen_ceiling, switch.plug_3 "Coffee maker"', "Living Room: light.living_room_lamp"]
UNREACHABLE = HomeAssistantError("Could not open a WebSocket to ws://ha.example/api/websocket: refused.")


class TestDeviceList(unittest.TestCase):

    def setUp(self):
        self.pkm = tempfile.mkdtemp()
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.addCleanup(shutil.rmtree, self.pkm, ignore_errors=True)
        self.path = os.path.join(self.pkm, "wallet", "home", "DEVICES.md")
        self.now = self.start(patch.object(module, "get_local_now", return_value=datetime(2026, 10, 18, 20, 0)))
        self.fetch = self.start(patch.object(module, "fetch", return_value=FETCHED))
        self.write_allowed = True
        self.grant = {"*": ["get_state"], "light.*": ["call_service"], "switch.*": ["call_service"]}
        self.check = self.start(patch.object(ToolsLoader, "check_permission", side_effect=self.allowed))
        self.start(patch.object(ToolsLoader, "get_tool_permissions",
                                side_effect=lambda ctx, tool: self.grant if tool == "home_assistant" else {}))

    def start(self, patcher):
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def allowed(self, ctx, tool, action=None, target=None, **kwargs):
        if tool == "home_assistant":
            return target.split(".", 1)[0] in CONTROLLED
        return self.write_allowed or action is None

    def call(self, **arguments):
        with execution_context(agent_id=AGENT):
            return envelope(device_list.invoke(arguments))

    def saved(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_the_first_call_fetches_and_saves_what_the_agent_may_control(self):
        result = self.call()
        self.assertEqual((result["status"], result["errors"]), ("ok", "None"))
        self.assertEqual(json.loads(result["payload"]), {"refreshed": "2026-10-18 20:00", "devices": LISTED})
        text = self.saved()
        self.assertEqual(text.splitlines()[0], NOTICE)
        self.assertIn("- switch.plug_3: Coffee maker", text)
        self.assertNotIn("fan.attic_fan", text)                    # not the agent's to control
        self.assertNotIn("media_player.living_room_tv", text)
        self.fetch.assert_called_once_with()
        self.check.assert_any_call(ANY, "device_list", "write", self.path)

    def test_asking_what_may_be_controlled_is_a_probe_not_a_write(self):
        self.call()
        asked = [c for c in self.check.call_args_list if c.args[1] == "home_assistant"]
        self.assertEqual(len(asked), len(FETCHED))
        for c in asked:
            self.assertEqual((c.args[2], c.kwargs), ("call_service", {"probe": True}))

    def test_later_calls_read_the_saved_list_without_home_assistant(self):
        self.call()
        self.fetch.reset_mock()
        result = self.call()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(json.loads(result["payload"]), {"refreshed": "2026-10-18 20:00", "devices": LISTED})
        self.fetch.assert_not_called()

    def test_a_refresh_fetches_again_and_rewrites_the_file(self):
        self.call()
        self.fetch.return_value = FETCHED + [Entry("", "light.porch", "Porch")]
        self.now.return_value = datetime(2026, 10, 25, 20, 0)
        result = self.call(refresh=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(json.loads(result["payload"]),
                         {"refreshed": "2026-10-25 20:00", "devices": LISTED + ["No area: light.porch"]})
        self.assertIn("Refreshed: 2026-10-25 20:00", self.saved())
        self.assertEqual(self.fetch.call_count, 2)

    def test_a_refresh_that_cant_reach_home_assistant_leaves_the_file(self):
        self.call()
        before = self.saved()
        self.fetch.side_effect = UNREACHABLE
        result = self.call(refresh=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["issues"], "- Home Assistant: Could not open a WebSocket to ws://ha.example/api/"
                                           "websocket: refused. The device list was left as it was. (required)")
        self.assertEqual(self.saved(), before)

    def test_no_list_and_no_home_assistant_says_to_look_devices_up(self):
        self.fetch.side_effect = UNREACHABLE
        result = self.call()
        self.assertEqual(result["status"], "failed")
        self.assertIn("There is no device list yet: look devices up with search_registry.", result["issues"])
        self.assertFalse(os.path.exists(self.path))

    def test_any_other_fetch_error_is_named(self):
        self.fetch.side_effect = TimeoutError()
        result = self.call()
        self.assertEqual(result["status"], "failed")
        self.assertIn("- Home Assistant: TimeoutError. There is no device list yet", result["issues"])

    def test_a_line_it_cant_read_comes_back_raw(self):
        self.call()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("Patio plug: broken\n")
        result = self.call()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["issues"], "- pkm/wallet/home/DEVICES.md: 1 line not recognized\n    Patio plug: broken")
        self.assertEqual(json.loads(result["payload"])["devices"], LISTED)

    def test_a_file_without_a_refreshed_line_is_partial(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("## Porch\n- light.porch: Porch\n")
        result = self.call()
        self.assertEqual(result["status"], "partial")
        self.assertIn("no Refreshed line", result["issues"])
        self.assertEqual(json.loads(result["payload"]),
                         {"refreshed": None, "devices": ["Porch: light.porch"]})

    def test_a_refused_write_asks_home_assistant_nothing(self):
        self.write_allowed = False
        result = self.call()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"], "Error: Agent 'home-steward' does not have permission to 'write' "
                                           "pkm/wallet/home/DEVICES.md.")
        self.fetch.assert_not_called()
        self.assertFalse(os.path.exists(self.path))

    def test_a_grant_without_call_service_or_a_blanket_one_lists_nothing(self):
        for grant in ({"*": ["get_state"]}, {}, ["call_service"]):
            with self.subTest(grant=grant):
                self.grant = grant
                result = self.call(refresh=True)
                self.assertEqual(json.loads(result["payload"]), {"refreshed": "2026-10-18 20:00", "devices": []})

    def test_explain_lists_home_assistant_and_how_many_were_kept(self):
        result = self.call(explain=True)
        self.assertIn("- Home Assistant (5 records)", result["explain"])
        self.assertIn("- pkm/wallet/home/DEVICES.md (modified ", result["explain"])
        self.assertIn("3 of the 5 live, user-facing devices are yours to control (call_service)", result["explain"])

    def test_a_new_file_is_readable_by_others(self):
        self.call()
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o644)

    def test_needs_an_agent_run(self):
        self.assertIn("no active execution context", device_list.invoke({}))

    def test_every_checked_action_writes(self):
        self.assertEqual(tuple(module.READ_ACTIONS), ())


class TestOnHomeStewardsGrants(unittest.TestCase):
    """agent.json grants device_list, its write, and control of every listed domain."""

    def setUp(self):
        ToolsLoader._instance = None
        self.addCleanup(setattr, ToolsLoader, "_instance", None)
        self.pkm = tempfile.mkdtemp()
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.addCleanup(shutil.rmtree, self.pkm, ignore_errors=True)
        fetch = patch.object(module, "fetch", return_value=FETCHED)
        fetch.start()
        self.addCleanup(fetch.stop)

    def test_lists_every_controllable_device(self):
        with execution_context(agent_id=AGENT):
            result = envelope(device_list.invoke({}))
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(json.loads(result["payload"])["devices"], [
            'Kitchen: light.kitchen_ceiling, switch.plug_3 "Coffee maker"',
            "Living Room: light.living_room_lamp, media_player.living_room_tv",
            "No area: fan.attic_fan",
        ])
        self.assertTrue(os.path.exists(os.path.join(self.pkm, "wallet", "home", "DEVICES.md")))


if __name__ == "__main__":
    unittest.main()
