"""Tests for agents/agent-designer/lib/lint.py, on throwaway checkouts and on the real repo."""
import json
import os
import re
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agents.agent_designer.lib import lint
from agents.agent_designer.lib.lint import AgentReport, Budget, Catalog, Finding, actions_in_module, lint_agent, render
from core.loaders.skills_loader import PRELOAD_TOKEN_BUDGET
from core.permissions.bundles import expand_permissions
from core.util.config import Config

TASK_TOOL = '''
from langchain_core.tools import tool


@tool
def task_query(action: str = "search", sql: str = "") -> str:
    """Query tasks."""
    if action == "sql":
        return sql
    elif action in ("stats", "sync"):
        return action
    return ""
'''

GOG_TOOL = '''
from langchain_core.tools import tool


@tool
def gog(command: str) -> str:
    """Run a gog command."""
    return command
'''

OWN_TOOL = '''
from langchain_core.tools import tool


@tool
def own_tool(action: str) -> str:
    """The agent's own tool."""
    return "went" if action == "go" else ""
'''

# A tool migrated onto @agent_tool, with the contract lines (core/tooling/agent_tool.py).
AGENT_TOOL = '''
from core.tooling import Report, agent_tool


@agent_tool
def credit_windows(report: Report, card: str = "") -> dict:
    """Where every recurring statement credit stands today.

    Reads: pkm/wallet/credit-card/*.md, and RULES.md
        for the alert windows.
    Computes: each credit's period, days left and status.
    Leaves out: which credit to use, and when.

    Args:
        card: Only this card's credits; empty for all.
    """
    return {"card": card}
'''

WORKFLOW = '''# test-agent: how its tools work

## credit_windows

A credit is raised `alert_days` before its period ends:

```python
>>> 14 - 3
11
```
'''

RULES_PY = '''from core.tooling import Rule

RULES = (
    Rule("alert_days", 14, "days before a credit lapses to raise it"),
    Rule("cpp_floor", 1.5, "cents per point a redemption must beat"),
)
'''

MATCHER_TOOL = '''
def PERMISSION_MATCHER(selector, target):
    return selector == target


def lights(action: str = "turn_on") -> str:
    """Switch lights."""
    return action
'''


class LintRepo(unittest.TestCase):
    """A throwaway checkout: agents/, skills/, tools/ (with the real filesystem tool), pkm/."""

    AGENT = "test-agent"

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "pkm", "agents"))
        Config().pkm_dir = os.path.join(self.root, "pkm")
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        with open(os.path.join(lint.PROJECT_ROOT, "tools", "filesystem.py"), encoding="utf-8") as f:
            self.file("tools/filesystem.py", f.read())
        self.file("tools/task_query.py", TASK_TOOL)
        self.file("tools/gog.py", GOG_TOOL)
        self.file("tools/load_skill.py", GOG_TOOL.replace("gog", "load_skill"))
        core_types = patch.object(lint, "core_precondition_types", return_value={"always", "files_exist"})
        core_types.start()
        self.addCleanup(core_types.stop)

    def file(self, rel, text=""):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def folder(self, rel):
        os.makedirs(os.path.join(self.root, rel), exist_ok=True)

    def agent(self, config=None, **files):
        config = {"agent_id": self.AGENT, "name": "T", **(config or {})}
        self.file(f"agents/{self.AGENT}/agent.json", json.dumps(config))
        for name, text in files.items():
            self.file(f"agents/{self.AGENT}/{name.replace('_', '.')}", text)

    def skill(self, skill_id, text, tools=None):
        self.file(f"skills/{skill_id}/SKILL.md", f"---\nname: {skill_id}\ndescription: d\n---\n{text}")
        self.file(f"skills/{skill_id}/skill.json", json.dumps({"skill_id": skill_id, "tools": tools or {}}))

    def report(self):
        return lint_agent(self.AGENT, self.root, measure_schemas=False)

    def found(self, kind=None):
        return [(f.kind, f.message, f.where) for f in self.report().findings if kind in (None, f.kind)]

    def messages(self, kind):
        return [message for _, message, _ in self.found(kind)]


class TestSkills(LintRepo):

    def test_phantom_and_ungranted_skills_in_the_agents_files(self):
        self.skill("research", "Read sources.")
        self.skill("weekly_review", "Review the week.")
        self.agent({"skills": {"research": {}}}, AGENTS_md=(
            "Follow the `research` skill.\nLog it with the `memory` skill.\nThen use the `weekly_review` skill.\n"))
        self.assertEqual(self.found("phantom_skill"),
                         [("phantom_skill", "`memory` is not a skill", f"agents/{self.AGENT}/AGENTS.md:2")])
        self.assertEqual(self.messages("skill_not_granted"), ["`weekly_review` is not in its skills"])

    def test_skill_text_counts_only_directives(self):
        self.skill("weekly_review", "Review the week.")
        self.skill("research", 'See the `weekly_review` skill for the format.\nThen call `load_skill("weekly_review")`.')
        self.agent({"skills": {"research": {}}})
        self.assertEqual(self.found("skill_not_granted"), [
            ("skill_not_granted", "`weekly_review` is not in its skills", "skills/research/SKILL.md:6")])

    def test_schedule_prompts_are_the_agents_own_text(self):
        self.agent({"schedules": [{"id": "nightly", "kind": "prompt", "precondition": {"type": "always", "reason": "r"},
                                   "prompt": ["Run the triage_tasks skill."]}]})
        self.assertEqual(self.found("phantom_skill"), [
            ("phantom_skill", "`triage_tasks` is not a skill", f"agents/{self.AGENT}/agent.json schedule nightly")])


class TestPaths(LintRepo):

    def setUp(self):
        super().setUp()
        self.folder("pkm/wallet/finance")

    def test_paths_against_filesystem_grants(self):
        self.agent({"tools": {"filesystem": {"pkm/wallet/finance": ["@read"]}}}, AGENTS_md="\n".join([
            "Read `pkm/wallet/finance/YYYY-MM.md` first.",
            "Notes are in `pkm/vault/notes/`.",
            "Your state is in `pkm/wallet/`.",
            "Keep scratch notes in `pkm/agents/<agent_id>/notes.md`.",
        ]))
        self.assertEqual(self.messages("path_outside_grants"), [
            "pkm/vault/notes: no filesystem grant covers it",
            "pkm/wallet: only pkm/wallet/finance inside it",
        ])
        self.assertEqual(self.found("missing_path"), [])

    def test_a_write_named_before_a_read_only_path(self):
        self.agent({"tools": {"filesystem": {"pkm/wallet/finance": ["@read"]}}},
                   AGENTS_md="Always use `append` on `pkm/wallet/finance/YYYY-MM.md`.")
        self.assertEqual(self.messages("action_not_granted"),
                         ["`filesystem` `append` on pkm/wallet/finance/YYYY-MM.md is not granted"])

    def test_skill_grants_count(self):
        self.skill("inbox", "Read `pkm/inbox/` and `append` to `pkm/inbox/log.md`.", {"filesystem": {"pkm/inbox": ["@write"]}})
        self.folder("pkm/inbox")
        self.agent({"skills": {"inbox": {}}})
        self.assertEqual(self.found("path_outside_grants") + self.found("action_not_granted"), [])

    def test_missing_folders(self):
        self.folder("pkm/journals")
        self.file("pkm/templates/Fleeting Pages.md")
        self.agent({"tools": {"filesystem": {"pkm": ["@read"]}}}, AGENTS_md="\n".join([
            "Read `pkm/journals/daily/YYYY-MM-DD.md`.",
            "Read `pkm/journals/today.md`; it may not exist yet.",
            "Write the summary to `pkm/summaries/week.md`.",
            "Copy `pkm/templates/Fleeting Pages.md`.",
            "Check `pkm/kitchen/` for stock.",
        ]))
        self.assertEqual(self.messages("missing_path"), [
            "pkm/journals/daily/YYYY-MM-DD.md: pkm/journals/daily does not exist",
            "pkm/kitchen does not exist",
        ])


class TestTools(LintRepo):

    def test_tool_and_action_references(self):
        self.agent({"tools": {"task_query": ["search"]}}, AGENTS_md="\n".join([
            "Use `file_search` to list the inbox.",
            "Check the calendar with `gog`.",
            "Run task_query `sql` for counts.",
            "Read it with filesystem `tail`.",
            "Never use the `explode` action.",
            "Use `start_line` to page through it.",
        ]))
        found = self.found()
        self.assertIn(("unknown_tool", "`file_search` is not a tool", f"agents/{self.AGENT}/AGENTS.md:1"), found)
        self.assertIn(("tool_not_granted", "`gog` is not granted", f"agents/{self.AGENT}/AGENTS.md:2"), found)
        self.assertIn(("action_not_granted", "`task_query` `sql` is not granted", f"agents/{self.AGENT}/AGENTS.md:3"), found)
        self.assertIn(("unknown_action", "`filesystem` has no action `tail`", f"agents/{self.AGENT}/AGENTS.md:4"), found)
        self.assertIn(("unknown_action", "no tool has an action `explode`", f"agents/{self.AGENT}/AGENTS.md:5"), found)
        self.assertEqual(len(found), 5, found)

    def test_skill_text_mentions_are_not_uses(self):
        self.skill("helper", 'Another agent has `gog`.\nThen call `gog("calendar list")`.\n```json\n{"action": "upsert_x"}\n```')
        self.agent({"skills": {"helper": {}}})
        self.assertEqual(self.messages("tool_not_granted"), ["`gog` is not granted"])
        self.assertEqual(self.messages("unknown_action"), ["no tool has an action `upsert_x`"])

    def test_the_agents_own_tools_and_missing_ones(self):
        self.file("agents/test-agent/tools/own_tool.py", OWN_TOOL)
        self.agent({"tools": {"own_tool": {}, "ghost": {}}},
                   AGENTS_md="Use own_tool `go`, never own_tool `stop`.")
        report = self.report()
        self.assertIn("own_tool", report.budget.tools)
        self.assertEqual([f.message for f in report.findings], [
            "`own_tool` has no action `stop`",
            "`ghost` is granted but is in neither tools/ nor agents/test-agent/tools/",
            "its tools (own_tool) have no WORKFLOW.md: a `## <tool>` section each, with examples "
            "(core/tooling/workflow.py)",
        ])

    def test_load_skill_is_bound_only_while_a_skill_is_left_to_load(self):
        self.skill("small", "Do the thing.")
        self.agent({"skills": {"small": {"preload": "full"}}}, AGENTS_md='Call `load_skill("small")` first.')
        self.assertEqual(self.messages("tool_not_granted"), ["`load_skill` is not granted"])
        self.agent({"skills": {"small": {}}}, AGENTS_md='Call `load_skill("small")` first.')
        self.assertEqual(self.messages("tool_not_granted"), [])

    def test_unresolved_bundles(self):
        self.agent({"tools": {"filesystem": {"pkm": ["@readx"]}}})
        self.assertEqual(self.messages("unresolved_bundle"),
                         ["`filesystem` grants @readx, which the loader can't expand, so they grant nothing"])


class TestConfig(LintRepo):

    def test_schedule_preconditions_are_core_or_the_agents_scans(self):
        self.file("agents/test-agent/preconditions/my_scan.py", "")
        entry = {"kind": "prompt", "cron": "0 9 * * *", "prompt": "p"}
        self.agent({"schedules": [
            {**entry, "id": "a", "precondition": {"type": "files_exist", "paths": ["x"]}},
            {**entry, "id": "b", "precondition": {"type": "my_scan"}},
            {**entry, "id": "c", "precondition": {"type": "market_screen"}},
        ]})
        self.assertEqual(self.messages("unknown_precondition"), [
            "schedule c: `market_screen` is neither a core precondition nor a scan in "
            "agents/test-agent/preconditions/"])
        self.assertEqual(self.report().schedules,
                         ["a (prompt, files_exist, cron 0 9 * * *)", "b (prompt, my_scan, cron 0 9 * * *)",
                          "c (prompt, market_screen, cron 0 9 * * *)"])

    def test_unloaded_files(self):
        self.agent(IDENTITIY_md="Name: T")
        self.assertEqual(self.found("unloaded_file"), [
            ("unloaded_file", "IDENTITIY.md is not one of the files the prompt loads", f"agents/{self.AGENT}/IDENTITIY.md")])

    def test_workflow_md_is_read_on_demand_not_unloaded(self):
        self.agent(WORKFLOW_md=WORKFLOW)
        self.assertEqual(self.found("unloaded_file"), [])


class TestDenyKeys(LintRepo):
    """`!` keys refuse their actions under a path (core/permissions/denies.py); a malformed one refuses less."""

    def test_well_formed_deny_keys(self):
        self.folder("agents")
        self.folder(f"pkm/agents/{self.AGENT}")
        self.agent({"tools": {"filesystem": {"agents/": ["@manage"], "!agents/*/tools/": ["@change"],
                                             "!pkm/agents/<agent_id>/": ["delete"]}}})
        self.assertEqual(self.found("malformed_deny"), [])

    def test_malformed_deny_keys(self):
        self.folder("pkm/wallet/finance")
        self.agent({"tools": {"filesystem": {
            "pkm": ["@read"],
            "!": ["@change"],
            "!pkm/wallet/": ["wipe", "@chnage"],
            "!pkm/walet/*/x/": ["write"],
            "!pkm/agents/": [],
            "!pkm/wallet/finance/": "write",
        }}})
        where = f"agents/{self.AGENT}/agent.json"
        self.assertEqual(self.found("malformed_deny"), [
            ("malformed_deny", "`filesystem` `!`: no path after the '!'", where),
            ("malformed_deny", "`filesystem` `!pkm/wallet/`: it has no action `wipe`", where),
            ("malformed_deny", "`filesystem` `!pkm/wallet/`: `@chnage` is not one of its bundles "
                               "(@change, @manage, @read, @write)", where),
            ("malformed_deny", "`filesystem` `!pkm/walet/*/x/`: pkm/walet does not exist: check the path for a typo",
             where),
            ("malformed_deny", "`filesystem` `!pkm/agents/`: it lists no action, so it refuses nothing", where),
            ("malformed_deny", "`filesystem` `!pkm/wallet/finance/`: its value must be a list of actions", where),
        ])
        self.assertEqual(self.found("unresolved_bundle"), [], "a deny key's bundle is malformed_deny's to report")

    def test_deny_keys_in_a_skill_count(self):
        self.skill("guard", "Guard the ledger.", {"filesystem": {"!pkm/ledger/": ["write"]}})
        self.agent({"skills": {"guard": {}}})
        self.assertEqual(self.found("malformed_deny"), [
            ("malformed_deny", "`filesystem` `!pkm/ledger/`: pkm/ledger does not exist: check the path for a typo",
             "skills/guard/skill.json")])

    def test_a_tool_that_matches_its_own_selectors_ignores_deny_keys(self):
        self.file("tools/lights.py", MATCHER_TOOL)
        self.agent({"tools": {"lights": {"light.*": ["turn_on"], "!light.porch": ["turn_on"]}}})
        self.assertEqual(self.messages("malformed_deny"), [
            "`lights` `!light.porch`: this tool matches its own selectors, so deny keys don't apply to it"])


class TestContract(LintRepo):
    """An agent with tools has a WORKFLOW.md and keeps the contract (core/tooling/workflow.py)."""

    def test_an_agent_with_tools_needs_a_workflow(self):
        self.file("agents/test-agent/tools/credit_windows.py", AGENT_TOOL)
        self.file("agents/test-agent/tools/__init__.py", "")
        self.agent({"tools": {"credit_windows": {}}})
        self.assertEqual(self.found("workflow_missing"), [(
            "workflow_missing",
            "its tools (credit_windows) have no WORKFLOW.md: a `## <tool>` section each, with examples "
            "(core/tooling/workflow.py)",
            "agents/test-agent/WORKFLOW.md")])

    def test_no_tools_no_workflow(self):
        self.file("agents/test-agent/tools/__init__.py", "")
        self.agent()
        self.assertEqual(self.found("workflow_missing") + self.found("contract_gap"), [])

    def test_a_workflow_that_keeps_the_contract(self):
        self.file("agents/test-agent/tools/credit_windows.py", AGENT_TOOL)
        self.file("agents/test-agent/lib/rules.py", RULES_PY.replace('Rule("cpp_floor", 1.5, "cents per point '
                                                                     'a redemption must beat"),\n', ""))
        self.agent({"tools": {"credit_windows": {}, "filesystem": {"pkm/wallet/credit-card": ["@read"]}}},
                   WORKFLOW_md=WORKFLOW)
        self.assertEqual(self.report().findings, [])

    def test_contract_gaps_are_read_from_the_source(self):
        self.file("agents/test-agent/tools/credit_windows.py", AGENT_TOOL.replace("    Leaves out:", "    Skips:"))
        self.file("agents/test-agent/tools/own_tool.py", OWN_TOOL)
        self.file("agents/test-agent/tools/half_done.py", "def half_done(report:\n")
        self.file("agents/test-agent/lib/rules.py", RULES_PY)
        self.agent(WORKFLOW_md=WORKFLOW.split("```python")[0])
        found = self.found("contract_gap")
        self.assertIn(("contract_gap", "doesn't parse (SyntaxError: '(' was never closed (<unknown>, line 1))",
                       "agents/test-agent/tools/half_done.py"), found)
        self.assertIn(("contract_gap", "the docstring has no 'Leaves out:' line",
                       "agents/test-agent/tools/credit_windows.py"), found)
        self.assertIn(("contract_gap", "no @agent_tool named own_tool", "agents/test-agent/tools/own_tool.py"), found)
        self.assertIn(("contract_gap", "no examples (```python blocks with >>> lines)",
                       "agents/test-agent/WORKFLOW.md"), found)
        self.assertIn(("contract_gap", "rule 'cpp_floor' is never named", "agents/test-agent/WORKFLOW.md"), found)
        self.assertEqual(len(found), 5, found)

    def test_a_missing_section(self):
        self.file("agents/test-agent/tools/credit_windows.py", AGENT_TOOL)
        self.agent(WORKFLOW_md=WORKFLOW.replace("## credit_windows", "## credit windows, in words"))
        self.assertEqual(self.messages("contract_gap"), ["no '## credit_windows' section"])

    def test_a_lib_rules_that_does_not_import(self):
        self.file("agents/test-agent/lib/rules.py", "import nowhere_to_be_found\n")
        self.file("pkm/agents/test-agent/RULES.md", "- alert_days: 10\n")
        self.agent(WORKFLOW_md=WORKFLOW)
        error = "doesn't import (ModuleNotFoundError: No module named 'nowhere_to_be_found')"
        self.assertEqual(self.found("contract_gap"), [("contract_gap", error, "agents/test-agent/lib/rules.py")])
        self.assertEqual(self.found("rules_mismatch"), [
            ("rules_mismatch", f"can't be checked: lib/rules.py {error}", "pkm/agents/test-agent/RULES.md")])

    def test_rules_that_are_not_a_tuple_of_rule(self):
        self.file("agents/test-agent/lib/rules.py", "RULES = [('alert_days', 14)]\n")
        self.agent(WORKFLOW_md=WORKFLOW)
        self.assertEqual(self.messages("contract_gap"), ["RULES must be a non-empty tuple of Rule"])


class TestRulesFile(LintRepo):
    """pkm/agents/<agent_id>/RULES.md sets the keys lib/rules.py declares, and no others."""

    def test_keys_against_lib_rules(self):
        self.file("agents/test-agent/lib/rules.py", RULES_PY)
        self.file("pkm/agents/test-agent/RULES.md",
                  "# test-agent: rules\n\n- alert_days: ten  # days before a credit lapses\n- cpp_flor: 1.2\n")
        self.agent()
        where = "pkm/agents/test-agent/RULES.md"
        self.assertEqual(self.found("rules_mismatch"), [
            ("rules_mismatch", "line 3: 'alert_days: ten' can't be read (expected a whole number, got 'ten'); "
                               "using the default 14", where),
            ("rules_mismatch", "line 4: unknown rule 'cpp_flor'; ignored", where),
            ("rules_mismatch", "doesn't set `cpp_floor`; the default 1.5 applies", where),
        ])

    def test_a_matching_file(self):
        self.file("agents/test-agent/lib/rules.py", RULES_PY)
        self.file("pkm/agents/test-agent/RULES.md", "- alert_days: 10\n- cpp_floor: 1.8  # my line\n")
        self.agent()
        self.assertEqual(self.found("rules_mismatch"), [])

    def test_no_rules_md_is_fine(self):
        self.file("agents/test-agent/lib/rules.py", RULES_PY)
        self.agent()
        self.assertEqual(self.found("rules_mismatch"), [])

    def test_a_rules_md_without_lib_rules(self):
        self.file("pkm/agents/test-agent/RULES.md", "- alert_days: 10\n- cpp_floor: 1.8\n")
        self.agent()
        self.assertEqual(self.messages("rules_mismatch"), [
            "sets `alert_days`, `cpp_floor`, but agents/test-agent/lib/rules.py doesn't exist, so no tool reads them"])


class TestReadGrants(LintRepo):
    """The agent may read every source its tools read: a tool is a shortcut, not the only path."""

    def setUp(self):
        super().setUp()
        self.folder("pkm/wallet/credit-card")
        self.file("agents/test-agent/tools/credit_windows.py", AGENT_TOOL)

    def test_a_source_without_a_read_grant(self):
        self.agent({"tools": {"credit_windows": {}, "filesystem": {"pkm/wallet/finance": ["@read"]}}})
        self.assertEqual(self.found("read_not_granted"), [(
            "read_not_granted",
            "`credit_windows` reads pkm/wallet/credit-card/*.md, but no filesystem grant lets the agent read it",
            "agents/test-agent/tools/credit_windows.py")])

    def test_append_is_not_a_read(self):
        self.agent({"tools": {"filesystem": {"pkm/wallet/credit-card": ["append"]}}})
        self.assertEqual(len(self.found("read_not_granted")), 1)

    def test_a_read_grant_from_a_skill_counts(self):
        self.skill("cards", "Card rules.", {"filesystem": {"pkm/wallet": ["@read"]}})
        self.agent({"skills": {"cards": {}}})
        self.assertEqual(self.found("read_not_granted"), [])

    def test_a_tool_without_a_reads_line_is_not_checked(self):
        self.file("agents/test-agent/tools/credit_windows.py", OWN_TOOL.replace("own_tool", "credit_windows"))
        self.agent()
        self.assertEqual(self.found("read_not_granted"), [])

    def test_the_agents_own_pkm_folder_is_always_readable(self):
        self.file("agents/test-agent/tools/credit_windows.py",
                  AGENT_TOOL.replace("pkm/wallet/credit-card/*.md", "pkm/agents/<agent_id>/cards.md"))
        self.agent()
        self.assertEqual(self.found("read_not_granted"), [])


class TestPartialMigration(LintRepo):
    """Other agents are mid-change while this runs: their state is findings, never a crash."""

    def test_a_half_migrated_agent_is_reported(self):
        self.file("agents/test-agent/tools/broken.py", "from core.tooling import agent_tool\n@agent_tool\ndef broken(:\n")
        self.file("agents/test-agent/tools/no_function.py", "x = 1\n")
        with open(self.file("agents/test-agent/tools/binary.py"), "wb") as f:
            f.write(b"\xff\xfe\x00")
        self.file("agents/test-agent/lib/rules.py", "raise RuntimeError('mid-edit')\n")
        self.file("pkm/agents/test-agent/RULES.md", "not a rules file\n  - stray: 1\n")
        self.agent({"tools": {"broken": {}}}, WORKFLOW_md="")
        found = self.found()
        self.assertIn(("contract_gap", "doesn't parse (can't be read)", "agents/test-agent/tools/binary.py"), found)
        self.assertIn(("contract_gap", "no @agent_tool named no_function", "agents/test-agent/tools/no_function.py"),
                      found)
        self.assertIn(("rules_mismatch", "can't be checked: lib/rules.py doesn't import (RuntimeError: mid-edit)",
                       "pkm/agents/test-agent/RULES.md"), found)

    def test_every_new_check_survives_an_empty_agent_folder(self):
        self.agent()
        self.assertEqual(self.found(), [])


class TestBudgets(LintRepo):

    def test_persona_ceilings_and_totals(self):
        self.agent(AGENTS_md="a" * 2600, SOUL_md="s" * 100)
        report = self.report()
        self.assertEqual(report.budget.persona, {"AGENTS.md": 2600, "SOUL.md": 100})
        self.assertEqual(self.messages("over_budget"), ["AGENTS.md is 2,600 chars (ceiling 2,500)"])

    def test_usermd_is_read_from_the_pkm(self):
        self.file(f"pkm/agents/{self.AGENT}/USER.md", "p" * 900)
        self.agent()
        self.assertEqual(self.report().budget.persona, {"USER.md": 900})
        self.assertEqual(self.found("over_budget"), [
            ("over_budget", "USER.md is 900 chars (ceiling 800)", f"pkm/agents/{self.AGENT}/USER.md")])

    def test_a_usermd_in_the_repo_is_flagged_and_not_counted(self):
        self.agent(USER_md="Lives at 1 Main St.")
        self.assertEqual(self.report().budget.persona, {})
        self.assertEqual(self.found("unloaded_file"), [(
            "unloaded_file",
            f"USER.md belongs in pkm/agents/{self.AGENT}/USER.md: the prompt reads it there, and the repo is public",
            f"agents/{self.AGENT}/USER.md")])

    def test_preloads(self):
        self.skill("big", "x" * (PRELOAD_TOKEN_BUDGET * 4 + 40))
        self.skill("flat", "No sections here.")
        self.agent({"skills": {"big": {"preload": "full"}, "flat": {"preload": "core"}}})
        report = self.report()
        self.assertEqual(report.budget.preload, {"big (full)": PRELOAD_TOKEN_BUDGET + 10})
        self.assertEqual(self.messages("preload_missing"), ["`flat` is preloaded as core but has no '## Core' section"])
        self.assertEqual(self.messages("over_budget"),
                         [f"preloaded skills are ~{PRELOAD_TOKEN_BUDGET + 10:,} tokens (cap {PRELOAD_TOKEN_BUDGET:,})"])

    def test_an_on_device_agent_must_fit_the_local_window(self):
        self.agent({"provider": "local"}, AGENTS_md="a" * 2400, INSTRUCTIONS_md="i" * 2400, SOUL_md="s" * 1400)
        self.assertTrue(any("on-device" in m for m in self.messages("over_budget")))


class TestHelpers(unittest.TestCase):

    def test_actions_in_module(self):
        source = '''
READ_ACTIONS = {"sleep": "s", "weight": "w"}
WRITE_IMPLEMENTED = frozenset({"call_service"})
PERMISSION_BUNDLES = {"@observe": sorted(READ_ACTIONS), "@log": ["log_workout"]}


def thing(action: str = "search", other: str = "x"):
    norm_action = action.lower()
    if norm_action in ("trips", "get_trips"):
        return 1
    if action == "sql":
        return 2
'''
        self.assertEqual(actions_in_module(source, "thing"),
                         {"sleep", "weight", "call_service", "log_workout", "search", "trips", "get_trips", "sql"})
        self.assertEqual(actions_in_module("def gog(command): return command", "gog"), set())
        self.assertEqual(actions_in_module("def broken(:", "broken"), set())

    def test_merge_follows_the_tools_loader(self):
        merged = {"filesystem": {"pkm/a": ["read"]}, "task_query": ["search"], "gog": {}}
        lint._merge(merged, {"filesystem": {"pkm/a": ["write"], "pkm/b": ["read"]}, "task_query": ["sql"],
                             "gog": {"x": ["y"]}, "web_search": {}})
        self.assertEqual(merged, {"filesystem": {"pkm/a": ["read", "write"], "pkm/b": ["read"]},
                                  "task_query": ["search", "sql"], "gog": {"x": ["y"]}, "web_search": {}})

    def test_granted_actions(self):
        self.assertIsNone(lint.granted_actions({}))
        self.assertIsNone(lint.granted_actions(["*"]))
        self.assertEqual(lint.granted_actions(["search"]), {"search"})
        self.assertEqual(lint.granted_actions({"a": ["read"], "b": ["write"]}), {"read", "write"})

    def test_render_collapses_repeats(self):
        repeated = [Finding("phantom_skill", "`memory` is not a skill", f"a.md:{n}") for n in (1, 2)]
        report = AgentReport("x", repeated + [Finding("over_budget", "big", "b.md")], Budget(), {"s": {"preload": "core"}}, [])
        text = report.render()
        self.assertIn("x: 2 findings", text)
        self.assertIn("- phantom_skill: `memory` is not a skill (a.md:1, +1 more)", text)
        self.assertIn("skills: s (core)", text)
        self.assertIn("schedules: none", text)
        self.assertTrue(render([report, report]).startswith("2 agents, 4 findings"))
        self.assertEqual(render([report]), text)

    def test_persona_limits_match_agent_creation(self):
        with open(os.path.join(lint.PROJECT_ROOT, "skills", "agent_creation", "SKILL.md"), encoding="utf-8") as f:
            rows = re.findall(r"^\| `(\w+\.md)`[^|]*\| (\d+)(?: chars)? \| (\d+) \|$", f.read(), re.MULTILINE)
        self.assertEqual({name: (int(t), int(c)) for name, t, c in rows},
                         {**lint.PERSONA_LIMITS, lint.USER_FILE: lint.USER_LIMIT})

    def test_read_paths_take_the_reads_line_and_its_continuation(self):
        docstring = ("Where every credit stands.\n\n"
                     "Reads: pkm/wallet/credit-card/*.md, `pkm/wallet/My Cards.md` and RULES.md;\n"
                     "    also agents/<agent_id>/data/ (pkm/wallet/credit-card/*.md again).\n"
                     "Computes: totals from pkm/wallet/finance/YYYY-MM.md.\n")
        self.assertEqual(lint.read_paths(docstring), [
            "pkm/wallet/credit-card/*.md", "pkm/wallet/My Cards.md", "agents/<agent_id>/data"])
        self.assertEqual(lint.read_paths("Reads: Zillow, and the tasks database.\nComputes: x."), [])
        self.assertEqual(lint.read_paths("No contract lines yet."), [])
        self.assertEqual(lint.read_paths(""), [])

    def test_deny_problems(self):
        actions, bundles = {"read", "write"}, {"@read", "@change"}
        self.assertEqual(lint.deny_problems("!agents/*/lib/", ["@change", "write"], actions, bundles), [])
        self.assertEqual(lint.deny_problems("!  ", ["write"], actions, bundles), ["no path after the '!'"])
        self.assertEqual(lint.deny_problems("!x/", [3], actions, bundles), ["3 is not an action name"])
        self.assertEqual(lint.deny_problems("!x/", ["@read"], actions, set()),
                         ["`@read` is not one of its bundles (it declares none)"])
        self.assertEqual(lint.deny_problems("!x/", ["@any", "any"], None, None), [], "an unknown tool isn't checked")
        self.assertEqual(lint.deny_problems("!x/", ["write"], actions, bundles, matched=True),
                         ["this tool matches its own selectors, so deny keys don't apply to it"])

    def test_permission_declarations(self):
        with open(os.path.join(lint.PROJECT_ROOT, "tools", "filesystem.py"), encoding="utf-8") as f:
            self.assertEqual(lint.permission_declarations(f.read()), ({"@read", "@write", "@manage", "@change"}, False))
        self.assertEqual(lint.permission_declarations(MATCHER_TOOL), (set(), True))
        self.assertEqual(lint.permission_declarations("PERMISSION_MATCHER = staticmethod(len)"), (set(), True))
        self.assertEqual(lint.permission_declarations("def broken(:"), (set(), False))

    def test_own_tool_reads_the_decorator_and_docstring_from_source(self):
        tool = lint.own_tool("credit_windows", AGENT_TOOL)
        self.assertTrue(tool.agent_tool)
        self.assertTrue(tool.docstring.startswith("Where every recurring statement credit stands today.\n\nReads: "))
        self.assertEqual(lint.own_tool("credit_windows", "def other(report):\n    pass\n"), lint.OwnTool("credit_windows"))
        self.assertTrue(lint.own_tool("x", "import core.tooling as t\n@t.agent_tool\ndef x(report):\n    'D.'\n").agent_tool)

    def test_rules_file_problems_cover_tables(self):
        from core.tooling import Rule

        rules = (Rule("shelf_life_days", {"milk": 7}, "days an item keeps, by name"), Rule("alerts", True, "alert me"))
        text = "- shelf_life_days:\n  - milk: a week\n- alerts: maybe\n"
        self.assertEqual(lint.rules_file_problems(text, rules), [
            "line 2: 'milk: a week' can't be read (expected a whole number, got 'a week'); "
            "shelf_life_days keeps its default for it",
            "line 3: 'alerts: maybe' can't be read (expected yes or no, got 'maybe'); using the default yes",
        ])
        self.assertEqual(lint.rules_file_problems("", rules), [
            "doesn't set `shelf_life_days`; the default table applies", "doesn't set `alerts`; the default yes applies"])


class TestRealRepo(unittest.TestCase):

    def test_every_agent_lints(self):
        reports = lint.lint_agents(measure_schemas=False)
        self.assertEqual([r.agent_id for r in reports], lint.agent_ids())
        for report in reports:
            with self.subTest(agent=report.agent_id):
                self.assertTrue(report.render().startswith(report.agent_id))

    def test_agent_designer_follows_its_own_rules(self):
        report = lint_agent("agent-designer", measure_schemas=False)
        self.assertEqual(report.findings, [], report.render())
        self.assertIn("agent_lint", report.budget.tools)
        self.assertLessEqual(report.budget.preload_tokens, PRELOAD_TOKEN_BUDGET)

    def test_unknown_agent(self):
        with self.assertRaises(LookupError):
            lint_agent("no-such-agent", measure_schemas=False)

    def test_merged_grants_match_the_tools_loader(self):
        """agent_lint merges grants itself; the runtime's merge is the reference."""
        from core.loaders.agents_loader import AgentsLoader
        from core.loaders.skills_loader import SkillsLoader
        from core.loaders.tools_loader import ToolsLoader
        from tests.helpers import make_context

        saved = AgentsLoader._instance, ToolsLoader._instance
        self.addCleanup(lambda: (setattr(AgentsLoader, "_instance", saved[0]), setattr(ToolsLoader, "_instance", saved[1])))
        SkillsLoader().clear_skills_cache()

        def normal(grants):
            return {tool: {k: sorted(set(v)) if isinstance(v, list) else v for k, v in scope.items()}
                    if isinstance(scope, dict) else sorted(set(scope)) if isinstance(scope, list) else scope
                    for tool, scope in grants.items()}

        for agent_id in lint.agent_ids():
            with open(os.path.join(lint.PROJECT_ROOT, "agents", agent_id, "agent.json"), encoding="utf-8") as f:
                config = json.load(f)
            fake = MagicMock()
            fake.get_agent.return_value.config = config
            AgentsLoader._instance, ToolsLoader._instance = fake, None
            with self.subTest(agent=agent_id):
                expected = ToolsLoader()._merge_tool_permissions(make_context(agent_id=agent_id))
                self.assertEqual(normal(lint.merged_grants(config, agent_id)), normal(expected))


class TestDenies(unittest.TestCase):
    """The audit subtracts deny entries the way check_permission refuses them (core/permissions/denies.py)."""

    def test_path_grants_subtract_denied_actions(self):
        designer = {
            "agents/": ["@manage"],
            "!agents/*/tools/": ["@change"],
            "!agents/*/preconditions/": ["@change"],
            "!agents/*/lib/": ["@change"],
            "!agents/*/WORKFLOW.md": ["@change"],
        }
        scope = expand_permissions({"filesystem": designer})["filesystem"]
        grants = lint._PathGrants(scope, "agent-designer", lint.PROJECT_ROOT)
        self.assertIn("write", grants.actions("agents/fitness-coach/AGENTS.md"))
        tools = grants.actions("agents/fitness-coach/tools/workout_setup.py")
        self.assertIn("read", tools)
        self.assertNotIn("write", tools)
        workflow = grants.actions("agents/<agent_id>/WORKFLOW.md")
        self.assertIn("read", workflow)
        self.assertNotIn("replace_block", workflow)
        self.assertNotIn("!agents/*/tools/", grants.granted_inside("agents"))


if __name__ == "__main__":
    unittest.main()
