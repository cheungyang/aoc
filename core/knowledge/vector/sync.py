import os
from typing import Dict, Any, List, Optional, Tuple

from core.config import Config
from core.knowledge.db import (
    init_knowledge_db,
    upsert_chunks,
    prune_deleted_files,
    get_existing_hashes,
    get_knowledge_db_path,
    build_fts_index,
)
from core.knowledge.indexer import (
    split_markdown_into_chunks,
    generate_embeddings,
    get_embedding_client,
)


def get_pkm_dir() -> str:
    """Returns the PKM directory path."""
    return Config().pkm_dir


def scan_knowledge_markdown_files(pkm_dir: str) -> List[Tuple[str, str, str]]:
    """
    Finds all indexable markdown files in the PKM vault.
    Scans:
      - ~/pkm/vault -> category: 'vault' (core personal notes)
      - ~/pkm/wiki  -> category: 'wiki' (agent-synthesized wiki)

    Explicitly excludes:
      - ~/pkm/ticktick (untidied tasks, handled by tasks.db SQLite)
      - ~/pkm/inbox    (unread notes)
      - ~/pkm/agents   (agent memory)
      - Non-markdown assets, templates, scripts, .git, .obsidian, backups.

    Returns a list of tuples: (full_file_path, relative_path, category).
    """
    target_files = []
    
    if not os.path.isdir(pkm_dir):
        return []

    # Scoped target subdirectories and their category tags
    target_scopes = [
        ("vault", "vault"),
        ("wiki", "wiki"),
    ]

    for sub_dir, category in target_scopes:
        scope_root = os.path.join(pkm_dir, sub_dir)
        if not os.path.isdir(scope_root):
            continue

        for root, dirs, files in os.walk(scope_root):
            # Exclude hidden, backup, asset directories
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in ("backup", "assets", "node_modules", ".trash", "templates")
            ]

            for file in files:
                if file.endswith(".md") and not file.startswith("."):
                    full_p = os.path.join(root, file)
                    rel_p = os.path.relpath(full_p, pkm_dir)
                    target_files.append((full_p, rel_p, category))

    return target_files


def sync_knowledge(
    pkm_dir: Optional[str] = None,
    db_path: Optional[str] = None,
    dry_run: bool = False,
    force_reindex: bool = False
) -> Dict[str, Any]:
    """
    Scans the PKM vault, splits notes into header-aware chunks,
    generates embeddings incrementally, and upserts them into LanceDB.
    """
    resolved_pkm = os.path.abspath(os.path.expanduser(pkm_dir or get_pkm_dir()))
    resolved_db = os.path.abspath(os.path.expanduser(db_path or get_knowledge_db_path()))

    if not os.path.isdir(resolved_pkm):
        raise FileNotFoundError(f"PKM directory not found: {resolved_pkm}")

    table = init_knowledge_db(db_path=resolved_db, force_recreate=force_reindex and not dry_run)
    existing_hashes = {} if force_reindex else get_existing_hashes(table)

    files = scan_knowledge_markdown_files(resolved_pkm)
    all_chunks: List[Dict[str, Any]] = []
    chunks_to_embed: List[Dict[str, Any]] = []
    valid_file_paths: List[str] = []

    vault_count = 0
    wiki_count = 0

    for full_path, rel_path, category in files:
        valid_file_paths.append(rel_path)
        if category == "vault":
            vault_count += 1
        elif category == "wiki":
            wiki_count += 1

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            print(f"Warning: Could not read {full_path}: {e}")
            continue

        file_chunks = split_markdown_into_chunks(rel_path, content, category=category)
        for chunk in file_chunks:
            all_chunks.append(chunk)
            cid = chunk["id"]
            chash = chunk["content_hash"]
            # Only embed if chunk is new or modified
            if cid not in existing_hashes or existing_hashes[cid] != chash:
                chunks_to_embed.append(chunk)

    if dry_run:
        return {
            "scanned_files": len(files),
            "vault_files": vault_count,
            "wiki_files": wiki_count,
            "total_chunks": len(all_chunks),
            "chunks_to_embed": len(chunks_to_embed),
            "unchanged": len(all_chunks) - len(chunks_to_embed),
            "inserted": len(chunks_to_embed),
            "updated": 0,
            "pruned": 0,
            "dry_run": True
        }

    # Generate embeddings in batches for chunks that need update
    if chunks_to_embed:
        client = get_embedding_client()
        batch_size = 64
        for i in range(0, len(chunks_to_embed), batch_size):
            batch = chunks_to_embed[i:i + batch_size]
            texts = [c["text"] for c in batch]
            vectors = generate_embeddings(texts, client=client)
            for c, vec in zip(batch, vectors):
                c["vector"] = vec

    # For unchanged chunks that are already in DB, we don't need to re-upsert them
    upsert_stats = upsert_chunks(table, chunks_to_embed)

    # Prune deleted files
    pruned_count = prune_deleted_files(table, valid_file_paths)

    return {
        "scanned_files": len(files),
        "vault_files": vault_count,
        "wiki_files": wiki_count,
        "total_chunks": len(all_chunks),
        "chunks_to_embed": len(chunks_to_embed),
        "inserted": upsert_stats.get("inserted", 0),
        "updated": upsert_stats.get("updated", 0),
        "unchanged": len(all_chunks) - len(chunks_to_embed),
        "pruned": pruned_count,
        "dry_run": False
    }
