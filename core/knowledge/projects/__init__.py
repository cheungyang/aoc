"""
Core projects module for managing Obsidian PKM projects in SQLite.

The parsing and the sync live here; projects.db itself (its schema and every
query) is core/storage/projects.py.
"""
from core.knowledge.projects.parser import (
    parse_project_file,
    parse_project_content,
    PRIORITY_MAP,
    STATUS_MAP,
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
    "sync_projects",
    "scan_project_files",
    "process_project_file",
    "get_projects_dir",
    "get_pkm_dir",
]
