"""Which host ticks, and the switch a handoff flips.

Only one host may tick at a time, or two hosts write the same manifest and the
pkm sync keeps one copy. These tests pin the pieces that make that safe: leases
that say where they were taken, a pause that is local to one host, and an
environment switch that overrides everything.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from graphs.coding.utils import host


class HostTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pause_file = os.path.join(self.tmp.name, "sessions", "coding_tick.paused")
        env = patch.dict(os.environ, {
            host.PAUSE_FILE_ENV: self.pause_file,
            host.HOST_NAME_ENV: "nas",
            host.TICK_ENABLED_ENV: "1",
        })
        env.start()
        self.addCleanup(env.stop)


class TestLeaseOwners(HostTestCase):
    def test_a_lease_owner_names_its_host(self):
        owner = host.new_lease_owner()
        self.assertRegex(owner, r"^nas:tick_[0-9a-f]{6}$")
        self.assertEqual(host.owner_host(owner), "nas")

    def test_the_hostname_is_used_when_no_name_is_configured(self):
        with patch.dict(os.environ, {host.HOST_NAME_ENV: ""}), \
             patch("graphs.coding.utils.host.socket.gethostname", return_value="devbox.local"):
            self.assertEqual(host.host_name(), "devbox")

    def test_a_colon_in_the_name_cannot_break_the_owner_format(self):
        with patch.dict(os.environ, {host.HOST_NAME_ENV: "a:b"}):
            self.assertEqual(host.owner_host(host.new_lease_owner()), "a_b")

    def test_a_legacy_owner_has_no_host(self):
        self.assertIsNone(host.owner_host("tick_ab12cd"))
        self.assertIsNone(host.owner_host(None))

    def test_a_legacy_owner_might_be_this_host(self):
        """Guessing "not mine" would let a handoff leave a live tick behind."""
        self.assertTrue(host.is_this_host("tick_ab12cd"))
        self.assertTrue(host.is_this_host("nas:tick_ab12cd"))
        self.assertFalse(host.is_this_host("devbox:tick_ab12cd"))


class TestTickSwitch(HostTestCase):
    def test_ticking_is_on_by_default(self):
        self.assertEqual(host.tick_enabled(), (True, ""))

    def test_pausing_stops_this_host_and_says_why(self):
        host.pause("handed off")

        enabled, reason = host.tick_enabled()

        self.assertFalse(enabled)
        self.assertIn("handed off", reason)
        self.assertIn("nas", reason)

    def test_unpausing_restores_it(self):
        host.pause("handed off")

        self.assertTrue(host.unpause())
        self.assertTrue(host.tick_enabled()[0])
        self.assertFalse(host.unpause(), "a second unpause has nothing to undo")

    def test_the_environment_switch_overrides_everything(self):
        for value in ("0", "false", "OFF", "no"):
            with self.subTest(value=value), patch.dict(os.environ, {host.TICK_ENABLED_ENV: value}):
                enabled, reason = host.tick_enabled()
                self.assertFalse(enabled)
                self.assertIn(host.TICK_ENABLED_ENV, reason)

    def test_the_default_pause_file_is_under_the_gitignored_sessions_dir(self):
        """Inside pkm or a tracked path, pausing one host would pause both."""
        with patch.dict(os.environ, {host.PAUSE_FILE_ENV: ""}):
            path = host.pause_file_path()
        self.assertEqual(os.path.basename(os.path.dirname(path)), "sessions")


class TestSyncPkm(HostTestCase):
    def test_a_successful_sync_is_reported(self):
        with patch("core.util.git_sync.sync_all", return_value={"success": True}) as mock_sync:
            ok, _ = host.sync_pkm()
        self.assertTrue(ok)
        self.assertTrue(mock_sync.call_args.kwargs["skip_codebase"])

    def test_a_failed_sync_carries_the_errors(self):
        with patch("core.util.git_sync.sync_all",
                   return_value={"success": False, "errors": ["conflict in build_request.json"]}):
            ok, message = host.sync_pkm()
        self.assertFalse(ok)
        self.assertIn("conflict", message)

    def test_an_exception_is_a_failed_sync_not_a_crash(self):
        with patch("core.util.git_sync.sync_all", side_effect=OSError("no network")):
            ok, message = host.sync_pkm()
        self.assertFalse(ok)
        self.assertIn("no network", message)


if __name__ == "__main__":
    unittest.main()
