"""Agent definitions, and each agent's own code.

`agents/<agent_id>/` holds an agent's definition (agent.json, AGENTS.md, …) and
the code only it uses: `tools/` (its tools), `preconditions/` (its scans) and
`lib/` (the helpers they share). See core/loaders/agent_code.py.

Agent ids use hyphens (`fitness-coach`), which Python can't import, so the code
is imported as `agents.<package>`, the id with "-" as "_":
`agents.fitness_coach.tools.workout_setup` loads
`agents/fitness-coach/tools/workout_setup.py`. The finder below maps the
package name back to the folder; the folders themselves keep the agent's id.
"""
import importlib.abc
import importlib.machinery
import os
import sys

_AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))


class _AgentFolderFinder(importlib.abc.MetaPathFinder):
    """Finds `agents.<package>` in `agents/<agent-id>/` when the id has a hyphen.

    Only the agent level is mapped. Below it (`tools`, `lib`, ...) the normal
    path machinery takes over, since those folder names are importable.
    """

    def find_spec(self, fullname, path=None, target=None):
        prefix, _, package = fullname.partition(".")
        if prefix != __name__ or not package or "." in package or "_" not in package:
            return None
        folder = os.path.join(_AGENTS_DIR, package.replace("_", "-"))
        if not os.path.isdir(folder):
            return None
        # A namespace package: the agent folder has no __init__.py of its own.
        spec = importlib.machinery.ModuleSpec(fullname, None, is_package=True)
        spec.submodule_search_locations = [folder]
        return spec


if not any(isinstance(finder, _AgentFolderFinder) for finder in sys.meta_path):
    # After the path finders: a folder that is already importable is found the
    # usual way, and only hyphenated ids fall through to this finder.
    sys.meta_path.append(_AgentFolderFinder())
