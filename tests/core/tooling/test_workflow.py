"""The WORKFLOW.md runner and contract checks, against a tiny fake agent in a temp folder."""
import importlib
import os
import sys
import tempfile
import unittest

from langchain_core.tools import tool

from core.tooling import Report, Rule, agent_tool
from core.tooling.workflow import agent_workflows, contract_problems, parse_workflow, read_workflow, run_examples
from core.util.config import Config

# The fake agent's lib/: what its examples call.
FAKE_LIB = '''
def recovery_now(stored, hours, per_hour):
    """Recovery points now: the stored value plus the hourly rate, capped at 100."""
    return min(100, stored + hours * per_hour)


def pkm_dir():
    from core.util.config import Config
    return Config().pkm_dir
'''

WORKFLOW = """# fake-coach: how its tools work

## recovery_status

A muscle group regains `recovery_per_hour` points an hour, up to 100.

Stored 40, 10 h at 2 an hour gives 60:

```python
>>> from fake_coach_lib import recovery_now
>>> recovery_now(stored=40, hours=10, per_hour=2)
60
```

The cap holds:

```python
>>> recovery_now(95, 10, 2)
100
```

A file's format, for reading only (not run):

```text
>>> this is not python
```

```python
# An illustration without prompts doesn't run.
recovery_now(1, 2, 3)
```

## Shared rules

Nothing here yet.
"""


@agent_tool
def recovery_status(report: Report) -> str:
    """Where each muscle group's recovery stands.

    Reads: pkm/wallet/exercise/recovery.md.
    Computes: recovery now, from the stored value and the hours since.
    Leaves out: which exercises to pick.
    """
    return ""


@agent_tool
def thin_doc(report: Report) -> str:
    """A tool whose docstring forgets its contract lines.

    Reads: something.
    """
    return ""


@tool
def plain_tool() -> str:
    """A plain LangChain tool, not an agent tool."""
    return ""


RULES = (Rule("recovery_per_hour", 2, "recovery points a group regains per hour"),)


class FakeAgentCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        with open(os.path.join(self.root, "fake_coach_lib.py"), "w") as f:
            f.write(FAKE_LIB)
        sys.path.insert(0, self.root)
        self.addCleanup(sys.path.remove, self.root)
        self.addCleanup(sys.modules.pop, "fake_coach_lib", None)
        importlib.invalidate_caches()

    def workflow(self, text, agent_id="fake-coach"):
        path = os.path.join(self.root, "agents", agent_id, "WORKFLOW.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class TestParse(unittest.TestCase):

    def test_sections_and_runnable_blocks(self):
        doc = parse_workflow(WORKFLOW, "WORKFLOW.md")
        self.assertEqual(doc.sections, ["recovery_status", "Shared rules"])
        self.assertEqual(doc.section_names(), ["recovery_status", "Shared"])
        self.assertEqual([block.line for block in doc.blocks], [10, 18])  # the first code line of each block
        self.assertTrue(doc.blocks[0].code.startswith(">>> from fake_coach_lib import recovery_now"))

    def test_headings_inside_a_block_are_not_sections(self):
        doc = parse_workflow("## a_tool\n````python\n>>> 1\n1\n```\n## not_a_section\n````\n### Detail\n")
        self.assertEqual(doc.sections, ["a_tool"])
        self.assertEqual(len(doc.blocks), 1)
        self.assertEqual(parse_workflow("## `a_tool`(op, items)\n").section_names(), ["a_tool"])


class TestRunExamples(FakeAgentCase):

    def test_the_examples_run_against_the_code(self):
        run = run_examples(read_workflow(self.workflow(WORKFLOW)))
        self.assertEqual((run.attempted, run.failed, run.output), (3, 0, ""))

    def test_a_drifted_example_fails_at_its_line_in_the_file(self):
        drifted = WORKFLOW.replace("recovery_now(95, 10, 2)\n100", "recovery_now(95, 10, 2)\n115")
        path = self.workflow(drifted)
        run = run_examples(read_workflow(path))
        self.assertEqual((run.attempted, run.failed), (3, 1))
        self.assertIn(f'File "{path}", line 18', run.output)
        self.assertIn("Expected:\n    115\nGot:\n    100", run.output)

    def test_examples_see_an_empty_temporary_pkm(self):
        before = Config().pkm_dir
        text = "## x\n```python\n>>> import os\n>>> from fake_coach_lib import pkm_dir\n" \
               ">>> os.listdir(pkm_dir()), pkm_dir() == {!r}\n([], False)\n```\n".format(before)
        run = run_examples(read_workflow(self.workflow(text)))
        self.assertEqual(run.failed, 0, run.output)
        self.assertEqual(Config().pkm_dir, before)

    def test_agent_workflows_lists_the_agents_that_have_one(self):
        self.workflow(WORKFLOW)
        os.makedirs(os.path.join(self.root, "agents", "no-workflow"))
        agents_dir = os.path.join(self.root, "agents")
        self.assertEqual(agent_workflows(agents_dir), [("fake-coach", os.path.join(agents_dir, "fake-coach", "WORKFLOW.md"))])
        self.assertEqual(agent_workflows(os.path.join(self.root, "nowhere")), [])


class TestContract(unittest.TestCase):

    def setUp(self):
        self.doc = parse_workflow(WORKFLOW, "WORKFLOW.md")

    def test_a_tool_that_keeps_the_contract_passes(self):
        self.assertEqual(contract_problems(self.doc, {"recovery_status": recovery_status}, RULES), [])
        self.assertEqual(contract_problems(self.doc, {"recovery_status": recovery_status}), [])

    def test_each_way_to_break_it_is_named(self):
        tools = {
            "recovery_status": recovery_status,
            "missing_tool": None,
            "plain_tool": plain_tool,
            "renamed": recovery_status,
            "thin_doc": thin_doc,
        }
        self.assertEqual(contract_problems(self.doc, tools), [
            "tools/missing_tool.py: no @agent_tool named missing_tool",
            "tools/plain_tool.py: no @agent_tool named plain_tool",
            "tools/renamed.py: no @agent_tool named renamed",
            "tools/thin_doc.py: the docstring has no 'Computes:' line",
            "tools/thin_doc.py: the docstring has no 'Leaves out:' line",
            "WORKFLOW.md: no '## thin_doc' section",
        ])

    def test_a_workflow_needs_examples(self):
        doc = parse_workflow("## recovery_status\nIn words only.\n")
        self.assertEqual(contract_problems(doc, {"recovery_status": recovery_status}),
                         ["WORKFLOW.md: no examples (```python blocks with >>> lines)"])

    def test_rules_are_declared_once_and_each_is_explained(self):
        unexplained = (RULES[0], Rule("deload_after_days", 21, "days off before a deload"), RULES[0])
        self.assertEqual(contract_problems(self.doc, {}, unexplained), [
            "WORKFLOW.md: rule 'deload_after_days' is never named",
            "lib/rules.py: 'recovery_per_hour' is declared twice",
        ])
        self.assertEqual(contract_problems(self.doc, {}, ()), ["lib/rules.py: RULES must be a non-empty tuple of Rule"])
        self.assertEqual(contract_problems(self.doc, {}, [RULES[0]]),
                         ["lib/rules.py: RULES must be a non-empty tuple of Rule"])
        self.assertEqual(contract_problems(self.doc, {}, ("x",)), ["lib/rules.py: 'x' is not a Rule"])


if __name__ == "__main__":
    unittest.main()
