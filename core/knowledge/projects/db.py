import os
import json
import sqlite3
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from core.config import Config

DEFAULT_DB_PATH = os.path.expanduser("~/pkm/projects.db")


def get_db_path() -> str:
    """Returns the configured database path or defaults to ~/pkm/projects.db."""
    return Config().projects_db_path


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates a sqlite3 connection with Row factory and enabled foreign keys."""
    path = db_path or get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection):
    """Initializes the projects schema, child tables, indexes, and full-text search virtual tables."""
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        status TEXT,
        raw_status TEXT,
        commitment_year INTEGER,
        priority TEXT,
        priority_rank INTEGER DEFAULT 99,
        commitments TEXT,
        start_date TEXT,
        last_reviewed TEXT,
        last_updated TEXT,
        category TEXT,
        type TEXT DEFAULT 'project',
        aliases TEXT,
        tags TEXT,
        content_hash TEXT NOT NULL,
        updated_date TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS project_commitments (
        id TEXT NOT NULL,
        year INTEGER NOT NULL,
        priority TEXT,
        priority_rank INTEGER DEFAULT 99,
        PRIMARY KEY(id, year),
        FOREIGN KEY(id) REFERENCES projects(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
    CREATE INDEX IF NOT EXISTS idx_projects_commitment_year ON projects(commitment_year);
    CREATE INDEX IF NOT EXISTS idx_projects_priority_rank ON projects(priority_rank);
    CREATE INDEX IF NOT EXISTS idx_projects_category ON projects(category);
    CREATE INDEX IF NOT EXISTS idx_projects_last_reviewed ON projects(last_reviewed);
    CREATE INDEX IF NOT EXISTS idx_projects_last_updated ON projects(last_updated);
    CREATE INDEX IF NOT EXISTS idx_projects_file_path ON projects(file_path);

    CREATE INDEX IF NOT EXISTS idx_commitments_id ON project_commitments(id);
    CREATE INDEX IF NOT EXISTS idx_commitments_year ON project_commitments(year);
    CREATE INDEX IF NOT EXISTS idx_commitments_priority_rank ON project_commitments(priority_rank);
    """)

    # Setup FTS5 full-text search table if supported
    try:
        cursor.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS projects_fts USING fts5(
            id UNINDEXED,
            name,
            tags,
            category,
            aliases,
            file_path,
            content='projects',
            content_rowid='rowid'
        );

        CREATE TRIGGER IF NOT EXISTS projects_ai AFTER INSERT ON projects BEGIN
          INSERT INTO projects_fts(rowid, id, name, tags, category, aliases, file_path) 
          VALUES (new.rowid, new.id, new.name, new.tags, new.category, new.aliases, new.file_path);
        END;

        CREATE TRIGGER IF NOT EXISTS projects_ad AFTER DELETE ON projects BEGIN
          INSERT INTO projects_fts(projects_fts, rowid, id, name, tags, category, aliases, file_path) 
          VALUES('delete', old.rowid, old.id, old.name, old.tags, old.category, old.aliases, old.file_path);
        END;

        CREATE TRIGGER IF NOT EXISTS projects_au AFTER UPDATE ON projects BEGIN
          INSERT INTO projects_fts(projects_fts, rowid, id, name, tags, category, aliases, file_path) 
          VALUES('delete', old.rowid, old.id, old.name, old.tags, old.category, old.aliases, old.file_path);
          INSERT INTO projects_fts(rowid, id, name, tags, category, aliases, file_path) 
          VALUES (new.rowid, new.id, new.name, new.tags, new.category, new.aliases, new.file_path);
        END;
        """)
    except Exception as e:
        print(f"Warning: FTS5 setup for projects skipped or encountered error: {e}")

    conn.commit()


def compute_content_hash(project: Dict[str, Any]) -> str:
    """Computes a hash of the project's semantic fields to detect modifications."""
    tags_str = json.dumps(sorted(project.get("tags") or []))
    commitments_str = json.dumps(project.get("commitments") or [], sort_keys=True)
    aliases_str = json.dumps(sorted(project.get("aliases") or []))

    raw_str = "|".join([
        str(project.get("name", "")),
        str(project.get("file_path", "")),
        str(project.get("status", "")),
        str(project.get("raw_status", "")),
        str(project.get("commitment_year", "")),
        str(project.get("priority", "")),
        str(project.get("priority_rank", 99)),
        commitments_str,
        str(project.get("start_date", "")),
        str(project.get("last_reviewed", "")),
        str(project.get("last_updated", "")),
        str(project.get("category", "")),
        str(project.get("type", "")),
        aliases_str,
        tags_str,
    ])
    return hashlib.md5(raw_str.encode("utf-8")).hexdigest()


def upsert_projects(conn: sqlite3.Connection, projects: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Inserts new projects and updates modified projects.
    Maintains the project_commitments relational table for multi-year commitments.
    Returns stats dict: {'inserted': int, 'updated': int, 'unchanged': int, 'total_scanned': int}.
    """
    cursor = conn.cursor()
    now_iso = datetime.now().isoformat(timespec="seconds")

    # Fetch existing hashes
    cursor.execute("SELECT id, content_hash FROM projects")
    existing = {row["id"]: row["content_hash"] for row in cursor.fetchall()}

    inserted_count = 0
    updated_count = 0
    unchanged_count = 0

    insert_rows = []
    update_rows = []
    commitments_to_sync = []  # list of (project_id, commitments_list)
    seen_in_batch = set()

    for p in projects:
        pid = p["id"]
        if pid in seen_in_batch:
            continue
        seen_in_batch.add(pid)

        c_hash = compute_content_hash(p)
        tags_json = json.dumps(p.get("tags") or [])
        aliases_json = json.dumps(p.get("aliases") or [])
        commitments_json = json.dumps(p.get("commitments") or [])

        row_tuple = (
            p["name"],
            p["file_path"],
            p.get("status"),
            p.get("raw_status"),
            p.get("commitment_year"),
            p.get("priority"),
            p.get("priority_rank", 99),
            commitments_json,
            p.get("start_date"),
            p.get("last_reviewed"),
            p.get("last_updated"),
            p.get("category"),
            p.get("type", "project"),
            aliases_json,
            tags_json,
            c_hash,
            now_iso,
            pid
        )

        if pid not in existing:
            insert_rows.append((
                pid,
                p["name"],
                p["file_path"],
                p.get("status"),
                p.get("raw_status"),
                p.get("commitment_year"),
                p.get("priority"),
                p.get("priority_rank", 99),
                commitments_json,
                p.get("start_date"),
                p.get("last_reviewed"),
                p.get("last_updated"),
                p.get("category"),
                p.get("type", "project"),
                aliases_json,
                tags_json,
                c_hash,
                now_iso
            ))
            commitments_to_sync.append((pid, p.get("commitments") or []))
            inserted_count += 1
        elif existing[pid] != c_hash:
            update_rows.append(row_tuple)
            commitments_to_sync.append((pid, p.get("commitments") or []))
            updated_count += 1
        else:
            unchanged_count += 1

    if insert_rows:
        cursor.executemany("""
        INSERT INTO projects (
            id, name, file_path, status, raw_status, commitment_year, priority, priority_rank,
            commitments, start_date, last_reviewed, last_updated, category, type, aliases, tags,
            content_hash, updated_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, insert_rows)

    if update_rows:
        cursor.executemany("""
        UPDATE projects SET
            name = ?,
            file_path = ?,
            status = ?,
            raw_status = ?,
            commitment_year = ?,
            priority = ?,
            priority_rank = ?,
            commitments = ?,
            start_date = ?,
            last_reviewed = ?,
            last_updated = ?,
            category = ?,
            type = ?,
            aliases = ?,
            tags = ?,
            content_hash = ?,
            updated_date = ?
        WHERE id = ?
        """, update_rows)

    # Sync project_commitments relational table
    for pid, c_list in commitments_to_sync:
        cursor.execute("DELETE FROM project_commitments WHERE id = ?", (pid,))
        if c_list:
            c_rows = [
                (pid, item["year"], item.get("priority"), item.get("priority_rank", 99))
                for item in c_list
            ]
            cursor.executemany("""
            INSERT OR REPLACE INTO project_commitments (id, year, priority, priority_rank)
            VALUES (?, ?, ?, ?)
            """, c_rows)

    conn.commit()
    return {
        "inserted": inserted_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "total_scanned": len(projects)
    }


def prune_deleted_projects(conn: sqlite3.Connection, current_project_ids: List[str]) -> int:
    """
    Deletes records from SQLite whose IDs were not found in the latest scan.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM projects")
    db_ids = {row["id"] for row in cursor.fetchall()}
    current_set = set(current_project_ids)

    to_delete = db_ids - current_set
    if to_delete:
        cursor.executemany("DELETE FROM projects WHERE id = ?", [(pid,) for pid in to_delete])
        cursor.executemany("DELETE FROM project_commitments WHERE id = ?", [(pid,) for pid in to_delete])
        conn.commit()
    return len(to_delete)


def _deserialize_project_row(d: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to deserialize JSON fields in a project row."""
    for field in ("tags", "commitments", "aliases"):
        if d.get(field) and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                pass
    return d


def query_projects_db(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    commitment_year: Optional[int] = None,
    priority: Optional[str] = None,
    min_priority: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[List[str]] = None,
    last_reviewed_before: Optional[str] = None,
    last_reviewed_after: Optional[str] = None,
    last_updated_before: Optional[str] = None,
    last_updated_after: Optional[str] = None,
    search_term: Optional[str] = None,
    order_by: str = "commitment_year DESC, priority_rank ASC, name ASC",
    limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Flexible query method to search and filter projects.
    """
    cursor = conn.cursor()
    where_clauses = []
    params = []

    if status and status.lower() != "all":
        where_clauses.append("status = ?")
        params.append(status.lower())

    if commitment_year:
        # Check either primary commitment_year or in project_commitments child table
        where_clauses.append("(commitment_year = ? OR id IN (SELECT id FROM project_commitments WHERE year = ?))")
        params.extend([commitment_year, commitment_year])

    if priority:
        where_clauses.append("priority = ?")
        params.append(priority)

    from core.projects.parser import PRIORITY_MAP
    if min_priority and min_priority in PRIORITY_MAP:
        max_rank = PRIORITY_MAP[min_priority]
        where_clauses.append("priority_rank <= ?")
        params.append(max_rank)

    if category:
        where_clauses.append("category LIKE ?")
        params.append(f"%{category}%")

    if last_reviewed_before:
        where_clauses.append("last_reviewed IS NOT NULL AND last_reviewed <= ?")
        params.append(last_reviewed_before)

    if last_reviewed_after:
        where_clauses.append("last_reviewed IS NOT NULL AND last_reviewed >= ?")
        params.append(last_reviewed_after)

    if last_updated_before:
        where_clauses.append("last_updated IS NOT NULL AND last_updated <= ?")
        params.append(last_updated_before)

    if last_updated_after:
        where_clauses.append("last_updated IS NOT NULL AND last_updated >= ?")
        params.append(last_updated_after)

    if tags:
        for tag in tags:
            clean_t = tag.lstrip("#")
            where_clauses.append("tags LIKE ?")
            params.append(f"%{clean_t}%")

    if search_term:
        where_clauses.append("(name LIKE ? OR tags LIKE ? OR aliases LIKE ? OR category LIKE ? OR file_path LIKE ?)")
        term_param = f"%{search_term}%"
        params.extend([term_param, term_param, term_param, term_param, term_param])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    allowed_order_fields = [
        "commitment_year DESC, priority_rank ASC, name ASC",
        "commitment_year ASC, priority_rank ASC, name ASC",
        "priority_rank ASC, name ASC",
        "priority_rank DESC, name ASC",
        "name ASC", "name DESC",
        "last_reviewed ASC", "last_reviewed DESC",
        "last_updated ASC", "last_updated DESC",
        "start_date ASC", "start_date DESC",
    ]
    if order_by not in allowed_order_fields:
        order_by = "commitment_year DESC, priority_rank ASC, name ASC"

    sql = f"SELECT * FROM projects {where_sql} ORDER BY {order_by} LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    return [_deserialize_project_row(dict(r)) for r in rows]


def get_project_by_id(conn: sqlite3.Connection, project_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves a single project by ID or file_path."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE id = ? OR file_path = ?", (project_id, project_id))
    row = cursor.fetchone()
    if not row:
        return None
    return _deserialize_project_row(dict(row))


def get_project_by_name(conn: sqlite3.Connection, name: str) -> Optional[Dict[str, Any]]:
    """Retrieves a single project by exact or case-insensitive name."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE name = ? COLLATE NOCASE", (name,))
    row = cursor.fetchone()
    if not row:
        return None
    return _deserialize_project_row(dict(row))


def get_project_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Returns summary metrics on projects."""
    cursor = conn.cursor()

    cursor.execute("SELECT status, count(*) as count FROM projects GROUP BY status")
    status_counts = {row["status"] or "unspecified": row["count"] for row in cursor.fetchall()}

    cursor.execute("SELECT category, count(*) as count FROM projects WHERE category IS NOT NULL GROUP BY category")
    category_counts = {row["category"]: row["count"] for row in cursor.fetchall()}

    cursor.execute("SELECT year, count(*) as count FROM project_commitments GROUP BY year ORDER BY year DESC")
    year_counts = {str(row["year"]): row["count"] for row in cursor.fetchall()}

    cursor.execute("SELECT priority, count(*) as count FROM projects WHERE status = 'executing' AND priority IS NOT NULL GROUP BY priority")
    active_priority_counts = {row["priority"]: row["count"] for row in cursor.fetchall()}

    cursor.execute("SELECT count(*) as count FROM projects")
    total_projects = cursor.fetchone()["count"]

    return {
        "total_projects": total_projects,
        "status": status_counts,
        "categories": category_counts,
        "commitment_years": year_counts,
        "active_priorities": active_priority_counts,
    }


def execute_read_sql(conn: sqlite3.Connection, sql: str, limit: int = 50) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Executes a read-only SELECT query against projects.db safely.
    Rejects any non-SELECT statements.
    """
    clean_sql = sql.strip()
    if not clean_sql.upper().startswith("SELECT"):
        return [], "Error: Only read-only SELECT queries are permitted."

    cursor = conn.cursor()
    try:
        cursor.execute(clean_sql)
        rows = cursor.fetchmany(limit)
        results = [_deserialize_project_row(dict(r)) for r in rows]
        return results, None
    except Exception as e:
        return [], str(e)
