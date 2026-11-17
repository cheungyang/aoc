"""The learning analyst's boundaries (core/learning/boundaries.py): the lethal-trifecta rule, in code."""
import json
import os
import unittest

from core.learning.boundaries import (
    ANALYST,
    EVALUATOR,
    FROZEN,
    HOME_CHANNEL,
    READABLE,
    proposable,
    violations,
    why_not_proposable,
    within,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
GOOD_CONFIG = {"channels": [HOME_CHANNEL], "proactivity": {"follow_ups": False, "taps": False}}
GOOD_TOOLS = {"friction_query": {}, "submit_proposal": {}, "load_skill": {},
              "filesystem": {"agents/": ["@read"], "skills/": ["@read"], "core/prompts/templates/": ["read", "grep"]}}


class TestTheTrifecta(unittest.TestCase):

    def test_reading_and_its_own_tools_are_within_bounds(self):
        self.assertEqual(violations(GOOD_CONFIG, GOOD_TOOLS), [])

    def test_a_way_to_send_data_out_is_refused(self):
        for tool in ("web_search", "gog", "agent_call", "home_assistant", "agent_lint", "a_tool_added_tomorrow"):
            with self.subTest(tool):
                [problem] = violations(GOOD_CONFIG, {**GOOD_TOOLS, tool: {}})
                self.assertIn(f"may not hold `{tool}`", problem)

    def test_its_filesystem_only_reads(self):
        [write] = violations(GOOD_CONFIG, {**GOOD_TOOLS, "filesystem": {"agents/": ["@read", "write"]}})
        self.assertEqual(write, "`filesystem` may only read, not write")
        problems = violations(GOOD_CONFIG, {**GOOD_TOOLS, "filesystem": {}})
        self.assertIn("`filesystem` with no scope allows every action; grant paths with @read", problems)

    def test_the_default_grant_of_its_own_vault_folder_is_refused_by_its_deny_key(self):
        """Every agent may write its own pkm/agents/<id>/; the analyst's `!pkm/` deny key takes it back."""
        own = os.path.join(os.path.realpath(os.path.join(REPO, "pkm")), "agents", ANALYST)
        granted = {**GOOD_TOOLS["filesystem"], own: ["@write", "delete"]}
        [write] = violations(GOOD_CONFIG, {**GOOD_TOOLS, "filesystem": granted})
        self.assertTrue(write.startswith("`filesystem` may only read, not append, delete"))
        refused = {**granted, "!pkm/": ["@change"]}
        self.assertEqual(violations(GOOD_CONFIG, {**GOOD_TOOLS, "filesystem": refused}), [])

    def test_its_filesystem_reaches_only_the_readable_places(self):
        for path in ("core/", "core/learning/", "graphs/coding/prompts/", "**/*.md", "agents/../core/learning/",
                     "core/prompts/templates_old/", "/etc/", "pkm/vault/"):
            with self.subTest(path):
                [problem] = violations(GOOD_CONFIG, {**GOOD_TOOLS, "filesystem": {path: ["read"]}})
                self.assertIn("`filesystem` may reach only", problem)
        [problem] = violations(GOOD_CONFIG, {**GOOD_TOOLS, "filesystem": ["@read"]})
        self.assertIn("`filesystem` must name its paths", problem, "actions with no paths apply everywhere")
        self.assertEqual(violations(GOOD_CONFIG, {**GOOD_TOOLS, "filesystem": {"agents/*/SOUL.md": ["read"]}}), [])

    def test_it_lives_in_learning_lab_only(self):
        self.assertEqual(len(violations({**GOOD_CONFIG, "channels": [HOME_CHANNEL, "general"]}, GOOD_TOOLS)), 1)
        self.assertEqual(len(violations({**GOOD_CONFIG, "channel_hosts": ["*"]}, GOOD_TOOLS)), 1)

    def test_no_follow_ups_and_no_taps(self):
        """Follow-ups post later; its talk quotes everyone's conversations, so it stays out of the team's."""
        problems = violations({"channels": [HOME_CHANNEL]}, GOOD_TOOLS)
        self.assertEqual(len(problems), 2)
        self.assertIn('"follow_ups": false', problems[0])
        self.assertIn('"taps": false', problems[1])
        self.assertEqual(violations({"channels": [HOME_CHANNEL], "stateless": True}, GOOD_TOOLS), [])

    def test_the_analyst_in_the_repo_is_within_bounds(self):
        """Its agent.json, and every tool it can reach: its own, its graph's and its skills'."""
        from core.loaders.tools_loader import ToolsLoader
        from core.runtime.foundation.sessions import SessionManager

        with open(os.path.join(REPO, "agents", ANALYST, "agent.json"), encoding="utf-8") as f:
            config = json.load(f)
        ctx = SessionManager.get_session(agent_id=ANALYST, source="discord", channel=HOME_CHANNEL)
        merged = ToolsLoader()._merge_tool_permissions(ctx)
        self.assertEqual(violations(config, merged), [])
        self.assertIn("submit_proposal", merged)


class TestWhatItMayPropose(unittest.TestCase):

    def test_prompt_text_in_the_repo_and_settings_in_the_vault(self):
        for path in ("agents/day-planner/SOUL.md", "agents/day-planner/AGENTS.md", "skills/planning/SKILL.md",
                     "core/prompts/templates/system/team.md", "./agents/goal-setter/AGENTS.md",
                     "pkm/agents/day-planner/RULES.md", "pkm/agents/day-planner/FEEDBACK.md"):
            with self.subTest(path):
                self.assertTrue(proposable(path), why_not_proposable(path))

    def test_never_code_config_contracts_safety_rules_itself_or_its_judge(self):
        for path in ("agents/day-planner/lib/plan.py",                 # code, though next to a prompt
                     "agents/day-planner/agent.json",                  # config: grants and models
                     "agents/goal-setter/WORKFLOW.md",                 # the tools' contract: it changes with code
                     "agents/day-planner/IDENTITY.md",                 # mirrors agent.json
                     "skills/planning/skill.json",
                     "core/prompts/system.py",
                     "core/prompts/templates/browser/handoff.md",      # a safety rule
                     f"agents/{ANALYST}/SOUL.md", "skills/friction_review/SKILL.md",
                     "skills/prompt_experiment/SKILL.md", f"pkm/agents/{ANALYST}/RULES.md",  # the analyst itself
                     "core/learning/protected.py",                     # the evaluator
                     "pkm/agents/day-planner/USER.md", "pkm/agents/day-planner/MEMORY.md",  # the dream's
                     "pkm/vault/notes.md", "graphs/coding/prompts/coder.md",
                     "agents/../core/learning/gates.py", "../elsewhere/x.md", "/etc/x.md", ""):
            with self.subTest(path):
                self.assertFalse(proposable(path))
                self.assertTrue(why_not_proposable(path))

    def test_the_evaluator_is_never_inside_a_readable_place(self):
        """Not even to read: the analyst must not learn what pleases its judge."""
        for root in READABLE:
            for evaluator in EVALUATOR:
                with self.subTest(root=root, evaluator=evaluator):
                    self.assertFalse(within(evaluator, [root]) or within(root, [evaluator]))

    def test_its_own_files_are_frozen(self):
        for path in FROZEN:
            with self.subTest(path):
                self.assertFalse(proposable(path.rstrip("/") + ("/SOUL.md" if path.endswith("/") else "")))


if __name__ == "__main__":
    unittest.main()
