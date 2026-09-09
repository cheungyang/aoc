"""
Core projects module for managing Obsidian PKM projects in SQLite.
"""
from core.knowledge.projects.parser import (
    parse_project_file,
    parse_project_content,
    PRIORITY_MAP,
    STATUS_MAP,
)
from core.knowledge.projects.db import (
    get_db_path,
    get_connection,
    init_db,
    upsert_projects,
    prune_deleted_projects,
    query_projects_db,
    get_project_by_id,
    get_project_by_name,
    get_project_stats,
    execute_read_sql,
)
from core.knowledge.projects.sync import (
    sync_projects,
    scan_project_files,
    process_project_file,
    get_projects_dir,
    get_pkm_dir,
)

__all__ = [
    "parse_project_file",
    "parse_project_content",
    "PRIORITY_MAP",
    "STATUS_MAP",
    "get_db_path",
    "get_connection",
    "init_db",
    "upsert_projects",
    "prune_deleted_projects",
    "query_projects_db",
    "get_project_by_id",
    "get_project_by_name",
    "get_project_stats",
    "execute_read_sql",
    "sync_projects",
    "scan_project_files",
    "process_project_file",
    "get_projects_dir",
    "get_pkm_dir",
]
