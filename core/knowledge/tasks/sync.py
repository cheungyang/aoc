import os
import re
import tempfile
from typing import Dict, Any, List, Optional, Tuple

from core.knowledge.tasks.parser import is_task_line, parse_task_line, extract_id
from core.knowledge.tasks.db import get_connection, init_db, upsert_tasks, prune_deleted_tasks, get_db_path
from core.util.config import Config


DEFAULT_PKM_DIR = os.path.expanduser("~/pkm")


def get_pkm_dir() -> str:
    """Returns the PKM directory path."""
    return Config().pkm_dir


def scan_markdown_files(pkm_dir: str) -> List[Tuple[str, str, str]]:
    """
    Finds all target markdown files in ~/pkm/ticktick and ~/pkm/vault.
    Returns a list of tuples: (full_file_path, relative_path, source_type).
    """
    target_files = []
    
    # 1. Scan ticktick directory
    ticktick_dir = os.path.join(pkm_dir, "ticktick")
    if os.path.isdir(ticktick_dir):
        for root, dirs, files in os.walk(ticktick_dir):
            # Skip backup directory
            if "backup" in dirs:
                dirs.remove("backup")
            if ".git" in dirs:
                dirs.remove(".git")
            for file in files:
                if file.endswith(".md") and not file.startswith("."):
                    full_p = os.path.join(root, file)
                    rel_p = os.path.relpath(full_p, pkm_dir)
                    target_files.append((full_p, rel_p, "ticktick"))

    # 2. Scan vault directory
    vault_dir = os.path.join(pkm_dir, "vault")
    if os.path.isdir(vault_dir):
        for root, dirs, files in os.walk(vault_dir):
            # Skip non-markdown assets / system dirs
            if ".git" in dirs:
                dirs.remove(".git")
            if ".obsidian" in dirs:
                dirs.remove(".obsidian")
            if "assets" in dirs:
                dirs.remove("assets")
            for file in files:
                if file.endswith(".md") and not file.startswith("."):
                    full_p = os.path.join(root, file)
                    rel_p = os.path.relpath(full_p, pkm_dir)
                    target_files.append((full_p, rel_p, "vault"))

    return target_files


def process_file(
    file_path: str,
    rel_path: str,
    source_type: str,
    dry_run: bool = False
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Reads a markdown file, parses tasks, and assigns IDs to vault tasks without IDs.
    Returns (list_of_parsed_tasks, was_file_modified).
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return [], False

    parsed_tasks = []
    modified_lines = []
    file_was_modified = False

    # Auto-assign IDs only for vault files (or any task missing an ID)
    should_auto_assign = (source_type == "vault")

    for line_idx, line in enumerate(lines, start=1):
        if is_task_line(line):
            task_data, updated_line = parse_task_line(
                line=line,
                line_number=line_idx,
                source_path=rel_path,
                source_type=source_type,
                auto_assign_id=should_auto_assign
            )
            if task_data:
                parsed_tasks.append(task_data)

            if updated_line is not None:
                modified_lines.append(updated_line)
                file_was_modified = True
            else:
                modified_lines.append(line)
        else:
            modified_lines.append(line)

    # If vault tasks had new IDs injected, write back atomically
    if file_was_modified and not dry_run:
        _atomic_write_file(file_path, modified_lines)

    return parsed_tasks, file_was_modified


def _atomic_write_file(file_path: str, lines: List[str]):
    """Writes lines to a file atomically via temporary file replacement."""
    dir_name = os.path.dirname(file_path)
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
        tf.writelines(lines)
        temp_name = tf.name
    os.replace(temp_name, file_path)


def sync_tasks(
    pkm_dir: Optional[str] = None,
    db_path: Optional[str] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Main synchronization routine:
    1. Scans ~/pkm/ticktick and ~/pkm/vault
    2. Assigns missing IDs to vault tasks
    3. Upserts all tasks into SQLite DB
    4. Prunes deleted tasks
    Returns summary statistics.
    """
    pkm_dir = pkm_dir or get_pkm_dir()
    db_path = db_path or get_db_path()

    if not os.path.isdir(pkm_dir):
        raise FileNotFoundError(f"PKM directory not found: {pkm_dir}")

    conn = get_connection(db_path)
    init_db(conn)

    files_to_scan = scan_markdown_files(pkm_dir)
    all_tasks = []
    files_modified_count = 0
    all_task_ids = []

    for full_path, rel_path, source_type in files_to_scan:
        tasks, modified = process_file(full_path, rel_path, source_type, dry_run=dry_run)
        if modified:
            files_modified_count += 1
        all_tasks.extend(tasks)
        for t in tasks:
            all_task_ids.append(t["id"])

    # Upsert into DB
    upsert_stats = upsert_tasks(conn, all_tasks)
    
    # Prune tasks that no longer exist
    pruned_count = prune_deleted_tasks(conn, all_task_ids)

    conn.close()

    return {
        "status": "success",
        "scanned_files": len(files_to_scan),
        "modified_markdown_files": files_modified_count,
        "total_tasks_found": len(all_tasks),
        "inserted": upsert_stats["inserted"],
        "updated": upsert_stats["updated"],
        "unchanged": upsert_stats["unchanged"],
        "pruned": pruned_count,
        "db_path": db_path,
        "dry_run": dry_run
    }
