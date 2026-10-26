"""WORKFLOW.md: the contract an agent's tools keep, in plain words, with examples the suite runs.

Every agent with tools of its own gets `agents/<agent_id>/WORKFLOW.md`. It
states every mechanism rule its tools apply (parsing, arithmetic, dates,
limits) in plain words, with worked examples, so the user, the agent (the
`tool_audit` skill) and agent-designer can check a number without reading
code. tests/agents/test_workflow_examples.py runs every example, so the text
can't drift from the code; tests/agents/test_workflow_contract.py checks the
rest (`contract_problems`).

The format:

    # <agent_id>: how its tools work

    ## kitchen_snapshot

    Expiry counts from the acquired date (`入庫日期`) plus the item's shelf
    life (`shelf_life_days` in RULES.md). A row with neither is returned raw,
    never guessed.

    Strawberries bought on Oct 14 keep 3 days, so on Oct 16 one day is left:

    ```python
    >>> from datetime import date
    >>> from agents.<package>.lib.shelf_life import days_left
    >>> days_left(acquired=date(2026, 10, 14), keeps_days=3, today=date(2026, 10, 16))
    1
    ```

- One `## <tool_name>` section per module in the agent's `tools/`. Other `##`
  sections are welcome: a file's format, rules several tools share.
- Each rule in plain words, naming the RULES.md key it reads in backticks.
- After a rule, a worked example: a sentence in words, then a ```python block
  in doctest form. `>>>` lines call the agent's code with literal inputs, and
  the lines under them are the exact result. `...` matches anything, and runs
  of whitespace compare equal. An expected error is written as doctest
  writes it: `Traceback (most recent call last):`, `...`, `ValueError: <message>`.
- Examples call pure functions in `lib/` with literal inputs: no files, no
  network, no clock (pass `today=date(...)`). They run against an empty
  temporary PKM, so code that reads the user's files finds none.
- A file's examples share one namespace, in order: an import in an early block
  holds for the later ones. A python block without `>>>` is an illustration
  and doesn't run.

The contract (`contract_problems`), for an agent that has a WORKFLOW.md:
- each module in `tools/` exposes an `@agent_tool` of the same name, whose
  docstring has a `Reads:`, a `Computes:` and a `Leaves out:` line;
- WORKFLOW.md has a `## <tool>` section for each, and at least one example;
- `lib/rules.py`, when there is one, declares `RULES`: a non-empty tuple of
  `Rule` with unique keys, each named in WORKFLOW.md.
"""
import doctest
import os
import re
import tempfile
import textwrap
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple

WORKFLOW_FILE = "WORKFLOW.md"
DOCSTRING_LINES = ("Reads:", "Computes:", "Leaves out:")
EXAMPLE_LANGUAGES = {"python", "py", "pycon"}

_FENCE = re.compile(r"^\s*(?P<marks>`{3,}|~{3,})(?P<info>.*)$")
_HEADING = re.compile(r"^##\s+(?P<title>.+?)\s*#*\s*$")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class ExampleBlock:
    """A ```python block holding `>>>` lines. `line` is the file line of its first code line."""
    line: int
    code: str


@dataclass
class WorkflowDoc:
    path: str
    text: str
    sections: List[str] = field(default_factory=list)  # the `##` headings, without backticks
    blocks: List[ExampleBlock] = field(default_factory=list)

    def section_names(self) -> List[str]:
        """Each section's leading name: "## `kitchen_inventory`(op, items)" gives "kitchen_inventory"."""
        return [match.group(0) for match in (_NAME.match(title) for title in self.sections) if match]


@dataclass(frozen=True)
class ExampleRun:
    attempted: int
    failed: int
    output: str  # doctest's account of each failure; "" when every example holds


def agent_workflows(agents_dir: Optional[str] = None) -> List[Tuple[str, str]]:
    """(agent_id, path) for every agent folder with a WORKFLOW.md, by id."""
    if agents_dir is None:
        from core.loaders.agent_code import AGENTS_DIR as agents_dir
    if not os.path.isdir(agents_dir):
        return []
    found = []
    for agent_id in sorted(os.listdir(agents_dir)):
        path = os.path.join(agents_dir, agent_id, WORKFLOW_FILE)
        if os.path.isfile(path):
            found.append((agent_id, path))
    return found


def read_workflow(path: str) -> WorkflowDoc:
    with open(path, "r", encoding="utf-8") as f:
        return parse_workflow(f.read(), path)


def parse_workflow(text: str, path: str = WORKFLOW_FILE) -> WorkflowDoc:
    """The `##` sections and example blocks of a WORKFLOW.md's text."""
    doc = WorkflowDoc(path, text)
    fence, language, start, body = None, "", 0, []
    for number, line in enumerate(text.splitlines(), 1):
        match = _FENCE.match(line)
        if fence is None:
            if match:
                fence, start, body = match.group("marks"), number + 1, []
                language = (match.group("info").strip().split() or [""])[0].lower()
            else:
                heading = _HEADING.match(line)
                if heading:
                    doc.sections.append(heading.group("title").replace("`", "").strip())
            continue
        closes = (match and match.group("marks")[0] == fence[0] and len(match.group("marks")) >= len(fence)
                  and not match.group("info").strip())
        if not closes:
            body.append(line)
            continue
        if language in EXAMPLE_LANGUAGES and any(code.lstrip().startswith(">>>") for code in body):
            doc.blocks.append(ExampleBlock(start, "\n".join(body)))
        fence = None
    return doc


def run_examples(doc: WorkflowDoc) -> ExampleRun:
    """Runs every example in `doc` as one doctest, against an empty temporary PKM.

    Failures are reported at their line in WORKFLOW.md.
    """
    parser = doctest.DocTestParser()
    examples = []
    for block in doc.blocks:
        for example in parser.get_examples(textwrap.dedent(block.code), doc.path):
            example.lineno += block.line - 1  # doctest prints lineno + 1: the `>>>` line's file line
            examples.append(example)
    test = doctest.DocTest(examples, {"__name__": "workflow_examples"}, os.path.basename(doc.path), doc.path, 0, None)
    runner = doctest.DocTestRunner(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    output: List[str] = []
    with _empty_pkm():
        result = runner.run(test, out=output.append, clear_globs=True)
    return ExampleRun(result.attempted, result.failed, "".join(output))


def contract_problems(doc: WorkflowDoc, tools: Mapping[str, Any], rules: Optional[Sequence[Any]] = None) -> List[str]:
    """What breaks the contract for one agent; [] when it holds (see the module docstring).

    `tools` maps each module in the agent's tools/ to its attribute of the same
    name (None when missing); `rules` is lib/rules.py's `RULES`, or None when the
    agent has no lib/rules.py.
    """
    from core.tooling.agent_tool import AgentTool
    from core.tooling.rules import Rule

    problems = []
    sections = set(doc.section_names())
    for name, tool in sorted(tools.items()):
        where = f"tools/{name}.py"
        if not isinstance(tool, AgentTool) or tool.name != name:
            problems.append(f"{where}: no @agent_tool named {name}")
            continue
        lines = [line.strip() for line in (tool.description or "").splitlines()]
        for label in DOCSTRING_LINES:
            if not any(line.startswith(label) for line in lines):
                problems.append(f"{where}: the docstring has no '{label}' line")
        if name not in sections:
            problems.append(f"{WORKFLOW_FILE}: no '## {name}' section")
    if not doc.blocks:
        problems.append(f"{WORKFLOW_FILE}: no examples (```python blocks with >>> lines)")

    if rules is None:
        return problems
    if not isinstance(rules, tuple) or not rules:
        return problems + ["lib/rules.py: RULES must be a non-empty tuple of Rule"]
    keys = set()
    for rule in rules:
        if not isinstance(rule, Rule):
            problems.append(f"lib/rules.py: {rule!r} is not a Rule")
            continue
        if rule.key in keys:
            problems.append(f"lib/rules.py: '{rule.key}' is declared twice")
        keys.add(rule.key)
        if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(rule.key)}(?![A-Za-z0-9_])", doc.text):
            problems.append(f"{WORKFLOW_FILE}: rule '{rule.key}' is never named")
    return problems


@contextmanager
def _empty_pkm():
    """Points the PKM and its databases at an empty temporary folder, then back."""
    from core.config.settings import Config

    with tempfile.TemporaryDirectory(prefix="workflow-examples-") as tmp:
        pkm = os.path.join(tmp, "pkm")
        os.makedirs(pkm)
        with Config().overriding(
            pkm_dir=pkm,
            tasks_db_path=os.path.join(pkm, "tasks.db"),
            projects_db_path=os.path.join(pkm, "projects.db"),
            projects_dir=os.path.join(pkm, "vault", "projects"),
        ):
            yield
