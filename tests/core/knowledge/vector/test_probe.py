"""Tests for the native-dependency capability probe.

The probe exists because `import lancedb` can kill the interpreter outright
rather than raise, so the important property is not that it detects a missing
module -- `importlib` does that -- but that it never runs the import in this
process.
"""

import subprocess
import sys
import unittest
from unittest.mock import patch

from core.knowledge.vector import probe


class TestRunImportProbe(unittest.TestCase):

    def setUp(self):
        probe.reset_cache()

    def tearDown(self):
        probe.reset_cache()

    def test_importable_module(self):
        self.assertTrue(probe._run_import_probe("json"))

    def test_missing_module(self):
        self.assertFalse(probe._run_import_probe("definitely_not_a_real_module_xyz"))

    def test_runs_in_a_child_interpreter(self):
        """The whole point: the probed import must not happen in this process."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            probe._run_import_probe("lancedb")

        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], sys.executable)
        self.assertEqual(args[1], "-c")
        self.assertEqual(args[2], "import lancedb")

    def test_nonzero_exit_is_unusable(self):
        """Exit 132 is 128 + SIGILL, which is how a crash on an old CPU shows up."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=132)
            self.assertFalse(probe._run_import_probe("lancedb"))

    def test_timeout_is_treated_as_unusable(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertFalse(probe._run_import_probe("lancedb"))

    def test_probe_that_cannot_be_launched_is_unusable(self):
        # A probe we were unable to run is not evidence the module works.
        with patch("subprocess.run", side_effect=OSError("no fork for you")):
            self.assertFalse(probe._run_import_probe("lancedb"))

    def test_passes_a_timeout(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            probe._run_import_probe("json")
        self.assertEqual(mock_run.call_args[1]["timeout"], probe.PROBE_TIMEOUT_SECONDS)


class TestProbeCaching(unittest.TestCase):

    def setUp(self):
        probe.reset_cache()

    def tearDown(self):
        probe.reset_cache()

    def test_verdict_is_memoized(self):
        with patch.object(probe, "_run_import_probe", return_value=True) as mock_probe:
            self.assertTrue(probe.can_import("somemodule"))
            self.assertTrue(probe.can_import("somemodule"))
        self.assertEqual(mock_probe.call_count, 1)

    def test_negative_verdict_is_memoized_too(self):
        with patch.object(probe, "_run_import_probe", return_value=False) as mock_probe:
            self.assertFalse(probe.can_import("somemodule"))
            self.assertFalse(probe.can_import("somemodule"))
        self.assertEqual(mock_probe.call_count, 1)

    def test_cache_can_be_bypassed(self):
        with patch.object(probe, "_run_import_probe", return_value=True) as mock_probe:
            probe.can_import("somemodule")
            probe.can_import("somemodule", use_cache=False)
        self.assertEqual(mock_probe.call_count, 2)

    def test_reset_cache_forces_a_re_probe(self):
        with patch.object(probe, "_run_import_probe", return_value=True) as mock_probe:
            probe.can_import("somemodule")
            probe.reset_cache()
            probe.can_import("somemodule")
        self.assertEqual(mock_probe.call_count, 2)

    def test_lancedb_usable_delegates_to_can_import(self):
        with patch.object(probe, "_run_import_probe", return_value=False) as mock_probe:
            self.assertFalse(probe.lancedb_usable())
        mock_probe.assert_called_once_with("lancedb")

    def test_modules_are_cached_independently(self):
        def fake(module):
            return module == "good"

        with patch.object(probe, "_run_import_probe", side_effect=fake):
            self.assertTrue(probe.can_import("good"))
            self.assertFalse(probe.can_import("bad"))
            self.assertTrue(probe.can_import("good"))


if __name__ == "__main__":
    unittest.main()
