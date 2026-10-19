# agent-designer: how its tool works

`agent_lint` reads an agent the way the runtime will and reports what would fail, or cost a model round trip, at run time. It never changes a file. Findings come from patterns, so each is a lead to check at the cited line, not a verdict.

## agent_lint

One report per agent: its findings, collapsed to one line per kind and message, then its budgets, tools, skills and schedules. A finding that repeats cites its first place and counts the rest:

```python
>>> from agents.agent_designer.lib.lint import AgentReport, Budget, Finding
>>> repeated = [Finding("phantom_skill", "`memory` is not a skill", f"agents/x/AGENTS.md:{n}") for n in (3, 9)]
>>> print(AgentReport("x", repeated, Budget(), {}, []).render().splitlines()[1])
- phantom_skill: `memory` is not a skill (agents/x/AGENTS.md:3, +1 more)
```

### Grants

Grants merge the way the tools loader merges them: agent.json, then each granted skill's skill.json, then the agent's graph, plus the default grant on the agent's own `pkm/agents/<agent_id>/` folder. Bundles (`@read`, `@write`, `@manage`, `@change`) expand after the merge. A path grant allows the actions of every key that contains the path; a `!` key refuses its actions under its path, whatever else is granted. `{}` or `["*"]` allows every action:

```python
>>> from agents.agent_designer.lib.lint import granted_actions
>>> granted_actions({}) is None
True
>>> sorted(granted_actions({"pkm/wallet": ["read", "ls"], "!pkm/wallet/finance/": ["write"]}))
['ls', 'read']
```

### What exists

A skill exists when `skills/<id>/skill.json` does. A tool exists when it is in `tools/` (shared) or in the agent's own `tools/`. A tool's actions and bundles are read from its source, never by importing it: comparisons against `action`, the default of its `action` argument, literal action tables, and the keys and members of its `PERMISSION_BUNDLES`. A tool with no actions takes no action names, and actions named for it aren't checked:

```python
>>> from agents.agent_designer.lib.lint import actions_in_module, permission_declarations
>>> source = '''
... PERMISSION_BUNDLES = {"@read": ["read", "ls"]}
...
... def task_query(action="search", sql=""):
...     if action == "sql":
...         return sql
... '''
>>> sorted(actions_in_module(source, "task_query"))
['ls', 'read', 'search', 'sql']
>>> permission_declarations(source)
({'@read'}, False)
```

### Text

The agent's own files (the persona files the prompt loads, and its schedule prompts) are held to every mention. Skill text names other agents' tools and skills as examples, so there only directives count: `load_skill("x")`, "call `x(`", "the `x` tool".

- A skill named that doesn't exist is `phantom_skill`; one that exists but isn't in the agent's skills is `skill_not_granted`.
- A tool used that doesn't exist is `unknown_tool`; one that exists but isn't granted is `tool_not_granted`. A tool granted in a config that exists nowhere is `unknown_tool` too.
- An action the tool doesn't have is `unknown_action`; one its grant leaves out is `action_not_granted`, as is a filesystem write named just before a path its grant leaves read-only.
- A repo or PKM path no grant covers is `path_outside_grants`. A folder that doesn't exist is `missing_path`, unless the line creates it (write, append, save, move). A segment such as `<slug>`, `YYYY-MM.md` or `*.md` stands for many, so only the folders before it must exist.

### Config

- A bundle the loader can't expand grants nothing: `unresolved_bundle`.
- A schedule precondition must be a core type or one of the agent's own scans (`preconditions/<type>.py`): `unknown_precondition`.
- A markdown file in the agent folder that the prompt never loads is `unloaded_file`. WORKFLOW.md is read on demand, so it is allowed.

### Deny keys

A `!` key is well formed when a path follows the `!`, it lists at least one action, and each is an action of the tool or one of its bundles. The folder before the first wildcard should exist: the key applies once it appears, but a misspelt path never matches. A tool that matches its own selectors (Home Assistant) ignores deny keys. Each problem is `malformed_deny`:

```python
>>> from agents.agent_designer.lib.lint import deny_problems
>>> deny_problems("!agents/*/tools/", ["@change"], {"read", "write"}, {"@read", "@change"})
[]
>>> for problem in deny_problems("!", ["@chnage", "wipe"], {"read", "write"}, {"@read", "@change"}):
...     print(problem)
no path after the '!'
`@chnage` is not one of its bundles (@change, @read)
it has no action `wipe`
```

### The WORKFLOW.md contract

An agent with tools of its own needs a WORKFLOW.md: `workflow_missing`. Once it has one, the contract test's checks apply (core/tooling/workflow.py), each a `contract_gap`: every tool an `@agent_tool` whose docstring has `Reads:`, `Computes:` and `Leaves out:` lines, a `## <tool>` section for each, at least one example, and each key in `lib/rules.py` named. Tools are read from their source, not imported, so code in the middle of a change is a finding, not a crash:

```python
>>> from agents.agent_designer.lib.lint import own_tool
>>> own_tool("credit_windows", "@agent_tool\ndef credit_windows(report):\n    'Reads: pkm/wallet/credit-card/*.md.'\n")
OwnTool(name='credit_windows', agent_tool=True, docstring='Reads: pkm/wallet/credit-card/*.md.', error='')
>>> own_tool("credit_windows", "@tool\ndef credit_windows(report):\n    'Reads: nothing.'\n").agent_tool
False
>>> print(own_tool("credit_windows", "def credit_windows(report:\n").error)
SyntaxError: ...
```

### Read grants

A tool is a shortcut, not the only path: the agent may read every source its tools read. The paths come from the tool docstring's `Reads:` line and the lines that continue it; names without a folder (RULES.md) and services aren't paths. A path the agent's grants don't let it read is `read_not_granted`:

```python
>>> from agents.agent_designer.lib.lint import read_paths
>>> read_paths("Where every credit stands.\n\nReads: pkm/wallet/credit-card/*.md, and RULES.md\n    for the alert windows.\nComputes: each credit's status.")
['pkm/wallet/credit-card/*.md']
```

### RULES.md

When `pkm/agents/<agent_id>/RULES.md` exists, it must match `lib/rules.py`. The tools ignore an unknown key, a value that doesn't read and a line out of place, so the default applies; a key the file doesn't set takes its default without a word. Each is `rules_mismatch`, as is a RULES.md for an agent with no `lib/rules.py`:

```python
>>> from core.tooling import Rule
>>> from agents.agent_designer.lib.lint import rules_file_problems
>>> rules = (Rule("move_pct", 4.0, "price move, in %, that triggers research"),
...          Rule("earnings_days", 7, "days ahead to flag earnings"))
>>> for problem in rules_file_problems("- move_pct: 3%\n- volume_x: 2\n", rules):
...     print(problem)
line 2: unknown rule 'volume_x'; ignored
doesn't set `earnings_days`; the default 7 applies
```

### Budgets

Persona files against their ceilings, in characters: AGENTS.md and INSTRUCTIONS.md 2,500, SOUL.md 1,500, USER.md 800 (read from `pkm/agents/<agent_id>/USER.md`: a USER.md in the agent's repo folder is `unloaded_file`), IDENTITY.md 400. The persona's total target is 3,500 (shown, not a finding). Preloaded skill text is capped at 1,500 tokens, at 4 characters a token; an on-device agent's static prompt and tool schemas must fit about 2,000 tokens. Each overrun is `over_budget`; a `core` preload without a `## Core` section is `preload_missing`.

```python
>>> from agents.agent_designer.lib.lint import PERSONA_LIMITS, PERSONA_TARGET
>>> PERSONA_LIMITS["AGENTS.md"], PERSONA_TARGET
((1500, 2500), 3500)
```
