import os
import tempfile
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from core.knowledge.projects.parser import parse_project_file
from core.knowledge.projects.db import (
    get_connection,
    init_db,
    upsert_projects,
    prune_deleted_projects,
    get_db_path,
)
from core.util.config import Config


def get_pkm_dir() -> str:
    """Returns the configured PKM directory path."""
    return Config().pkm_dir


def get_projects_dir(pkm_dir: Optional[str] = None) -> str:
    """Returns the configured projects directory path or defaults to ~/pkm/vault/projects."""
    base_pkm = pkm_dir or get_pkm_dir()
    return os.path.join(base_pkm, "vault", "projects")


def scan_project_files(projects_dir: str, pkm_dir: str) -> List[Tuple[str, str]]:
    """
    Finds all project markdown files in projects_dir.
    Returns a list of tuples: (full_file_path, relative_path_to_pkm).
    """
    target_files = []
    if not os.path.isdir(projects_dir):
        return target_files

    for root, dirs, files in os.walk(projects_dir):
        # Exclude system/hidden directories
        if ".git" in dirs:
            dirs.remove(".git")
        if ".obsidian" in dirs:
            dirs.remove(".obsidian")
        if "assets" in dirs:
            dirs.remove("assets")

        for file in sorted(files):
            if file.endswith(".md") and not file.startswith("."):
                full_p = os.path.join(root, file)
                rel_p = os.path.relpath(full_p, pkm_dir)
                target_files.append((full_p, rel_p))

    return target_files


def _atomic_write_file(file_path: str, content: str):
    """Writes content to a file atomically via temporary file replacement."""
    dir_name = os.path.dirname(file_path)
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
        tf.write(content)
        temp_name = tf.name
    os.replace(temp_name, file_path)


def process_project_file(
    file_path: str,
    rel_path: str,
    pkm_dir: Optional[str] = None,
    auto_assign_id: bool = True,
    dry_run: bool = False
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """
    Reads a markdown project file, extracts/assigns frontmatter UUID, and parses metadata.
    Returns (parsed_project_dict, was_file_modified).
    """
    try:
        project_data, updated_content = parse_project_file(
            file_path,
            rel_path=rel_path,
            pkm_dir=pkm_dir,
            auto_assign_id=auto_assign_id
        )
        was_modified = updated_content is not None
        if was_modified and not dry_run:
            _atomic_write_file(file_path, updated_content)
            if project_data:
                try:
                    mtime = os.path.getmtime(file_path)
                    project_data["last_updated"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
        return project_data, was_modified
    except Exception as e:
        print(f"Error reading/parsing project {file_path}: {e}")
        return None, False


def sync_projects(
    pkm_dir: Optional[str] = None,
    db_path: Optional[str] = None,
    projects_dir: Optional[str] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Main synchronization routine for Obsidian projects:
    1. Scans ~/pkm/vault/projects (or configured projects_dir)
    2. Injects/ensures persistent frontmatter UUID in each project file
    3. Parses YAML frontmatter, tags, statuses, commitments, dates
    4. Upserts projects and multi-year commitments into SQLite DB
    5. Prunes deleted projects
    Returns summary statistics.
    """
    pkm_dir = pkm_dir or get_pkm_dir()
    db_path = db_path or get_db_path()
    projects_dir = projects_dir or get_projects_dir(pkm_dir)

    if not os.path.isdir(pkm_dir):
        raise FileNotFoundError(f"PKM directory not found: {pkm_dir}")

    if not os.path.isdir(projects_dir):
        raise FileNotFoundError(f"Projects directory not found: {projects_dir}")

    conn = get_connection(db_path)
    init_db(conn)

    files_to_scan = scan_project_files(projects_dir, pkm_dir)
    all_projects = []
    all_project_ids = []
    modified_files_count = 0

    for full_path, rel_path in files_to_scan:
        project_data, was_modified = process_project_file(
            full_path,
            rel_path,
            pkm_dir=pkm_dir,
            auto_assign_id=True,
            dry_run=dry_run
        )
        if was_modified:
            modified_files_count += 1
        if project_data:
            all_projects.append(project_data)
            all_project_ids.append(project_data["id"])

    if not dry_run:
        # Upsert into DB
        upsert_stats = upsert_projects(conn, all_projects)
        # Prune projects that no longer exist
        pruned_count = prune_deleted_projects(conn, all_project_ids)
    else:
        upsert_stats = {
            "inserted": len(all_projects),
            "updated": 0,
            "unchanged": 0,
            "total_scanned": len(all_projects)
        }
        pruned_count = 0

    conn.close()

    return {
        "status": "success",
        "scanned_files": len(files_to_scan),
        "modified_files": modified_files_count,
        "total_projects_found": len(all_projects),
        "inserted": upsert_stats["inserted"],
        "updated": upsert_stats["updated"],
        "unchanged": upsert_stats["unchanged"],
        "pruned": pruned_count,
        "db_path": db_path,
        "dry_run": dry_run
    }
