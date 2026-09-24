#!/usr/bin/env python3
"""Runs one reconciliation tick of the coding graph, per project.

Invoked by the `script-executor` cron every five minutes. Each project has its
own `pkm/wiki/software/<project>/build_request.json`; with no arguments this
visits every one of them. It prints what the ticks did and nothing at all when
they did nothing — the runner treats empty stdout as "post nothing", which is
what keeps a channel usable when the queues are idle 287 times out of 288 a day.

Exit codes:
    0  the tick ran (whether or not it had work to do)
    1  the tick could not run (bad manifest, broken config)

Usage:
    scripts/coding_tick.py [--manifest PATH | --project NAME]
                           [--max-tasks N] [--dry-run] [--verbose]
"""
import argparse
import asyncio
import contextlib
import io
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import (  # noqa: E402
    ensure_project_interpreter,
    enter_project_root,
    project_root as _project_root,
)

# Import-safe: the scheduler imports this module in-process to call
# `has_work`, so re-exec and chdir happen only under `__main__`.
project_root = _project_root()


def has_work(ctx) -> bool:
    """Scheduler gate: is there anything a tick could advance?

    True when any project's queue has a runnable task or a review to sync, or
    holds an expired lease the tick would reclaim. A manifest that cannot be
    read also counts, so the tick runs and reports it rather than going quiet.
    """
    from graphs.coding.nodes.scheduler import select_task
    from graphs.coding.utils import manifest as manifest_store
    from graphs.coding.utils.dag import SOFTWARE_ROOT, discover_manifests

    now = time.time()
    for manifest_path in discover_manifests(
        os.path.join(project_root, SOFTWARE_ROOT)
    ):
        try:
            queue = manifest_store.load_manifest(manifest_path).get("queue") or []
        except Exception:
            return True
        for task in queue:
            if task.get("lease_owner") and not manifest_store.lease_is_active(
                task, now
            ):
                return True
        try:
            task, _route = select_task(queue, handled=[], now=now)
        except Exception:
            return True
        if task is not None:
            return True
    return False


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run one coding graph tick.")
    parser.add_argument(
        "--manifest",
        default=os.environ.get("AOC_BUILD_REQUEST", ""),
        help="Path to build_request.json. Omit to tick every project."
    )
    parser.add_argument(
        "--project",
        default="",
        help="Project name, resolved to "
             "pkm/wiki/software/<project>/build_request.json."
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=1,
        help="How many tasks one invocation may advance, per project. "
             "Each is a separate tick."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what the tick would pick up, without running anything."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=bool(os.environ.get("AOC_TICK_VERBOSE")),
        help="Re-emit the runtime's own chatter on stderr."
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass error cache and report errors even if they persist."
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear the error cache before running."
    )
    return parser.parse_args(argv)


@contextlib.contextmanager
def quiet_stdout(verbose: bool = False):
    """Keeps stdout for the tick report alone.

    The runtime narrates itself on stdout — graph reloads, tool rosters, every
    tool call the worker makes. That is fine in a terminal and wrong here: the
    script-executor posts this script's stdout to `#software-dev` verbatim, so
    the narration became the message and the one line that mattered was buried
    in it. Captured chatter is discarded, or sent to stderr under `--verbose`
    (the runner only reads stderr when the script exits non-zero).
    """
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            yield
    finally:
        noise = buffer.getvalue()
        if verbose and noise.strip():
            sys.stderr.write(noise)


def select_manifests(args) -> list:
    """The manifests this invocation is responsible for.

    An explicit `--manifest` or `--project` names one. With neither — which is
    how cron runs it — every project is visited in turn, because there is no
    longer one shared queue that all projects wait in.
    """
    from graphs.coding.utils.dag import (
        discover_manifests,
        manifest_path_for_project,
    )

    if args.manifest:
        return [os.path.abspath(os.path.expanduser(args.manifest))]
    if args.project:
        return [manifest_path_for_project(args.project)]
    return discover_manifests()


def project_label(manifest_path: str) -> str:
    """The project folder name — says which queue a report came from."""
    return os.path.basename(os.path.dirname(manifest_path))


async def run_tick(manifest_path: str) -> str:
    from graphs.coding.adapters import format_output, prepare_input
    from graphs.coding.graph import create_graph

    graph = create_graph()
    inp = prepare_input(query="coding tick", build_request_path=manifest_path)
    state = await graph.ainvoke(inp)
    return format_output(state)


def describe_pending(manifest_path: str) -> str:
    """The dry-run view: what the next real tick would act on."""
    import time

    from graphs.coding.nodes.scheduler import select_task
    from graphs.coding.utils.manifest import load_manifest

    queue = load_manifest(manifest_path).get("queue") or []
    task, route = select_task(queue, handled=[], now=time.time())
    if task is None:
        return ""
    return f"Next tick would run `{task['task_id']}` → {route}."


def get_cache_file_path() -> str:
    """Returns the path to the error cache JSON file."""
    env_path = os.environ.get("AOC_TICK_ERROR_CACHE")
    if env_path:
        return os.path.abspath(os.path.expanduser(env_path))
    return os.path.join(project_root, "sessions", "coding_tick_errors.json")


def load_error_cache(cache_path: Optional[str] = None) -> Dict[str, Any]:
    """Loads the error cache dictionary from disk."""
    path = cache_path or get_cache_file_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def save_error_cache(
    cache: Dict[str, Any], cache_path: Optional[str] = None
) -> None:
    """Saves the error cache dictionary to disk atomically."""
    path = cache_path or get_cache_file_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = f"{path}.tmp.{os.getpid()}"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        os.replace(temp_path, path)
    except Exception:
        pass


def cache_key_for_manifest(manifest_path: str) -> str:
    """Returns the canonical cache key for a manifest path."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(manifest_path)))


def get_cached_error(
    key: str, cache_path: Optional[str] = None
) -> Optional[str]:
    """Retrieves the previous cached error message for a manifest, if any."""
    cache = load_error_cache(cache_path)
    entry = cache.get(key)
    if isinstance(entry, dict):
        return entry.get("error")
    if isinstance(entry, str):
        return entry
    return None


def set_cached_error(
    key: str, error_message: str, cache_path: Optional[str] = None
) -> None:
    """Records the error message for a manifest in the cache."""
    cache = load_error_cache(cache_path)
    cache[key] = {
        "error": error_message.strip(),
        "timestamp": time.time(),
    }
    save_error_cache(cache, cache_path)


def clear_cached_error(key: str, cache_path: Optional[str] = None) -> None:
    """Clears any cached error message for a manifest."""
    cache = load_error_cache(cache_path)
    if key in cache:
        del cache[key]
        save_error_cache(cache, cache_path)


def is_tick_error(report: str) -> bool:
    """Checks whether a tick report represents a fatal error."""
    stripped = report.strip()
    return stripped.startswith("🛑") or stripped.startswith("Error:")


def tick_project(manifest_path: str, args) -> str:
    """Advances one project by up to `--max-tasks` tasks."""
    if args.dry_run:
        return describe_pending(manifest_path)

    key = cache_key_for_manifest(manifest_path)
    use_cache = not getattr(args, "no_cache", False)

    reports = []
    for _ in range(max(1, args.max_tasks)):
        output = asyncio.run(run_tick(manifest_path))
        if not output.strip():
            # Nothing left to advance; further ticks would repeat this.
            if use_cache:
                clear_cached_error(key)
            break

        cleaned = output.strip()
        if is_tick_error(cleaned):
            if use_cache:
                cached = get_cached_error(key)
                if cached == cleaned:
                    # Same error persists from previous run; suppress it.
                    break
                set_cached_error(key, cleaned)
            reports.append(cleaned)
            # A fatal error halts further advances for this project.
            break
        else:
            if use_cache:
                clear_cached_error(key)
            reports.append(cleaned)

    return "\n".join(reports)


def main(argv=None) -> int:
    args = parse_args(argv)

    if getattr(args, "clear_cache", False):
        save_error_cache({})

    manifests = [p for p in select_manifests(args) if p and os.path.exists(p)]
    if not manifests:
        # Not an error worth alerting on every five minutes: no manifest simply
        # means nothing has been queued yet.
        return 0

    sections = []
    try:
        with quiet_stdout(args.verbose):
            for manifest_path in manifests:
                report = tick_project(manifest_path, args)
                if not report.strip():
                    continue
                # Several projects tick in one run, so a line has to say which
                # queue it came from.
                if len(manifests) > 1:
                    sections.append(
                        f"**{project_label(manifest_path)}**\n{report.strip()}"
                    )
                else:
                    sections.append(report.strip())
    except Exception as e:
        # A broken manifest or config must be loud: it will not fix itself, and
        # silence here would look exactly like an idle queue.
        print(f"🛑 Coding tick could not run: {e}", file=sys.stderr)
        return 1

    if sections:
        print("\n\n".join(sections))
    return 0


if __name__ == "__main__":
    ensure_project_interpreter()
    enter_project_root()
    from core.runtime.execution_context import surface_scope

    # Run as a script, this is the scheduled tick: the worker calls it makes
    # are booked as scheduled work.
    with surface_scope("scheduled"):
        sys.exit(main())
