#!/usr/bin/env python3
"""Operator control plane for the coding graph.

    scripts/coding_admin.py status [TASK_ID]
    scripts/coding_admin.py retry TASK_ID [--from-stage STAGE]
    scripts/coding_admin.py reset TASK_ID [--yes] [--dry-run]
    scripts/coding_admin.py skip TASK_ID
    scripts/coding_admin.py unblock TASK_ID
    scripts/coding_admin.py abort TASK_ID [--reason TEXT]

None of these run the pipeline. They write the manifest, and the next tick acts
on what they wrote — so there is exactly one execution path, and inspecting the
manifest tells you the whole truth about what will happen next.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import ensure_project_interpreter, enter_project_root  # noqa: E402

ensure_project_interpreter()
enter_project_root()

DEFAULT_MANIFEST = "pkm/wiki/software/build_request.json"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Inspect and steer the coding graph queue.")
    parser.add_argument(
        "--manifest",
        default=os.environ.get("AOC_BUILD_REQUEST", DEFAULT_MANIFEST),
        help=f"Path to build_request.json (default: {DEFAULT_MANIFEST})."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show the queue, or one task in detail.")
    p_status.add_argument("task_id", nargs="?")

    p_retry = sub.add_parser("retry", help="Make a halted or failed task runnable again.")
    p_retry.add_argument("task_id")
    p_retry.add_argument(
        "--from-stage",
        help="Rewind to this stage first (clears the digests so the work is really redone)."
    )

    p_reset = sub.add_parser(
        "reset",
        help="Destroy the task's work: close the PR, delete the branch, remove the worktree."
    )
    p_reset.add_argument("task_id")
    p_reset.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    p_reset.add_argument("--dry-run", action="store_true", help="Say what would happen, do nothing.")

    p_skip = sub.add_parser("skip", help="Block a task without failing it.")
    p_skip.add_argument("task_id")

    p_unblock = sub.add_parser("unblock", help="Return a skipped task to the queue.")
    p_unblock.add_argument("task_id")

    p_abort = sub.add_parser(
        "abort",
        help="Fail a task now, leaving the branch, worktree and PR for inspection."
    )
    p_abort.add_argument("task_id")
    p_abort.add_argument("--reason", default="")

    return parser.parse_args(argv)


def _confirm(prompt: str) -> bool:
    """Reset is destructive, so it asks — unless there is nobody to ask."""
    if not sys.stdin.isatty():
        print("Refusing to reset without --yes when there is no terminal to confirm at.", file=sys.stderr)
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def main(argv=None) -> int:
    args = parse_args(argv)
    manifest_path = os.path.abspath(os.path.expanduser(args.manifest))

    from graphs.coding.utils import control

    if not os.path.exists(manifest_path):
        print(f"No manifest at {manifest_path}.", file=sys.stderr)
        return 1

    try:
        if args.command == "status":
            print(control.status_report(manifest_path, args.task_id))

        elif args.command == "retry":
            print(control.retry(manifest_path, args.task_id, from_stage=args.from_stage))

        elif args.command == "reset":
            if not args.dry_run and not args.yes:
                preview = asyncio.run(control.reset(manifest_path, args.task_id, dry_run=True))
                if not _confirm(f"{preview}\nProceed?"):
                    print("Cancelled.")
                    return 0
            print(asyncio.run(control.reset(manifest_path, args.task_id, dry_run=args.dry_run)))

        elif args.command == "skip":
            print(control.skip(manifest_path, args.task_id))

        elif args.command == "unblock":
            print(control.unblock(manifest_path, args.task_id))

        elif args.command == "abort":
            print(control.abort(manifest_path, args.task_id, reason=args.reason))

    except control.ControlError as e:
        # An operator mistake: say what is wrong, do not print a traceback.
        print(f"⚠️  {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"🛑 {args.command} failed: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
