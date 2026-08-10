#!/usr/bin/env python3
"""
CLI script to sync Obsidian & TickTick tasks from ~/pkm to SQLite database.
Usage:
    python3 scripts/sync_tasks.py [--pkm-dir ~/pkm] [--db-path ~/pkm/tasks.db] [--dry-run]
"""

import sys
import os
import argparse
import json

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.knowledge.tasks.sync import sync_tasks, get_pkm_dir
from core.knowledge.tasks.db import get_db_path


def parse_args():
    parser = argparse.ArgumentParser(description="Synchronize Obsidian PKM tasks into SQLite database.")
    parser.add_argument("--pkm-dir", type=str, default=None, help="Path to PKM directory (defaults to ~/pkm)")
    parser.add_argument("--db-path", type=str, default=None, help="Path to tasks.db SQLite file (defaults to ~/pkm/tasks.db)")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without modifying files or database")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed output")
    return parser.parse_args()


def main():
    args = parse_args()
    pkm_dir = os.path.abspath(os.path.expanduser(args.pkm_dir)) if args.pkm_dir else get_pkm_dir()
    db_path = os.path.abspath(os.path.expanduser(args.db_path)) if args.db_path else get_db_path()

    print(f"=== Starting PKM Tasks Sync ===")
    print(f"PKM Directory: {pkm_dir}")
    print(f"Database Path: {db_path}")
    if args.dry_run:
        print("Mode: DRY-RUN (no changes will be written)")

    try:
        results = sync_tasks(pkm_dir=pkm_dir, db_path=db_path, dry_run=args.dry_run)
        print(f"\n--- Sync Summary ---")
        print(f"Scanned files:            {results['scanned_files']}")
        print(f"Modified markdown files:  {results['modified_markdown_files']}")
        print(f"Total tasks found:        {results['total_tasks_found']}")
        print(f"Inserted into SQLite:     {results['inserted']}")
        print(f"Updated in SQLite:        {results['updated']}")
        print(f"Unchanged in SQLite:      {results['unchanged']}")
        print(f"Pruned deleted tasks:     {results['pruned']}")
        print("=== Sync Complete ===")
    except Exception as e:
        print(f"Error during sync: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
