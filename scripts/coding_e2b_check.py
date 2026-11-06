#!/usr/bin/env python3
"""Checks, in under a minute, that this host can verify coding tasks on E2B.

E2B is the coding graph's default verify backend, so a host that can't reach it
halts every task at verify. Run this after setting a host up, after moving the
key, or when a task halts with "the `e2b` verify backend isn't set up":

  1. config    the `e2b` SDK imports; an API key is found (the key file,
               chmod 600, or $E2B_API_KEY); which backend and template this
               host resolves to.
  2. projects  each manifest's backend, and any `setup_command` that would fail
               in a sandbox: one that scaffolds or rewrites `package.json`
               changes files the commit doesn't have, which the sandbox rejects.
  3. sandbox   starts a real sandbox, checks the tools the runner relies on
               (curl and tar fetch the commit, git checks that setup left the
               tree alone) and its CPU and memory, then kills it. Costs a few
               sandbox-seconds. Skipped with --offline.
  4. smoke     with --project: runs that project's `setup_command`, then
               `--verify CMD` if given, against a commit on GitHub, through the
               same runner verify uses. This is the run a task's verify does.

Usage:
    scripts/coding_e2b_check.py [--offline]
    scripts/coding_e2b_check.py --project NAME [--ref REF] [--verify "CMD"]
    scripts/coding_e2b_check.py --manifest PATH [--ref REF] [--verify "CMD"]

Exit code 0 means the coding graph can verify on E2B from this host (warnings
allowed). 1 means it can't, and the ✗ lines say why. 2 is a usage error.
"""
import argparse
import asyncio
import os
import re
import shlex
import stat
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import ensure_project_interpreter, enter_project_root  # noqa: E402
from _bootstrap import project_root as _project_root  # noqa: E402

project_root = _project_root()
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# The sandbox class and the runner are looked up through these, so a test can
# hand in fakes without patching the SDK.
SANDBOX_CLASS: Any = None
MAKE_RUNNER: Any = None

# Below this, `npm ci` + vitest/jsdom or a large `pip install` tends to be
# OOM-killed (exit 137). The `base` template has 512 MiB.
MIN_MEMORY_MB = 1024
PROBE_TIMEOUT_SECONDS = 60
SMOKE_TIMEOUT_SECONDS = 600.0
TAIL_LINES = 25

# What the runner needs inside the sandbox: curl and tar fetch the commit, and
# without git the check that setup left the tree alone is silently skipped.
REQUIRED_TOOLS = ("curl", "tar")
PROBED_TOOLS = ("git", "curl", "tar", "node", "npm", "python3")
PROBE_SCRIPT = (
    "for t in " + " ".join(PROBED_TOOLS) + "; do "
    "if command -v \"$t\" >/dev/null 2>&1; then "
    "printf '%s=%s\\n' \"$t\" \"$(\"$t\" --version 2>&1 | head -n 1)\"; "
    "else printf '%s=MISSING\\n' \"$t\"; fi; done; "
    "printf 'cpus=%s\\n' \"$(nproc 2>/dev/null || echo '?')\"; "
    "printf 'mem_mb=%s\\n' \"$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo '?')\""
)

# A `setup_command` that does any of these changes files the pushed commit does
# not have, and the sandbox fails it (graphs/coding/utils/sandbox/e2b_runner.py).
TREE_SMELLS: Tuple[Tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"create-vite|\b(?:npm|yarn|pnpm|bun) create\b|\bnpx (?:--yes )?create-"),
     "scaffolds the project"),
    (re.compile(r"\bcp -[a-zA-Z]*[rR]|\brsync\b"), "copies files over the repository"),
    (re.compile(r"\b(?:npm|pnpm|yarn|bun) (?:install|i|add)\s+(?!-)[^\s&|;]"),
     "adds packages, which rewrites package.json"),
    (re.compile(r"\bnpm (?:install|i)\b(?!\s+[^\s&|;-])"),
     "runs `npm install`, which can rewrite package-lock.json"),
)
# These pass that check but assume the dependencies are already installed,
# which in a fresh sandbox only a custom template can arrange.
TEMPLATE_SMELLS: Tuple[Tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"sys\.prefix"), "links .venv to whatever Python the sandbox has"),
)


class Report:
    """Prints each result as it comes; remembers whether anything failed."""

    def __init__(self) -> None:
        self.failed = False

    def section(self, title: str) -> None:
        print(f"\n{title}")

    def ok(self, message: str) -> None:
        print(f"  ✓ {message}")

    def warn(self, message: str) -> None:
        print(f"  ⚠ {message}")

    def fail(self, message: str) -> None:
        self.failed = True
        print(f"  ✗ {message}")

    def info(self, message: str) -> None:
        print(f"    {message}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Check that this host can verify coding tasks on E2B."
    )
    parser.add_argument("--offline", action="store_true",
                        help="Check configuration only; start no sandbox.")
    parser.add_argument("--project", default="",
                        help="Smoke-test this project: pkm/wiki/software/<project>/build_request.json.")
    parser.add_argument("--manifest", default="",
                        help="Smoke-test the project in this build_request.json.")
    parser.add_argument("--ref", default="",
                        help="Commit, branch or tag to test (default: the repo's default branch).")
    parser.add_argument("--verify", default="",
                        help="A test command to run after setup, e.g. \"npx vitest run\".")
    return parser.parse_args(argv)


# --- 1. config --------------------------------------------------------------

def mask(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else "…"


def key_file_path() -> str:
    """Where the runner looks for the key file (same rule as `read_api_key`)."""
    from graphs.coding.utils.sandbox import e2b_runner

    return os.path.expanduser(
        os.environ.get(e2b_runner.API_KEY_FILE_ENV) or os.path.join(project_root, "e2b_api_key")
    )


def check_config(report: Report) -> Optional[str]:
    """The SDK, the key, the backend and template this host resolves to. Returns the key."""
    from graphs.coding.utils.sandbox import BACKEND_ENV_VAR, e2b_runner, resolve_backend

    report.section("1. Configuration")
    try:
        import importlib.metadata

        import e2b  # noqa: F401

        report.ok(f"e2b SDK {importlib.metadata.version('e2b')} is installed")
    except Exception as e:
        report.fail(f"the `e2b` package can't be imported ({e}). Install requirements.txt.")

    key = ""
    try:
        key = e2b_runner.read_api_key()
    except Exception as e:
        report.fail(f"{e}")

    if key:
        if (os.environ.get(e2b_runner.API_KEY_ENV) or "").strip():
            report.ok(f"API key {mask(key)} from ${e2b_runner.API_KEY_ENV}")
            report.warn(
                f"the key is in the environment, where every subprocess can read it — "
                f"including the worker's bash. Prefer the key file {key_file_path()} (chmod 600)."
            )
        else:
            path = key_file_path()
            report.ok(f"API key {mask(key)} from {path}")
            try:
                mode = stat.S_IMODE(os.stat(path).st_mode)
                if mode & 0o077:
                    report.warn(f"{path} is mode {oct(mode)}; run `chmod 600 {path}`.")
            except OSError:
                pass
        if not key.startswith("e2b_"):
            report.warn("E2B keys start with `e2b_`; this one doesn't. Check it's the API key, not an access token.")

    backend = resolve_backend()
    source = f"${BACKEND_ENV_VAR}" if os.environ.get(BACKEND_ENV_VAR) else "default"
    if backend == "e2b":
        report.ok(f"verify backend on this host: e2b ({source})")
    elif backend == "local":
        report.warn(
            f"verify backend on this host: local ({source}) — projects that don't name a "
            f"backend test on this host. Unset {BACKEND_ENV_VAR} to use E2B."
        )
    else:
        report.fail(f"verify backend `{backend}` from ${BACKEND_ENV_VAR} is unknown. Use e2b or local.")

    template = os.environ.get(e2b_runner.TEMPLATE_ENV) or ""
    report.ok(f"default template: {template or 'base (E2B default: 2 vCPU, 512 MiB)'}"
              + (f" (${e2b_runner.TEMPLATE_ENV})" if template else ""))
    return key or None


# --- 2. projects ------------------------------------------------------------

def setup_smells(command: str, smells=None) -> List[str]:
    return [why for pattern, why in (smells or TREE_SMELLS) if pattern.search(command or "")]


def check_projects(report: Report, manifest_paths: List[str]) -> None:
    """Each project's backend, and setup commands the sandbox would reject."""
    from graphs.coding.utils.manifest import load_manifest
    from graphs.coding.utils.sandbox import resolve_backend
    from graphs.coding.utils.sandbox import e2b_runner

    report.section("2. Projects")
    if not manifest_paths:
        report.info("no project manifests under pkm/wiki/software/<project>/")
        return

    for path in manifest_paths:
        name = os.path.basename(os.path.dirname(path))
        try:
            manifest = load_manifest(path)
        except Exception as e:
            report.fail(f"{name}: the manifest can't be read ({e})")
            continue

        environment = manifest.get("environment") or {}
        backend = resolve_backend(environment)
        where = "manifest" if environment.get("backend") else "host default"
        template = environment.get("template") or os.environ.get(e2b_runner.TEMPLATE_ENV) or "base"
        overrides = [t.get("task_id") for t in manifest.get("queue") or []
                     if t.get("environment") or t.get("setup_command")]

        if backend == "local":
            report.warn(f"{name}: backend local ({where}) — its tests run on this host")
        else:
            report.ok(f"{name}: backend {backend} ({where}), template {template}")
        if overrides:
            report.info(f"tasks with their own environment or setup: {', '.join(map(str, overrides))}")
        if backend != "e2b":
            continue

        setup = manifest.get("setup_command") or ""
        tree = setup_smells(setup, TREE_SMELLS)
        if tree:
            report.warn(
                f"{name}: setup_command {'; '.join(tree)}. The sandbox fails a setup that "
                f"changes files the commit doesn't have — use an install-only command such as "
                f"`npm ci`, and commit anything generated once."
            )
        needs_template = setup_smells(setup, TEMPLATE_SMELLS)
        if needs_template and template == "base":
            report.warn(
                f"{name}: setup_command {'; '.join(needs_template)}, so the tests only find their "
                f"dependencies on a template that has them installed. Set `environment.template`, "
                f"or keep this project on `environment.backend: \"local\"` until there is one."
            )


# --- 3. sandbox -------------------------------------------------------------

def _sandbox_class():
    if SANDBOX_CLASS is not None:
        return SANDBOX_CLASS
    from e2b import AsyncSandbox

    return AsyncSandbox


def parse_probe(output: str) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for line in (output or "").splitlines():
        name, sep, value = line.partition("=")
        if sep:
            found[name.strip()] = value.strip()
    return found


async def probe_sandbox(report: Report, api_key: str, template: Optional[str]) -> None:
    """Starts a sandbox, checks what the runner needs inside it, and kills it."""
    report.section("3. Sandbox")
    started = time.monotonic()
    try:
        sbx = await _sandbox_class().create(
            template=template or None,
            timeout=PROBE_TIMEOUT_SECONDS * 2,
            metadata={"purpose": "coding_e2b_check"},
            api_key=api_key,
        )
    except Exception as e:
        report.fail(f"a sandbox did not start ({e}). Check the key, the template name and your E2B plan.")
        return

    try:
        report.ok(f"sandbox {getattr(sbx, 'sandbox_id', '?')} started in {time.monotonic() - started:.1f}s"
                  f" from template {template or 'base'}")
        try:
            result = await sbx.commands.run(f"bash -c {shlex.quote(PROBE_SCRIPT)}",
                                            timeout=PROBE_TIMEOUT_SECONDS)
            found = parse_probe(getattr(result, "stdout", "") or "")
        except Exception as e:
            report.fail(f"a command could not run in the sandbox ({e})")
            return

        for tool in PROBED_TOOLS:
            version = found.get(tool, "MISSING")
            if version != "MISSING":
                report.ok(f"{tool}: {version}")
            elif tool in REQUIRED_TOOLS:
                report.fail(f"{tool} is missing: the runner can't fetch the commit. Use a template that has it.")
            elif tool == "git":
                report.warn("git is missing: the check that setup left the tree alone will be skipped.")
            else:
                report.info(f"{tool}: not installed (fine unless a project's setup or tests need it)")

        memory = found.get("mem_mb", "?")
        report.info(f"cpus: {found.get('cpus', '?')} · memory: {memory} MiB")
        if memory.isdigit() and int(memory) < MIN_MEMORY_MB:
            report.warn(
                f"{memory} MiB of memory: `npm ci` with vitest/jsdom or a big `pip install` can be "
                f"OOM-killed (exit 137). Consider a template with more (`memory_mb` at build time)."
            )
    finally:
        try:
            await sbx.kill()
        except Exception as e:
            report.warn(f"could not kill sandbox {getattr(sbx, 'sandbox_id', '?')} (it expires on its own): {e}")


# --- 4. smoke ---------------------------------------------------------------

def _make_runner(template: Optional[str]):
    if MAKE_RUNNER is not None:
        return MAKE_RUNNER(template)
    from graphs.coding.utils.sandbox import E2BRunner

    return E2BRunner(template=template)


def _tail(text: str, lines: int = TAIL_LINES) -> List[str]:
    kept = [line for line in (text or "").splitlines() if line.strip()]
    return kept[-lines:]


async def smoke(report: Report, manifest_path: str, ref: str, verify: str) -> None:
    """The project's setup (and `verify`) on a GitHub commit, exactly as verify runs it."""
    from graphs.coding.utils.manifest import load_manifest
    from graphs.coding.utils.repo import get_push_identity, get_repo_descriptor
    from graphs.coding.utils.sandbox import Step

    name = os.path.basename(os.path.dirname(manifest_path))
    report.section(f"4. Smoke test: {name}")
    try:
        manifest = load_manifest(manifest_path)
    except Exception as e:
        report.fail(f"the manifest can't be read ({e})")
        return

    descriptor = get_repo_descriptor(manifest)
    slug = descriptor.get("slug") or ""
    if not slug:
        report.fail("the manifest names no repo.slug, so there is no commit to fetch")
        return
    ref = ref or descriptor.get("default_branch") or "main"

    steps = []
    setup = manifest.get("setup_command") or ""
    if setup:
        steps.append(Step("setup", str(setup)))
    if verify:
        steps.append(Step("verify", verify))
    if not steps:
        report.info("no setup_command and no --verify: nothing to run")
        return

    environment = manifest.get("environment") or {}
    template = environment.get("template") or None
    report.info(f"{slug}@{ref} · template {template or os.environ.get('CODING_E2B_TEMPLATE') or 'base'}")
    for step in steps:
        report.info(f"{step.name}: {step.command}")

    started = time.monotonic()
    result = await _make_runner(template).run(
        steps,
        workspace_path="",  # ignored: the sandbox fetches the commit from GitHub
        timeout=SMOKE_TIMEOUT_SECONDS,
        meta={"task_id": "e2b-check", "head_sha": ref, "repo": slug,
              "push_identity": get_push_identity(manifest)},
    )
    elapsed = time.monotonic() - started

    if result.passed:
        names = " + ".join(step.name for step in steps)
        report.ok(f"{names} passed in {elapsed:.0f}s")
        return
    if result.config_error:
        report.fail(f"the runner isn't configured: {result.config_error}")
    elif result.infra_error:
        report.fail(f"the runner failed, not the project: {result.infra_error}")
    else:
        report.fail(f"`{result.failed_step or 'a step'}` failed (exit {result.exit_code}) after {elapsed:.0f}s")
    for line in _tail(result.stdout) + _tail(result.stderr):
        report.info(f"│ {line}")


# --- main -------------------------------------------------------------------

def target_manifests(args) -> Tuple[List[str], Optional[str]]:
    """(manifests to review, the one to smoke-test or None)."""
    from graphs.coding.utils.dag import discover_manifests, manifest_path_for_project

    if args.manifest:
        path = os.path.abspath(os.path.expanduser(args.manifest))
        return [path], path
    if args.project:
        path = manifest_path_for_project(args.project)
        return [path], path
    return discover_manifests(), None


async def main(argv=None) -> int:
    args = parse_args(argv)
    if (args.verify or args.ref) and not (args.project or args.manifest):
        print("--verify and --ref need --project or --manifest: they say what to smoke-test.", file=sys.stderr)
        return 2

    report = Report()
    print("E2B check for the coding graph")
    api_key = check_config(report)

    manifests, smoke_target = target_manifests(args)
    if smoke_target and not os.path.exists(smoke_target):
        report.section("2. Projects")
        report.fail(f"no manifest at {smoke_target}")
        smoke_target = None
    else:
        check_projects(report, manifests)

    if args.offline:
        print("\n(offline: no sandbox started)")
    elif not api_key:
        print("\nNo sandbox started: there is no API key to start one with.")
    else:
        from graphs.coding.utils.sandbox import e2b_runner

        await probe_sandbox(report, api_key, os.environ.get(e2b_runner.TEMPLATE_ENV) or None)
        if smoke_target and not report.failed:
            await smoke(report, smoke_target, args.ref, args.verify)

    if report.failed:
        print("\n✗ This host can't verify on E2B yet: fix the ✗ lines above and run this again.")
        return 1
    print("\n✓ This host can verify coding tasks on E2B."
          + ("" if not args.offline else " (configuration only — run without --offline to start a sandbox)"))
    return 0


if __name__ == "__main__":
    ensure_project_interpreter()
    enter_project_root()
    sys.exit(asyncio.run(main()))
