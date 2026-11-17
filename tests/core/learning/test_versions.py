"""Prompt versions (core/learning/versions.py): which prompt a model call ran on."""
import hashlib
import os
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.learning import events, versions
from core.storage.learning import LearningStore

VERSION = re.compile(r"^[0-9a-f]{8}\.[0-9a-f]{8}$")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TestVersions(unittest.TestCase):

    def setUp(self):
        self.repo, self.pkm = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo)
        self.addCleanup(shutil.rmtree, self.pkm)
        write(os.path.join(self.repo, "agents", "planner", "AGENTS.md"), "# AGENTS.md\n\nPlan the day.\n")
        write(os.path.join(self.repo, "agents", "planner", "agent.json"),
              '{"skills": {"digest": {"preload": "core"}}, "memory_topics": ["home"]}')
        write(os.path.join(self.repo, "core", "prompts", "templates", "system", "rules.md"), "Be brief.\n")
        write(os.path.join(self.repo, "skills", "digest", "SKILL.md"), "## Core\nThree items.\n")
        write(os.path.join(self.repo, "skills", "unlisted", "SKILL.md"), "Not this agent's.\n")
        write(os.path.join(self.pkm, "agents", "planner", "FEEDBACK.md"), "- keep it short\n")
        write(os.path.join(self.pkm, "wiki", "memory", "PROFILE.md"), "- lives by the sea\n")
        write(os.path.join(self.pkm, "wiki", "memory", "topics", "home.md"), "- two cats\n")
        patcher = patch.dict(os.environ, {"LEARNING_OBSERVE": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        versions.forget()
        self.addCleanup(versions.forget)

    def version(self):
        return versions.compute("planner", self.repo, self.pkm)[0]

    def test_the_files_a_version_covers(self):
        static = [os.path.relpath(p, self.repo) for p in versions.static_paths("planner", self.repo)]
        self.assertEqual(static, ["agents/planner/AGENTS.md", "agents/planner/agent.json",
                                  "core/prompts/templates/system/rules.md", "skills/digest/SKILL.md"])
        memory = [os.path.relpath(p, self.pkm) for p in versions.memory_paths("planner", self.repo, self.pkm)]
        self.assertEqual(memory, ["agents/planner/FEEDBACK.md", "wiki/memory/PROFILE.md", "wiki/memory/topics/home.md"])

    def test_a_skill_edit_moves_the_static_half_and_a_dream_the_memory_half(self):
        before = self.version()
        self.assertRegex(before, VERSION)
        write(os.path.join(self.repo, "skills", "digest", "SKILL.md"), "## Core\nFive items.\n")
        after_skill = self.version()
        self.assertNotEqual(after_skill.split(".")[0], before.split(".")[0])
        self.assertEqual(after_skill.split(".")[1], before.split(".")[1])
        write(os.path.join(self.pkm, "agents", "planner", "FEEDBACK.md"), "- keep it short\n- no emoji\n")
        after_dream = self.version()
        self.assertEqual(after_dream.split(".")[0], after_skill.split(".")[0])
        self.assertNotEqual(after_dream.split(".")[1], after_skill.split(".")[1])

    def test_a_skill_the_agent_doesnt_list_doesnt_count(self):
        before = self.version()
        write(os.path.join(self.repo, "skills", "unlisted", "SKILL.md"), "Changed.\n")
        self.assertEqual(self.version(), before)

    def test_current_records_each_new_version_once_with_its_repo_files(self):
        first = versions.current("planner", self.repo, self.pkm)
        self.assertEqual(first, self.version())
        with patch.object(versions, "TTL_S", 0):
            self.assertEqual(versions.current("planner", self.repo, self.pkm), first)
            write(os.path.join(self.repo, "agents", "planner", "AGENTS.md"), "# AGENTS.md\n\nPlan the week.\n")
            second = versions.current("planner", self.repo, self.pkm)
        self.assertNotEqual(second, first)
        events.flush()
        store = LearningStore()
        recorded = store.prompt_versions("planner")
        self.assertEqual([v["version"] for v in recorded], [second, first])
        sha = recorded[0]["files"]["agents/planner/AGENTS.md"]
        self.assertEqual(store.prompt_file(sha), "# AGENTS.md\n\nPlan the week.\n")
        vault_sha = recorded[0]["files"]["pkm/agents/planner/FEEDBACK.md"]
        self.assertEqual(vault_sha, hashlib.sha256(b"- keep it short\n").hexdigest())
        self.assertIsNone(store.prompt_file(vault_sha), "the vault's text stays in the vault")

    def test_within_the_ttl_the_files_arent_checked_again(self):
        first = versions.current("planner", self.repo, self.pkm)
        write(os.path.join(self.repo, "agents", "planner", "AGENTS.md"), "Changed.\n")
        self.assertEqual(versions.current("planner", self.repo, self.pkm), first)

    def test_no_version(self):
        self.assertIsNone(versions.current("", self.repo, self.pkm))
        with patch.dict(os.environ, {"LEARNING_OBSERVE": "0"}):
            self.assertIsNone(versions.current("planner", self.repo, self.pkm))
        with patch.object(versions, "compute", side_effect=OSError("unreadable")):
            self.assertIsNone(versions.current("planner", self.repo, self.pkm), "it never raises")


if __name__ == "__main__":
    unittest.main()
