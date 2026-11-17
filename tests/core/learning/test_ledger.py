"""The prompt ledger (core/learning/ledger.py): each agent's prompt size, measured as the prompt loads it."""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.learning import ledger
from core.storage.learning import LearningStore

INJECT = "core.knowledge.memory.inject"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TestInstructionCount(unittest.TestCase):

    def test_list_items_and_imperative_lines_count(self):
        text = ("You plan the day.\n"
                "- Lead with three items\n"
                "2. Then the rest\n"
                "Never book without asking.\n"
                "Always say where a fact came from.\n"
                "Don't repeat yourself.\n"
                "The calendar is the source of truth.\n")
        self.assertEqual(ledger.instruction_count(text), 5)
        self.assertEqual(ledger.instruction_count(""), 0)


class TestMeasure(unittest.TestCase):

    def setUp(self):
        self.repo, self.pkm = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo)
        self.addCleanup(shutil.rmtree, self.pkm)
        agent = os.path.join(self.repo, "agents", "planner")
        write(os.path.join(agent, "AGENTS.md"), "# AGENTS.md\n\nPlan the day.\n- Lead with three items")
        write(os.path.join(agent, "SOUL.md"), "Warm and brief.")
        write(os.path.join(agent, "agent.json"), '{"skills": {"whole": {"preload": "full"}, '
                                                 '"cored": {"preload": "core"}, "on_demand": {}}}')
        write(os.path.join(self.repo, "skills", "whole", "SKILL.md"), "---\nname: whole\n---\nAll of it.")
        write(os.path.join(self.repo, "skills", "cored", "SKILL.md"),
              "Intro.\n## Core\n- The core step\n## Reference\nLong reference text.")
        write(os.path.join(self.repo, "skills", "on_demand", "SKILL.md"), "Loaded only when asked for.")
        write(os.path.join(self.repo, "agents", "runner", "agent.json"), '{"stateless": true}')
        write(os.path.join(self.pkm, "agents", "planner", "USER.md"), "# USER.md\n\nLikes mornings.")

    def measure(self, agent_id="planner", **renders):
        values = {"render_profile": "P" * 10, "render_topics": "T" * 5, "render_memory": "M" * 7,
                  "render_feedback": "F" * 3, "memory_topics": ["home"]}
        values.update(renders)
        with patch(f"{INJECT}.render_profile", return_value=values["render_profile"]), \
                patch(f"{INJECT}.render_topics", return_value=values["render_topics"]), \
                patch(f"{INJECT}.memory_topics", return_value=values["memory_topics"]), \
                patch(f"{INJECT}.render_memory", return_value=values["render_memory"]), \
                patch(f"{INJECT}.render_feedback", return_value=values["render_feedback"]):
            return ledger.measure(agent_id, self.repo, self.pkm)

    def test_each_part_as_the_prompt_carries_it(self):
        row = self.measure()
        persona = len("Plan the day.\n- Lead with three items") + len("Warm and brief.")
        self.assertEqual(row["persona_chars"], persona, "the title line is dropped, as read_prompt_file does")
        self.assertEqual(row["preload_chars"], len("All of it.") + len("- The core step"),
                         "a full preload's body, a core preload's Core section, no on-demand skill")
        self.assertEqual(row["user_chars"], len("Likes mornings."))
        self.assertEqual((row["shared_chars"], row["memory_chars"], row["feedback_chars"]), (15, 7, 3))
        self.assertEqual(row["instructions"], 2)

    def test_a_stateless_agent_gets_no_shared_memory(self):
        self.assertEqual(self.measure("runner")["shared_chars"], 0)

    def test_an_agent_whose_vault_cant_be_read_gets_no_row_that_day(self):
        write(os.path.join(self.repo, "agents", "other", "AGENTS.md"), "Another agent.")
        store = LearningStore(os.path.join(self.repo, "memory.db"))

        def memory(agent_id, pkm_dir=None):
            if agent_id == "planner":
                raise ValueError("bad entry")
            return ""

        with patch(f"{INJECT}.render_memory", side_effect=memory), \
                patch(f"{INJECT}.render_profile", return_value=""), patch(f"{INJECT}.render_topics", return_value=""), \
                patch(f"{INJECT}.render_feedback", return_value=""):
            with self.assertRaisesRegex(ledger.LedgerError, r"planner: ValueError: bad entry"):
                ledger.snapshot(store, ["planner", "other"], day="2026-11-02", repo=self.repo, pkm_dir=self.pkm)
        self.assertEqual([r["agent_id"] for r in store.ledger("2026-11-02")], ["other"],
                         "a partial row would read as a sudden drop")

    def test_a_snapshot_records_the_agents_that_have_a_prompt(self):
        store = LearningStore(os.path.join(self.repo, "memory.db"))
        with patch(f"{INJECT}.render_profile", return_value=""), patch(f"{INJECT}.render_topics", return_value=""), \
                patch(f"{INJECT}.render_memory", return_value=""), patch(f"{INJECT}.render_feedback", return_value=""):
            rows = ledger.snapshot(store, ["planner", "runner"], day="2026-11-02", repo=self.repo, pkm_dir=self.pkm)
        self.assertEqual([r["agent_id"] for r in rows], ["planner"], "runner has no persona and no preload")
        [saved] = store.ledger("2026-11-02")
        self.assertEqual((saved["agent_id"], ledger.total(saved)), ("planner", ledger.total(rows[0])))

    def test_today_is_on_the_wall_clock(self):
        self.assertRegex(ledger.today(), r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
