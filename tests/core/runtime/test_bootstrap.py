"""bootstrap.wire(): the runtime plugged into the packages below it."""
import unittest
from unittest.mock import patch

from core.loaders import agents_loader
from core.prompts import base as prompts
from core.runtime import bootstrap
from core.runtime.delegation import ask_worker
from core.runtime.agent_factory import build_agent


class TestWire(unittest.TestCase):

    def test_it_fills_every_port_with_the_runtime(self):
        with patch.object(agents_loader, "_agent_factory", None), patch.object(prompts, "_worker", None):
            bootstrap.wire()
            self.assertIs(agents_loader._agent_factory, build_agent)
            self.assertIs(prompts._worker, ask_worker)

    def test_wiring_twice_changes_nothing(self):
        with patch.object(agents_loader, "_agent_factory", None), patch.object(prompts, "_worker", None):
            bootstrap.wire()
            bootstrap.wire()
            self.assertIs(agents_loader._agent_factory, build_agent)
            self.assertIs(prompts._worker, ask_worker)


if __name__ == "__main__":
    unittest.main()
