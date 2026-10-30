"""Running the scripts in `scripts/`, and checking they are fit to be scheduled.

Two jobs live here:

1. **Load-time verification, without executing anything.** A scheduled script
   must expose a module-level `has_work(ctx) -> (bool, reason)` and keep its
   side effects behind `if __name__ == "__main__":`. Both are checked by parsing
   the script's AST, so validating a schedule never runs the script's code.

2. **Fire time.** `has_work()` is called in-process (that is why the
   `__main__` guard matters — importing the module must be harmless), and only
   if it says yes is the script launched as a subprocess.

The subprocess contract is the one the script-executor always had: the script's
stdout *is* the channel message, empty stdout posts nothing, and a non-zero exit
reports stderr.

A script run on request (`!python`, core/runtime/commands/typed/python.py)
goes through `run_script` instead, which keeps everything the run produced:
its exit status, both streams and how long it took. Whether such a run may go
ahead without a confirmation is `write_reason`, read from the file the same
way as the checks above, without importing it.
"""
import ast
import asyncio
import importlib.util
import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from core.scheduler.spec import ScheduleContext, ScriptStep, WorkDecision

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")

def default_timeout() -> int:
    return int(os.getenv("AOC_SCRIPT_TIMEOUT", "300"))


# --------------------------------------------------------------------------
# Parsing and path resolution
# --------------------------------------------------------------------------

def parse_step(text: str) -> ScriptStep:
    """`"coding_tick.py --max-tasks 2"` -> ScriptStep("coding_tick.py", ("--max-tasks", "2"))."""
    parts = shlex.split(str(text or ""))
    if not parts:
        raise ValueError("empty script entry")
    return ScriptStep(script=parts[0], args=tuple(parts[1:]))


def resolve_script_path(name: str, scripts_dir: Optional[str] = None) -> str:
    """The absolute path of a script that must live directly in `scripts/`."""
    base = scripts_dir or SCRIPTS_DIR
    if not name or os.path.basename(name) != name or name in (".", ".."):
        raise ValueError(f"'{name}' must be a bare file name inside scripts/")
    if not name.endswith(".py"):
        raise ValueError(f"'{name}' is not a Python script (.py)")
    return os.path.join(base, name)


# --------------------------------------------------------------------------
# Load-time AST verification
# --------------------------------------------------------------------------

def _is_main_guard(node: ast.AST) -> bool:
    """`if __name__ == "__main__":` in either operand order."""
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    operands = [test.left, test.comparators[0]]
    has_name = any(isinstance(o, ast.Name) and o.id == "__name__" for o in operands)
    has_main = any(isinstance(o, ast.Constant) and o.value == "__main__" for o in operands)
    return has_name and has_main


def _is_sys_path_call(call: ast.Call) -> bool:
    """`sys.path.insert(...)` / `sys.path.append(...)`: import bootstrapping, harmless."""
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in ("insert", "append")
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "path"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "sys"
    )


_DECLARATIONS = (
    ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
    ast.ClassDef, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Pass,
)


def _top_level_violations(body: List[ast.stmt]) -> List[str]:
    """Statements that would *do* something when the module is imported.

    Declarations, docstrings and `sys.path` bootstrapping are fine. A bare call
    such as `main()` or `ensure_project_interpreter()` is not: importing the
    module to call `has_work()` would run it. Plain `if`/`try` blocks are
    checked recursively; the `__main__` guard is where side effects belong.
    """
    problems = []
    for node in body:
        if isinstance(node, _DECLARATIONS):
            continue
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant):
                continue
            if isinstance(node.value, ast.Call) and _is_sys_path_call(node.value):
                continue
            problems.append(f"line {node.lineno}: top-level expression runs on import")
            continue
        if isinstance(node, ast.If):
            if _is_main_guard(node):
                continue
            problems.extend(_top_level_violations(node.body))
            problems.extend(_top_level_violations(node.orelse))
            continue
        if isinstance(node, ast.Try):
            problems.extend(_top_level_violations(node.body))
            for handler in node.handlers:
                problems.extend(_top_level_violations(handler.body))
            problems.extend(_top_level_violations(node.orelse))
            problems.extend(_top_level_violations(node.finalbody))
            continue
        problems.append(
            f"line {node.lineno}: top-level {type(node).__name__} runs on import"
        )
    return problems


def check_script_source(source: str, filename: str = "<script>") -> List[str]:
    """Every reason the source is not a schedulable script. Empty means OK."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return [f"does not parse: {e.msg} (line {e.lineno})"]

    errors = []
    has_work = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "has_work"
    ]
    if not has_work:
        errors.append("defines no module-level has_work(ctx)")
    else:
        fn = has_work[-1]
        if isinstance(fn, ast.AsyncFunctionDef):
            errors.append("has_work must be a plain function, not async")
        positional = fn.args.posonlyargs + fn.args.args
        if len(positional) < 1 and fn.args.vararg is None:
            errors.append("has_work must accept a ctx argument")

    if not any(_is_main_guard(n) for n in tree.body):
        errors.append('has no `if __name__ == "__main__":` guard')

    errors.extend(_top_level_violations(tree.body))
    return errors


def check_script(name: str, scripts_dir: Optional[str] = None) -> List[str]:
    """Resolves and AST-checks a script by name. Empty list means schedulable."""
    try:
        path = resolve_script_path(name, scripts_dir)
    except ValueError as e:
        return [str(e)]
    if not os.path.isfile(path):
        return [f"scripts/{name} does not exist"]
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    return [f"scripts/{name}: {problem}" for problem in check_script_source(source, path)]


_UNREADABLE = object()  # a declaration whose value isn't a literal


def _module_constants(path: str, names: Sequence[str]) -> Dict[str, Any]:
    """The values of the script's module-level assignments to `names`, the last
    one winning; _UNREADABLE for one that isn't a literal. Parsed, never
    imported, like the checks above; a file that can't be read or parsed
    declares nothing."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError, ValueError):
        return {}
    found: Dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in names:
                try:
                    found[target.id] = ast.literal_eval(value)
                except (ValueError, TypeError, SyntaxError, RecursionError):
                    found[target.id] = _UNREADABLE
    return found


def declares_read_only(path: str) -> bool:
    """Whether the script says it changes nothing: a module-level `READ_ONLY = True`."""
    return _module_constants(path, ("READ_ONLY",)).get("READ_ONLY") is True


def write_reason(path: str, args: Sequence[str]) -> str:
    """Why running the script with `args` may change something, or "" when it can't.

    A script that changes nothing declares `READ_ONLY = True`, and lists the
    flags that make it write after all in `WRITES_WITH`, such as
    compare_token_stats.py's `WRITES_WITH = ("--archive-all",)`. A flag counts
    as given alone or as `--flag=value`. Whatever can't be read counts as
    writing.
    """
    declared = _module_constants(path, ("READ_ONLY", "WRITES_WITH"))
    if declared.get("READ_ONLY") is not True:
        return "it doesn't declare READ_ONLY = True"
    flags = declared.get("WRITES_WITH", ())
    if isinstance(flags, str):
        flags = (flags,)
    if not isinstance(flags, (tuple, list)) or not all(isinstance(flag, str) for flag in flags):
        return "its WRITES_WITH isn't a list of flags"
    for arg in args:
        for flag in flags:
            if arg == flag or arg.startswith(flag + "="):
                return f"{flag} makes it write"
    return ""


# --------------------------------------------------------------------------
# Fire time: in-process has_work()
# --------------------------------------------------------------------------

_module_cache: Dict[str, tuple] = {}
_module_lock = threading.Lock()


def load_script_module(path: str):
    """Imports a script by path, re-importing only when the file changed."""
    mtime = os.path.getmtime(path)
    with _module_lock:
        cached = _module_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        stem = os.path.splitext(os.path.basename(path))[0]
        spec = importlib.util.spec_from_file_location(f"aoc_scheduled_{stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _module_cache[path] = (mtime, module)
        return module


def call_has_work(
    step: ScriptStep, ctx: ScheduleContext, scripts_dir: Optional[str] = None
) -> WorkDecision:
    """Asks the script. A crash here runs the script anyway (fail open).

    A broken check must not silently stop a job that has work; the run itself
    will surface whatever is wrong, loudly.
    """
    try:
        module = load_script_module(resolve_script_path(step.script, scripts_dir))
        return WorkDecision.coerce(module.has_work(ctx))
    except Exception as e:
        print(
            f"ScheduleRunner: has_work() of {step.script} failed "
            f"({type(e).__name__}: {e}); running it anyway.",
            file=sys.stderr,
        )
        return WorkDecision(True, f"has_work() failed, running anyway: {e}")


# --------------------------------------------------------------------------
# Fire time: the subprocess
# --------------------------------------------------------------------------

@dataclass
class ScriptResult:
    label: str
    ok: bool
    output: str


async def execute_command(
    cmd: List[str],
    label: str,
    *,
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
) -> ScriptResult:
    """Runs one command. stdout is the message; errors never come back silent."""
    timeout_sec = timeout if timeout is not None else default_timeout()
    kwargs = dict(stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=timeout_sec)
    if cwd is not None:
        kwargs["cwd"] = cwd
    try:
        res = await asyncio.to_thread(subprocess.run, cmd, **kwargs)
        # The script's own stdout *is* the message. A script that succeeded and
        # said nothing gets no message at all, and one that did say something
        # gets no banner wrapped around it: on a five-minute schedule both the
        # wrapper and the silence would be the noise.
        return ScriptResult(label, True, (res.stdout or "").strip())
    except subprocess.TimeoutExpired:
        return ScriptResult(label, False, f"Error executing script '{label}': timed out after {timeout_sec}s")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip()
        if not err:
            err = (e.stdout or "").strip() or f"process exited with code {e.returncode}"
        return ScriptResult(label, False, f"Error executing script '{label}': {err}")
    except Exception as e:
        return ScriptResult(label, False, f"Error running script '{label}': {str(e)}")


async def run_step(
    step: ScriptStep,
    scripts_dir: Optional[str] = None,
    timeout: Optional[int] = None,
) -> ScriptResult:
    """Runs a scheduled script with the project interpreter, from the repo root."""
    label = step.label()
    try:
        path = resolve_script_path(step.script, scripts_dir)
    except ValueError as e:
        return ScriptResult(label, False, f"Error running script '{label}': {e}")

    cmd = [sys.executable, path] + [os.path.expanduser(a) for a in step.args]
    return await execute_command(cmd, label, cwd=PROJECT_ROOT, timeout=timeout)


@dataclass
class ScriptRun:
    """Everything a run on request produced (`run_script`)."""
    label: str
    returncode: Optional[int]  # None: it was stopped at the timeout
    stdout: str
    stderr: str
    seconds: float
    timeout: int

    @property
    def timed_out(self) -> bool:
        return self.returncode is None

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _decoded(output) -> str:
    """A stream as text. TimeoutExpired carries bytes even when the run asked for text."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


async def run_script(
    step: ScriptStep,
    scripts_dir: Optional[str] = None,
    timeout: Optional[int] = None,
) -> ScriptRun:
    """Runs a script as `run_step` does -- the project interpreter, from the
    repo root, stdin closed, in a thread -- and keeps what the run produced,
    whatever its outcome: exit status, stdout, stderr and duration. A run
    stopped at the timeout keeps what it had printed by then.

    Raises ValueError for a name `resolve_script_path` refuses.
    """
    path = resolve_script_path(step.script, scripts_dir)
    timeout_sec = timeout if timeout is not None else default_timeout()
    cmd = [sys.executable, path] + [os.path.expanduser(a) for a in step.args]
    started = time.monotonic()
    try:
        res = await asyncio.to_thread(
            subprocess.run, cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=PROJECT_ROOT, timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        return ScriptRun(step.label(), None, _decoded(e.stdout), _decoded(e.stderr),
                         time.monotonic() - started, timeout_sec)
    return ScriptRun(step.label(), res.returncode, res.stdout or "", res.stderr or "",
                     time.monotonic() - started, timeout_sec)


class ProjectScripts:
    """The project's `scripts/` as `!python` runs them: the Scripts port
    (core/runtime/commands/ports.py), which the Discord runner passes in, so
    the command never imports this adapter."""

    def __init__(self, scripts_dir: Optional[str] = None):
        self._scripts_dir = scripts_dir  # None: SCRIPTS_DIR, read when used

    @property
    def root(self) -> str:
        return PROJECT_ROOT

    def path(self, script: str) -> str:
        return resolve_script_path(script, self._scripts_dir)

    def writes(self, path: str, args) -> str:
        return write_reason(path, args)

    async def run(self, script: str, args, timeout: Optional[int] = None) -> ScriptRun:
        return await run_script(ScriptStep(script, tuple(args)), self._scripts_dir, timeout)
