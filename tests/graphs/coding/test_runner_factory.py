"""Backend selection defaults to local and never silently falls back.

Falling back to `local` on a typo would run untrusted code on the host exactly
when the configuration asked for it to run somewhere else.
"""
import os
import unittest
from unittest.mock import patch

from graphs.coding.utils.sandbox.local_runner import LocalRunner
from graphs.coding.utils.sandbox.runner_factory import (
    BACKEND_ENV_VAR,
    UnknownBackendError,
    get_runner,
    resolve_backend,
)


class TestResolveBackend(unittest.TestCase):
    def test_defaults_to_local(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_backend(), "local")

    def test_env_var_selects_the_backend(self):
        with patch.dict(os.environ, {BACKEND_ENV_VAR: "E2B "}):
            self.assertEqual(resolve_backend(), "e2b")

    def test_manifest_override_beats_the_env_var(self):
        with patch.dict(os.environ, {BACKEND_ENV_VAR: "e2b"}):
            self.assertEqual(resolve_backend({"backend": "local"}), "local")

    def test_an_environment_block_without_backend_uses_the_env_var(self):
        with patch.dict(os.environ, {BACKEND_ENV_VAR: "e2b"}):
            self.assertEqual(resolve_backend({"stack": "node-npm"}), "e2b")


class TestGetRunner(unittest.TestCase):
    def test_local_returns_a_local_runner(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(get_runner(), LocalRunner)

    def test_an_unknown_backend_raises_instead_of_falling_back(self):
        with patch.dict(os.environ, {BACKEND_ENV_VAR: "lcoal"}):
            with self.assertRaises(UnknownBackendError) as ctx:
                get_runner()
        self.assertIn("lcoal", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
