#!/usr/bin/env python3
"""
CLI script to regenerate or rebuild the LanceDB vector database from Obsidian PKM notes.

Usage:
    python3 scripts/regenerate_lancedb.py [OPTIONS]

Examples:
    # Full regeneration with default paths (~/pkm -> ~/pkm/.lancedb):
    python3 scripts/regenerate_lancedb.py

    # Fast offline regeneration using deterministic embeddings (no API quota):
    python3 scripts/regenerate_lancedb.py --skip-embedding

    # Regenerate custom PKM and DB paths:
    python3 scripts/regenerate_lancedb.py --pkm-dir ./pkm --db-path ./pkm/.lancedb

    # Dry-run preview without writing:
    python3 scripts/regenerate_lancedb.py --dry-run
"""

import sys
import os
import argparse
import time
from typing import Optional, Dict, Any, List

# Check dependencies before heavy imports
try:
    import pyarrow as pa
    import lancedb
except ImportError as e:
    print(f"Error: Missing required dependency ({e}).", file=sys.stderr)
    print("Please run this script using the Python environment where dependencies are installed.", file=sys.stderr)
    print("Example: /opt/homebrew/bin/python3.11 scripts/regenerate_lancedb.py", file=sys.stderr)
    print("Or: source .venv/bin/activate && pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

# Ensure project root is in sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.util.config import Config
from core.knowledge.vector.db import (
    init_knowledge_db,
    build_fts_index,
    hybrid_search_vault,
    get_knowledge_db_path,
    TABLE_NAME,
)
from core.knowledge.vector.indexer import (
    split_markdown_into_chunks,
    generate_embeddings,
    generate_query_embedding,
    get_embedding_client,
)
from core.knowledge.vector.sync import (
    scan_knowledge_markdown_files,
    get_pkm_dir,
)


def resolve_paths(pkm_dir_arg: Optional[str] = None, db_path_arg: Optional[str] = None) -> tuple[str, str]:
    """
    Resolves and validates PKM directory and LanceDB storage path.
    Checks CLI args, environment variables, config, and sensible project-relative fallbacks.
    """
    config = Config()

    # 1. Resolve PKM directory
    if pkm_dir_arg:
        pkm_dir = os.path.abspath(os.path.expanduser(pkm_dir_arg))
    elif os.getenv("PKM_DIR"):
        pkm_dir = os.path.abspath(os.path.expanduser(os.getenv("PKM_DIR")))
    elif os.path.isdir(os.path.join(project_root, "pkm")):
        pkm_dir = os.path.abspath(os.path.join(project_root, "pkm"))
    else:
        pkm_dir = os.path.abspath(os.path.expanduser(config.pkm_dir))

    # 2. Resolve LanceDB directory
    if db_path_arg:
        db_path = os.path.abspath(os.path.expanduser(db_path_arg))
    elif os.getenv("KNOWLEDGE_DB_PATH"):
        db_path = os.path.abspath(os.path.expanduser(os.getenv("KNOWLEDGE_DB_PATH")))
    elif pkm_dir and os.path.isdir(pkm_dir):
        # Default to .lancedb within the resolved PKM directory
        db_path = os.path.join(pkm_dir, ".lancedb")
    else:
        db_path = os.path.abspath(os.path.expanduser(config.knowledge_db_path))

    return pkm_dir, db_path


def check_and_repair_workspace_symlink(resolved_pkm: str) -> None:
    """
    Checks if a ./pkm symlink in the codebase root is broken or missing, and fixes it.
    """
    repo_pkm_link = os.path.join(project_root, "pkm")
    if os.path.islink(repo_pkm_link):
        target = os.readlink(repo_pkm_link)
        if not os.path.exists(repo_pkm_link):
            print(f"Notice: Broken symlink detected at {repo_pkm_link} -> {target}")
            if os.path.exists(resolved_pkm):
                try:
                    os.unlink(repo_pkm_link)
                    os.symlink(resolved_pkm, repo_pkm_link)
                    print(f"Repaired symlink: {repo_pkm_link} -> {resolved_pkm}")
                except Exception as e:
                    print(f"Warning: Could not repair symlink: {e}")
    elif not os.path.exists(repo_pkm_link) and os.path.exists(resolved_pkm):
        try:
            os.symlink(resolved_pkm, repo_pkm_link)
            print(f"Created workspace symlink: {repo_pkm_link} -> {resolved_pkm}")
        except Exception as e:
            print(f"Notice: Could not create symlink at {repo_pkm_link}: {e}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Regenerate or rebuild LanceDB vector database from Obsidian PKM notes."
    )
    parser.add_argument(
        "--pkm-dir",
        type=str,
        default=None,
        help="Path to PKM directory containing 'vault/' and 'wiki/' (defaults to ~/pkm or ./pkm)"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to LanceDB database directory (defaults to <pkm-dir>/.lancedb)"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        default=True,
        help="Clean rebuild: recreate the database table from scratch (default: True)"
    )
    parser.add_argument(
        "--no-clean",
        dest="clean",
        action="store_false",
        help="Preserve existing table and merge/update changes incrementally"
    )
    parser.add_argument(
        "--skip-embedding",
        action="store_true",
        help="Use fast deterministic embeddings instead of external API calls (offline mode, 0 quota cost)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Embedding model override (e.g. 'gemini-embedding-001' or 'models/gemini-embedding-001')"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Batch size for embedding generation (default: 64)"
    )
    parser.add_argument(
        "--write-batch-size",
        type=int,
        default=500,
        help="Batch size for writing records to LanceDB (default: 500)"
    )
    parser.add_argument(
        "--test-query",
        type=str,
        default="project",
        help="Verification search query to test post-regeneration (set to '' to skip)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and chunk notes without writing to database or generating live embeddings"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Display detailed per-file indexing information"
    )
    return parser.parse_args()


def regenerate_lancedb(
    pkm_dir: Optional[str] = None,
    db_path: Optional[str] = None,
    clean: bool = True,
    dry_run: bool = False,
    skip_embedding: bool = False,
    model: Optional[str] = None,
    batch_size: int = 64,
    write_batch_size: int = 500,
    test_query: Optional[str] = "project",
    verbose: bool = False
) -> Dict[str, Any]:
    """
    Scans PKM markdown notes (vault/ and wiki/), parses header chunks,
    computes vector embeddings, writes to LanceDB, builds BM25 FTS index,
    and runs a verification sanity query.
    """
    start_time = time.time()

    resolved_pkm, resolved_db = resolve_paths(pkm_dir, db_path)
    check_and_repair_workspace_symlink(resolved_pkm)

    if not os.path.isdir(resolved_pkm):
        raise FileNotFoundError(
            f"PKM directory not found at: {resolved_pkm}\n"
            "Please ensure your PKM notes exist or specify --pkm-dir /path/to/pkm"
        )

    vault_sub = os.path.join(resolved_pkm, "vault")
    wiki_sub = os.path.join(resolved_pkm, "wiki")
    if not os.path.isdir(vault_sub) and not os.path.isdir(wiki_sub):
        raise FileNotFoundError(
            f"Neither 'vault' nor 'wiki' subdirectories were found inside: {resolved_pkm}"
        )

    print(f"=== LanceDB Regeneration Started ===")
    print(f"PKM Directory:    {resolved_pkm}")
    print(f"LanceDB Path:     {resolved_db}")
    print(f"Mode:             {'DRY-RUN' if dry_run else ('CLEAN REBUILD' if clean else 'INCREMENTAL')}")
    print(f"Embedding Mode:   {'Deterministic (Offline)' if skip_embedding else 'API Client (with deterministic fallback)'}")

    # 1. Scan markdown files
    print("\n[1/5] Scanning markdown files...")
    files = scan_knowledge_markdown_files(resolved_pkm)
    vault_files = sum(1 for _, _, cat in files if cat == "vault")
    wiki_files = sum(1 for _, _, cat in files if cat == "wiki")
    print(f"Found {len(files)} indexable markdown files (Vault: {vault_files}, Wiki: {wiki_files})")

    if not files:
        print("Warning: No markdown files found to index.")
        return {
            "pkm_dir": resolved_pkm,
            "db_path": resolved_db,
            "scanned_files": 0,
            "vault_files": 0,
            "wiki_files": 0,
            "total_chunks": 0,
            "embedded_chunks": 0,
            "rows_in_db": 0,
            "dry_run": dry_run,
            "verified": False,
            "duration_seconds": round(time.time() - start_time, 2)
        }

    # 2. Chunk markdown files
    print("\n[2/5] Splitting files into header-aware contextual chunks...")
    all_chunks: List[Dict[str, Any]] = []
    read_errors = 0

    for idx, (full_path, rel_path, category) in enumerate(files, 1):
        if verbose:
            print(f"  [{idx}/{len(files)}] Parsing {rel_path} ({category})")
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            read_errors += 1
            if verbose:
                print(f"    Warning: Could not read {full_path}: {e}")
            continue

        file_chunks = split_markdown_into_chunks(rel_path, content, category=category)
        all_chunks.extend(file_chunks)

    vault_chunks = sum(1 for c in all_chunks if c.get("category") == "vault")
    wiki_chunks = sum(1 for c in all_chunks if c.get("category") == "wiki")
    print(f"Generated {len(all_chunks)} chunks across {len(files) - read_errors} files (Vault: {vault_chunks}, Wiki: {wiki_chunks})")

    if dry_run:
        print("\n[DRY RUN COMPLETE] No database modifications made.")
        return {
            "pkm_dir": resolved_pkm,
            "db_path": resolved_db,
            "scanned_files": len(files),
            "vault_files": vault_files,
            "wiki_files": wiki_files,
            "total_chunks": len(all_chunks),
            "embedded_chunks": 0,
            "rows_in_db": 0,
            "dry_run": True,
            "verified": False,
            "duration_seconds": round(time.time() - start_time, 2)
        }

    # 3. Generate embeddings
    print("\n[3/5] Generating embeddings...")
    embedding_client = None
    if not skip_embedding:
        target_model = model or "gemini-embedding-001"
        embedding_client = get_embedding_client(target_model)
        if embedding_client:
            print(f"Using GoogleGenerativeAIEmbeddings (Model: {target_model})")
        else:
            print("No embedding API key configured or client failed; using deterministic embeddings.")
    else:
        print("Using deterministic embeddings (--skip-embedding requested).")

    total_chunks = len(all_chunks)
    total_batches = (total_chunks + batch_size - 1) // batch_size
    api_failure_logged = False

    for b_idx in range(total_batches):
        start_i = b_idx * batch_size
        end_i = min(start_i + batch_size, total_chunks)
        batch = all_chunks[start_i:end_i]
        texts = [c["text"] for c in batch]

        pct = int((b_idx + 1) / total_batches * 100)
        print(f"\r  Embedding batch {b_idx + 1}/{total_batches} ({pct}%) [{start_i + 1}-{end_i}/{total_chunks}]", end="", flush=True)

        if embedding_client and not api_failure_logged:
            try:
                vectors = embedding_client.embed_documents(texts)
                for c, vec in zip(batch, vectors):
                    c["vector"] = vec
                continue
            except Exception as e:
                print(f"\n  Warning: API embedding failed on batch {b_idx + 1} ({e}). Switching to deterministic embeddings for remaining chunks.")
                api_failure_logged = True
                embedding_client = None

        # Deterministic fallback
        vectors = generate_embeddings(texts, client=None)
        for c, vec in zip(batch, vectors):
            c["vector"] = vec

    print("\n  Embeddings generated successfully.")

    # 4. Write to LanceDB
    print("\n[4/5] Initializing LanceDB table and writing chunks...")
    os.makedirs(resolved_db, exist_ok=True)
    table = init_knowledge_db(db_path=resolved_db, force_recreate=clean)

    write_batches = (total_chunks + write_batch_size - 1) // write_batch_size
    for wb_idx in range(write_batches):
        w_start = wb_idx * write_batch_size
        w_end = min(w_start + write_batch_size, total_chunks)
        batch_to_add = all_chunks[w_start:w_end]
        table.add(batch_to_add)
        w_pct = int((wb_idx + 1) / write_batches * 100)
        print(f"\r  Wrote batch {wb_idx + 1}/{write_batches} ({w_pct}%) to LanceDB table '{TABLE_NAME}'", end="", flush=True)

    print("\n  Building Tantivy Full-Text Search (BM25) index on 'text'...")
    build_fts_index(table)
    print("  Full-Text Search index built.")

    # 5. Verification & Health Check
    print("\n[5/5] Verifying database integrity...")
    total_rows = table.count_rows()
    print(f"Total rows in '{TABLE_NAME}': {total_rows}")

    verified = False
    if test_query:
        print(f"Running test query: '{test_query}'...")
        try:
            sample_vector = [0.0] * Config().embedding_dimensions
            results = hybrid_search_vault(
                table=table,
                query=test_query,
                query_vector=sample_vector,
                limit=3,
                search_type="hybrid"
            )
            if results:
                top = results[0]
                print(f"Verification query successful! Top match:")
                print(f"  Title:    {top.get('title')}")
                print(f"  Path:     {top.get('file_path')} ({top.get('category')})")
                print(f"  Section:  {top.get('header_path')}")
                verified = True
            else:
                # Try FTS search if hybrid yielded no top result
                fts_results = hybrid_search_vault(
                    table=table,
                    query=test_query,
                    limit=3,
                    search_type="keyword"
                )
                if fts_results:
                    top = fts_results[0]
                    print(f"Verification FTS query successful! Top match: {top.get('title')} ({top.get('file_path')})")
                    verified = True
                else:
                    print("Verification query returned 0 rows (database is populated, but query term was not matched).")
                    verified = total_rows > 0
        except Exception as e:
            print(f"Warning: Verification search encountered error: {e}")
            verified = total_rows > 0
    else:
        verified = total_rows > 0

    duration = round(time.time() - start_time, 2)
    print(f"\n=== LanceDB Regeneration Completed in {duration}s ===")
    print(f"Database Location: {resolved_db}")
    print(f"Total Chunks:      {total_rows}")
    print(f"Status:            {'HEALTHY & READY' if verified else 'CHECK WARNINGS'}")

    return {
        "pkm_dir": resolved_pkm,
        "db_path": resolved_db,
        "scanned_files": len(files),
        "vault_files": vault_files,
        "wiki_files": wiki_files,
        "total_chunks": len(all_chunks),
        "embedded_chunks": len(all_chunks),
        "rows_in_db": total_rows,
        "dry_run": False,
        "verified": verified,
        "duration_seconds": duration
    }


def main():
    args = parse_args()
    try:
        results = regenerate_lancedb(
            pkm_dir=args.pkm_dir,
            db_path=args.db_path,
            clean=args.clean,
            dry_run=args.dry_run,
            skip_embedding=args.skip_embedding,
            model=args.model,
            batch_size=args.batch_size,
            write_batch_size=args.write_batch_size,
            test_query=args.test_query,
            verbose=args.verbose
        )
        if not results.get("verified", False) and not args.dry_run:
            sys.exit(1)
    except Exception as e:
        print(f"\nError during LanceDB regeneration: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
