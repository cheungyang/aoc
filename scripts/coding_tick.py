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
    scripts/coding_tick.py [--manifest PATH] [--max-tasks N] [--dry-run]
"""
import argparse
import asyncio
import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# The graph resolves the manifest and the worktree root relative to the working
# directory, so a cron invocation from anywhere must still act on the repo.
os.chdir(project_root)


def _reexec_in_venv_if_needed():
    """Re-runs this script under the repo's own interpreter when it has to.

    `#!/usr/bin/env python3` resolves against whatever PATH the cron happens to
    have, which on macOS is usually the system python — no langgraph, no
    langchain. Failing that way would post an import traceback every five
    minutes, so hop into `.venv` once instead.
    """
    if os.environ.get("AOC_TICK_REEXEC"):
        return  # Already re-executed once; do not loop.

    import importlib.util
    if importlib.util.find_spec("langgraph") is not None:
        return

    venv_python = os.path.join(project_root, ".venv", "bin", "python")
    if not os.path.exists(venv_python) or os.path.realpath(venv_python) == os.path.realpath(sys.executable):
        return

    os.environ["AOC_TICK_REEXEC"] = "1"
    os.execv(venv_python, [venv_python, os.path.abspath(__file__)] + sys.argv[1:])


_reexec_in_venv_if_needed()



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
    return parser.parse_args()


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
