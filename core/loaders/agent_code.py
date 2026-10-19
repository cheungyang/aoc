"""Each agent's own code: `agents/<agent_id>/tools/`, `preconditions/` and `lib/`.

An agent's folder holds its definition (agent.json, AGENTS.md, …) and the code
only it uses: its tools (`tools/<name>.py`), its scans (`preconditions/<type>.py`)
and the helpers they share (`lib/`). Agent ids have hyphens, which Python can't
import, so the code is imported as `agents.<package>`, the id with "-" as "_"
(`agents/__init__.py` maps the name back to the folder).

Shared, general tools stay in `tools/`. An agent's own tools load only for that
agent, when its agent.json or one of its skills grants them (`ToolsLoader`);
its scans resolve only in its own schedules (`build_precondition`).
"""
import os
from typing import Dict, List

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
AGENTS_DIR = os.path.join(PROJECT_ROOT, "agents")
CODE_KINDS = ("tools", "preconditions")


def package_for(agent_id: str) -> str:
    """The import name of `agent_id`'s code: `agents.<this>`."""
    return (agent_id or "").replace("-", "_")


def agents_with_code() -> List[str]:
    """Every agent id whose folder has tools or scans of its own."""
    if not os.path.isdir(AGENTS_DIR):
        return []
    return sorted(
        name for name in os.listdir(AGENTS_DIR)
        if any(os.path.isdir(os.path.join(AGENTS_DIR, name, kind)) for kind in CODE_KINDS)
    )


def modules(agent_id: str, kind: str) -> Dict[str, str]:
    """{module name: dotted path} for the modules in `agents/<agent_id>/<kind>/`."""
    folder = os.path.join(AGENTS_DIR, agent_id, kind)
    if not os.path.isdir(folder):
        return {}
    return {
        filename[:-3]: f"agents.{package_for(agent_id)}.{kind}.{filename[:-3]}"
        for filename in sorted(os.listdir(folder))
        if filename.endswith(".py") and not filename.startswith("_")
    }
