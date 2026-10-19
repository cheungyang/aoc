"""Permissions: what an agent's tool grants mean.

ToolsLoader merges an agent's grants (agent.json, its skills, its graph) and
checks every tool call against them with these modules. agent-designer's
agent_lint expands and checks grants with the same ones, so the audit and
the runtime agree.

- bundles.py: `@` names (`@read`, `@write`, ...) expanded into actions.
- matchers.py: selector matching for tools whose targets aren't paths.
- denies.py: `"!<path>"` keys, which refuse actions under a path.
- declarations.py: reads what a tool module declares about itself
  (`PERMISSION_BUNDLES`, `PERMISSION_MATCHER`), failing closed.
"""
