"""Interpreter bootstrap for scripts the scheduler runs directly.

`#!/usr/bin/env python3` resolves against whatever PATH the caller has. Under
cron on macOS that is the system python, which has none of this project's
dependencies, so a script would die on its first import — and a scheduled script
that dies posts a traceback on every run.

This module must stay import-safe under *any* python 3: standard library only,
no project imports.
"""
import os
import sys

_REEXEC_FLAG = "AOC_SCRIPT_REEXEC"


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_project_interpreter(probe_module: str = "langgraph") -> None:
    """Re-executes the calling script under the repo's `.venv` if it needs to.

    Does nothing when the current interpreter can already import `probe_module`,
    when there is no `.venv`, or when this has already happened once — the flag
    is what stops a misconfigured venv from spawning an infinite chain.
    """
    if os.environ.get(_REEXEC_FLAG):
        return

    import importlib.util
    if importlib.util.find_spec(probe_module) is not None:
        return

    venv_python = os.path.join(project_root(), ".venv", "bin", "python")
    if not os.path.exists(venv_python):
        return
    if os.path.realpath(venv_python) == os.path.realpath(sys.executable):
        return

    os.environ[_REEXEC_FLAG] = "1"
    script = os.path.abspath(sys.argv[0])
    os.execv(venv_python, [venv_python, script] + sys.argv[1:])


def enter_project_root() -> str:
    """Puts the repo on `sys.path` and makes it the working directory.

    Both graph and manifest paths are resolved relative to the working
    directory, so a cron invocation from anywhere must still act on the repo.
    """
    root = project_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    os.chdir(root)
    return root
