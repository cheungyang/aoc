"""An agent's one USER.md lives in the PKM, so facts about the user never enter the repo."""
import os
import tempfile
import unittest
from unittest.mock import patch

from core.prompts import system
from core.config.settings import Config
from core.prompts.system import get_agent_memory_prompt, user_path


class TestUserFile(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.agent_dir = os.path.join(tmp.name, "agent")
        self.pkm = os.path.join(tmp.name, "pkm")
        os.makedirs(self.agent_dir)
        real = system._agent_prompt_files
        patcher = patch.object(system, "_agent_prompt_files", side_effect=lambda a: {
            k: (os.path.join(self.agent_dir, os.path.basename(path)), desc) for k, (path, desc) in real(a).items()
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_the_file_lives_in_the_agents_pkm_folder(self):
        self.assertEqual(user_path("test-agent"), os.path.join(self.pkm, "agents", "test-agent", "USER.md"))

    def test_it_precedes_the_profile(self):
        self.write(user_path("test-agent"), "# USER.md\n\nAddress the user as Sam.")
        self.write(os.path.join(self.pkm, "wiki", "memory", "PROFILE.md"),
                   "- [profile] Lives in Springfield. (src: test-agent · first 2026-09-01 · seen 2026-09-01 · x1)\n")
        memory = get_agent_memory_prompt("test-agent", {})
        self.assertIn("<content>Address the user as Sam.\n\n- Lives in Springfield.</content>", memory)

    def test_a_usermd_left_in_the_agent_folder_is_ignored(self):
        self.write(os.path.join(self.agent_dir, "USER.md"), "Lives at 1 Main St.")
        self.assertNotIn("1 Main St", get_agent_memory_prompt("test-agent", {}))

    def test_stateless_agents_get_it_but_not_the_profile(self):
        self.write(user_path("test-agent"), "Address the user as Sam.")
        self.write(os.path.join(self.pkm, "wiki", "memory", "PROFILE.md"),
                   "- [profile] Lives in Springfield. (src: test-agent · first 2026-09-01 · seen 2026-09-01 · x1)\n")
        memory = get_agent_memory_prompt("test-agent", {"stateless": True})
        self.assertIn("<content>Address the user as Sam.</content>", memory)
        self.assertNotIn("Springfield", memory)


if __name__ == "__main__":
    unittest.main()
