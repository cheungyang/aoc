#!/usr/bin/env python3
"""
CLI script to synchronize PKM Obsidian Vault and Main Codebase repositories.

Tasks:
1. PKM Obsidian Vault:
   - git add new files
   - git commit all changes
   - git pull (resolving conflicts with remote automatically via -X theirs / remote checkout)
   - git push
   - If conflicts cannot be resolved automatically, outputs error listing files needing manual resolution.
2. Main Codebase:
   - git pull for new updates only and resolve content with remote.

Usage:
    python3 scripts/git-sync.py [--pkm-dir ~/pkm] [--codebase-dir .] [--dry-run] [--verbose]
"""

import sys
import os
import argparse

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.sync.git_sync import sync_all, GitSyncConflictError, GitSyncError
from core.config import Config


def parse_args():
    parser = argparse.ArgumentParser(
        description="Synchronize PKM Obsidian Vault and Main Codebase Git repositories."
    )
    parser.add_argument(
        "--pkm-dir",
        type=str,
        default=None,
        help="Path to PKM Obsidian vault directory (defaults to PKM_DIR or ~/pkm)"
    )
    parser.add_argument(
        "--codebase-dir",
        type=str,
        default=None,
        help="Path to main codebase directory (defaults to project root)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate operations without making changes to git repos"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print verbose output during sync"
    )
    parser.add_argument(
        "--skip-pkm",
        action="store_true",
        help="Skip syncing the PKM Obsidian vault"
    )
    parser.add_argument(
        "--skip-codebase",
        action="store_true",
        help="Skip syncing the main codebase"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    pkm_dir = os.path.abspath(os.path.expanduser(args.pkm_dir)) if args.pkm_dir else Config().pkm_dir
    codebase_dir = os.path.abspath(os.path.expanduser(args.codebase_dir)) if args.codebase_dir else project_root

    print("=== Starting Git Synchronization ===")
    if not args.skip_pkm:
        print(f"PKM Vault: {pkm_dir}")
    if not args.skip_codebase:
        print(f"Codebase:  {codebase_dir}")
    if args.dry_run:
        print("Mode: DRY-RUN (no changes will be applied)")
    print("------------------------------------")

    try:
        sync_output = sync_all(
            pkm_dir=pkm_dir,
            codebase_dir=codebase_dir,
            dry_run=args.dry_run,
            verbose=args.verbose,
            skip_pkm=args.skip_pkm,
            skip_codebase=args.skip_codebase,
        )

        print(sync_output["summary"])

        if not sync_output["success"]:
            # Write error summary to stderr for script executor / agent to capture
            error_details = "\n".join(sync_output["errors"])
            if sync_output["conflict_files"]:
                conflict_list = "\n".join(f"  - {f}" for f in sync_output["conflict_files"])
                error_msg = (
                    f"\n[ERROR] Git Sync Conflict: Manual resolution required.\n"
                    f"The following files need to be resolved manually:\n{conflict_list}\n\n"
                    f"Details:\n{error_details}"
                )
            else:
                error_msg = f"\n[ERROR] Git Sync Failed:\n{error_details}"
            
            print(error_msg, file=sys.stderr)
            sys.exit(1)

        sys.exit(0)

    except GitSyncConflictError as e:
        print(f"\n[ERROR] Git Sync Conflict:\n{str(e)}", file=sys.stderr)
        sys.exit(1)
    except GitSyncError as e:
        print(f"\n[ERROR] Git Sync Failed:\n{str(e)}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error during git sync: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
