#!/usr/bin/env python3
"""Runs one reconciliation tick of the coding graph.

Invoked by the `script-executor` cron every five minutes. It prints what the
tick did and nothing at all when the tick did nothing — the runner treats empty
stdout as "post nothing", which is what keeps a channel usable when the queue is
idle 287 times out of 288 a day.

Exit codes:
    0  the tick ran (whether or not it had work to do)
    1  the tick could not run (bad manifest, broken config)

Usage:
    scripts/coding_tick.py [--manifest PATH] [--max-tasks N] [--dry-run] [--verbose]
"""
import argparse
import asyncio
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import ensure_project_interpreter, enter_project_root  # noqa: E402

ensure_project_interpreter()
project_root = enter_project_root()


def parse_args():
    parser = argparse.ArgumentParser(description="Run one coding graph tick.")
    parser.add_argument(
        "--manifest",
        default=os.environ.get("AOC_BUILD_REQUEST", "pkm/wiki/software/build_request.json"),
        help="Path to build_request.json (default: pkm/wiki/software/build_request.json)."
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=1,
        help="How many tasks one invocation may advance. Each is a separate tick."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what the tick would pick up, without running or writing anything."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=bool(os.environ.get("AOC_TICK_VERBOSE")),
        help="Re-emit the runtime's own chatter on stderr instead of discarding it."
    )
    return parser.parse_args()


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


async def run_tick(manifest_path: str) -> str:
    from graphs.coding.adapters import format_output, prepare_input
    from graphs.coding.graph import create_graph

    graph = create_graph()
    state = await graph.ainvoke(prepare_input(query="coding tick", build_request_path=manifest_path))
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


def main() -> int:
    args = parse_args()
    manifest_path = os.path.abspath(os.path.expanduser(args.manifest))

    if not os.path.exists(manifest_path):
        # Not an error worth alerting on every five minutes: no manifest simply
        # means nothing has been queued yet.
        return 0

    try:
        with quiet_stdout(args.verbose):
            if args.dry_run:
                report = describe_pending(manifest_path)
            else:
                reports = []
                for _ in range(max(1, args.max_tasks)):
                    output = asyncio.run(run_tick(manifest_path))
                    if not output.strip():
                        # Nothing left to advance; further ticks would repeat this.
                        break
                    reports.append(output.strip())
                report = "\n".join(reports)
    except Exception as e:
        # A broken manifest or config must be loud: it will not fix itself, and
        # silence here would look exactly like an idle queue.
        print(f"🛑 Coding tick could not run: {e}", file=sys.stderr)
        return 1

    if report.strip():
        print(report.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
