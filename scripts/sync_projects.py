#!/usr/bin/env python3
"""
CLI script to sync Obsidian projects from ~/pkm/vault/projects to SQLite database (~/pkm/projects.db).
Usage:
    python3 scripts/sync_projects.py [--pkm-dir ~/pkm] [--db-path ~/pkm/projects.db] [--dry-run]
"""

import sys
import os
import argparse

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.knowledge.projects.sync import sync_projects, get_pkm_dir, get_projects_dir
from core.knowledge.projects.db import get_db_path


def parse_args():
    parser = argparse.ArgumentParser(description="Synchronize Obsidian PKM projects into SQLite database.")
    parser.add_argument("--pkm-dir", type=str, default=None, help="Path to PKM directory (defaults to ~/pkm)")
    parser.add_argument("--db-path", type=str, default=None, help="Path to projects.db SQLite file (defaults to ~/pkm/projects.db)")
    parser.add_argument("--projects-dir", type=str, default=None, help="Path to projects directory (defaults to ~/pkm/vault/projects)")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without modifying database")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed output")
    return parser.parse_args()


def main():
    args = parse_args()
    pkm_dir = os.path.abspath(os.path.expanduser(args.pkm_dir)) if args.pkm_dir else get_pkm_dir()
    db_path = os.path.abspath(os.path.expanduser(args.db_path)) if args.db_path else get_db_path()
    projects_dir = os.path.abspath(os.path.expanduser(args.projects_dir)) if args.projects_dir else get_projects_dir(pkm_dir)

    print(f"=== Starting PKM Projects Sync ===")
    print(f"PKM Directory:      {pkm_dir}")
    print(f"Projects Directory: {projects_dir}")
    print(f"Database Path:      {db_path}")
    if args.dry_run:
        print("Mode: DRY-RUN (no changes will be written)")

    try:
        results = sync_projects(
            pkm_dir=pkm_dir,
            db_path=db_path,
            projects_dir=projects_dir,
            dry_run=args.dry_run
        )
        print(f"\n--- Sync Summary ---")
        print(f"Scanned files:            {results['scanned_files']}")
        print(f"Modified markdown files:  {results.get('modified_files', 0)}")
        print(f"Total projects found:     {results['total_projects_found']}")
        print(f"Inserted into SQLite:     {results['inserted']}")
        print(f"Updated in SQLite:        {results['updated']}")
        print(f"Unchanged in SQLite:      {results['unchanged']}")
        print(f"Pruned deleted projects:  {results['pruned']}")
        print("=== Sync Complete ===")
    except Exception as e:
        print(f"Error during sync: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
