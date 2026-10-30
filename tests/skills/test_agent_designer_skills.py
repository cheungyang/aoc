"""agent-designer's skills and config: each skill's text matches its grants, ends with agent_lint,
and the Monday tooling review is wired to the memory topic's real file."""
import json
import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SKILLS = ("agent_creation", "round_trip_audit", "skill_creation", "update_permissions", "update_schedule",
          "workflow_change", "tooling_review")
# The steps that need more depth than agent-designer's low-thinking text turns.
HIGH_THINKING = ("agent_creation", "round_trip_audit", "workflow_change", "tooling_review")


def skill_text(skill_id):
    with open(os.path.join(REPO, "skills", skill_id, "SKILL.md"), encoding="utf-8") as f:
        return f.read()


def skill_json(skill_id):
    with open(os.path.join(REPO, "skills", skill_id, "skill.json"), encoding="utf-8") as f:
        return json.load(f)


def agent_json():
    with open(os.path.join(REPO, "agents", "agent-designer", "agent.json"), encoding="utf-8") as f:
        return json.load(f)


def required_tools(text):
    """The tools a SKILL.md's Required Tools section lists, one `- `name`` line each."""
    section = re.split(r"^#{2,3} Required Tools\s*$", text, flags=re.MULTILINE)[-1]
    return set(re.findall(r"^- `(\w+)`", section, re.MULTILINE))


class TestSkillFiles(unittest.TestCase):

    def test_frontmatter_matches_skill_json(self):
        for skill_id in SKILLS:
            with self.subTest(skill=skill_id):
                text, config = skill_text(skill_id), skill_json(skill_id)
                self.assertEqual(re.search(r"^name: (\S+)$", text, re.MULTILINE).group(1), skill_id)
                self.assertEqual(config["skill_id"], skill_id)
                self.assertEqual(re.search(r"^description: (.+)$", text, re.MULTILINE).group(1), config["description"])

    def test_required_tools_match_the_grants(self):
        for skill_id in SKILLS:
            with self.subTest(skill=skill_id):
                self.assertEqual(required_tools(skill_text(skill_id)), set(skill_json(skill_id)["tools"]))

    def test_every_skill_ends_with_agent_lint(self):
        for skill_id in SKILLS:
            with self.subTest(skill=skill_id):
                self.assertRegex(skill_text(skill_id), r"Run `agent_lint`[^\n]* and fix what it finds before reporting done")
                self.assertIn("agent_lint", skill_json(skill_id)["tools"])

    def test_heavy_steps_ask_for_high_thinking(self):
        """Its text turns think low; a heavy step asks for high with its reads (the model's
        <next_thinking> marker), at no extra step. No skill.json field and no set_thinking."""
        for skill_id in SKILLS:
            with self.subTest(skill=skill_id):
                if skill_id != "skill_creation":  # it only shows how to write one
                    asks = re.search(r"ask(?:ing)? for `high` thinking", skill_text(skill_id)) is not None
                    self.assertEqual(asks, skill_id in HIGH_THINKING)
                self.assertNotIn("thinking", skill_json(skill_id))
                self.assertNotIn("set_thinking", skill_text(skill_id))
                self.assertNotIn("set_thinking", skill_json(skill_id)["tools"])

    def test_agent_creation_asks_in_its_preloaded_core(self):
        """The plan's classification is the heavy step, and it happens before the Reference is
        loaded, so the ask has to be in the Core agent-designer preloads."""
        self.assertEqual(agent_json()["skills"]["agent_creation"], {"preload": "core"})
        core = skill_text("agent_creation").split("## Reference")[0]
        step = re.search(r"^2\. (.+)$", core, re.MULTILINE).group(1)
        self.assertIn("This is the heavy step: ask for `high` thinking in the same response as the reads it needs.",
                      step)

    def test_workflow_change_goes_through_the_coding_graph(self):
        text, tools = skill_text("workflow_change"), skill_json("workflow_change")["tools"]
        for needle in ("graph_call(", "graph_status(", "PR", "RULES.md", "WORKFLOW.md", "real files",
                       "Never copy a line from the user's files", "tests/test_repo_privacy.py"):
            self.assertIn(needle, text)
        self.assertNotIn("Copy the lines in", text)
        self.assertLessEqual({"graph_call", "graph_status"}, set(tools))
        self.assertEqual(tools["filesystem"]["pkm/wiki/software"], ["@write"], "it writes only the spec and manifest")
        self.assertNotIn("agents", [key for key, actions in tools["filesystem"].items() if actions != ["@read"]])

    def test_the_coding_graph_resolves_the_skills_query(self):
        from graphs.coding.adapters import prepare_input

        query = re.search(r'graph_call\(graph_name="coding", query="([^"]+)"\)', skill_text("workflow_change")).group(1)
        state = prepare_input(query, caller="agent-designer")
        self.assertEqual(state["project_name"], "aoc")
        self.assertTrue(state["build_request_path"].endswith(os.path.join("pkm", "wiki", "software", "aoc",
                                                                         "build_request.json")))

    def test_a_new_tool_is_granted_after_its_pr_merges(self):
        """Granted before then, it would be lint's unknown_tool until the merge."""
        core = skill_text("agent_creation").split("## Reference")[0]
        self.assertIn("A new tool is granted only after its PR merges, in `workflow_change`'s last step.", core)
        steps = re.findall(r"^\d+\. (.+)$", skill_text("workflow_change").split("## Example")[0], re.MULTILINE)
        self.assertTrue(steps[-1].startswith("After the PR merges, grant a new tool with `update_permissions`."))

    def test_update_permissions_covers_the_design(self):
        core = skill_text("update_permissions").split("## Reference")[0]
        for needle in ("!", "@change", "<agent_id>", "Reads:", "RULES.md needs no grant"):
            self.assertIn(needle, core)


class TestAgentDesignerConfig(unittest.TestCase):

    def test_text_turns_think_low(self):
        self.assertEqual(agent_json()["thinking"], {"text": "low"})

    def test_it_has_the_new_skills_on_demand(self):
        skills = agent_json()["skills"]
        self.assertEqual(skills["workflow_change"], {})
        self.assertEqual(skills["tooling_review"], {})

    def test_the_monday_review_follows_the_tooling_topic(self):
        from core.knowledge.memory.store import topic_scope

        config = agent_json()
        self.assertIn("tooling", config["memory_topics"])
        [entry] = [e for e in config["schedules"] if e["id"] == "tooling-review"]
        self.assertEqual((entry["cron"], entry["kind"]), ("0 9 * * 1", "prompt"))
        self.assertEqual(entry["channel"], "agent-management")
        self.assertIn(entry["channel"], config["channels"])
        self.assertEqual(entry["precondition"]["type"], "files_changed_since")
        self.assertEqual(entry["precondition"]["paths"], [topic_scope("tooling", pkm_dir="pkm").path])
        self.assertIn("tooling_review skill", " ".join(entry["prompt"]))

    def test_the_deny_entries_stay(self):
        filesystem = agent_json()["tools"]["filesystem"]
        for key in ("!agents/*/tools/", "!agents/*/preconditions/", "!agents/*/lib/", "!agents/*/WORKFLOW.md",
                    "!tests/agents/"):
            self.assertEqual(filesystem[key], ["@change"])


if __name__ == "__main__":
    unittest.main()
