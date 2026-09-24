#!/usr/bin/env python3
"""
CLI script to sync Obsidian PKM vault notes to LanceDB knowledge base.
Usage:
    python3 scripts/sync_knowledge.py [--pkm-dir ~/pkm] [--db-path ~/pkm/.lancedb] [--dry-run] [--force-reindex]
"""

import sys
import os
import argparse

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.knowledge.vector.sync import sync_knowledge, get_pkm_dir
from core.knowledge.vector.db import get_knowledge_db_path


def has_work(ctx):
    """Scheduler gate: the index covers markdown under ~/pkm/vault and ~/pkm/wiki.

    Only `.md` files count, so the scanner's `pending_lint.json` (written into
    wiki/ every night) does not force a re-index on its own.
    """
    from core.scheduler.preconditions import first_changed_path

    if ctx.last_success_at is None:
        return True, "no previous successful sync"
    if not os.path.exists(get_knowledge_db_path()):
        return True, "the knowledge store does not exist yet"
    pkm_dir = get_pkm_dir()
    changed = first_changed_path(
        [os.path.join(pkm_dir, "vault"), os.path.join(pkm_dir, "wiki")],
        ctx.last_success_at,
        suffixes=(".md",),
    )
    if changed:
        return True, f"{os.path.relpath(changed, pkm_dir)} changed"
    return False, "no vault or wiki notes changed since the last sync"


def parse_args():
    parser = argparse.ArgumentParser(description="Synchronize Obsidian PKM notes into LanceDB vector database.")
    parser.add_argument("--pkm-dir", type=str, default=None, help="Path to PKM directory (defaults to ~/pkm)")
    parser.add_argument("--db-path", type=str, default=None, help="Path to LanceDB directory (defaults to ~/pkm/.lancedb)")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without modifying database")
    parser.add_argument("--force-reindex", action="store_true", help="Force re-indexing and re-embedding of all chunks")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed output")
    return parser.parse_args()


def main():
    args = parse_args()
    pkm_dir = os.path.abspath(os.path.expanduser(args.pkm_dir)) if args.pkm_dir else get_pkm_dir()
    db_path = os.path.abspath(os.path.expanduser(args.db_path)) if args.db_path else get_knowledge_db_path()

    print(f"=== Starting PKM Knowledge LanceDB Sync ===")
    print(f"PKM Directory: {pkm_dir}")
    print(f"Database Path: {db_path}")
    if args.dry_run:
        print("Mode: DRY-RUN (no changes will be written)")
    if args.force_reindex:
        print("Mode: FORCE RE-INDEX (all chunks will be re-embedded)")

    try:
        results = sync_knowledge(
            pkm_dir=pkm_dir,
            db_path=db_path,
            dry_run=args.dry_run,
            force_reindex=args.force_reindex
        )
        print(f"\n--- Sync Summary ---")
        print(f"Scanned files:            {results['scanned_files']} (Vault: {results.get('vault_files', 0)}, Wiki: {results.get('wiki_files', 0)})")
        print(f"Total chunks in vault:    {results['total_chunks']}")
        print(f"Chunks embedded:          {results['chunks_to_embed']}")
        print(f"Inserted into LanceDB:    {results['inserted']}")
        print(f"Updated in LanceDB:       {results['updated']}")
        print(f"Unchanged in LanceDB:     {results['unchanged']}")
        print(f"Pruned deleted files:     {results['pruned']}")
        print("=== Sync Complete ===")
    except Exception as e:
        print(f"Error during knowledge sync: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
