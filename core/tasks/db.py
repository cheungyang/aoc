import os
import json
import sqlite3
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple


from core.config import Config

DEFAULT_DB_PATH = os.path.expanduser("~/pkm/tasks.db")


def get_db_path() -> str:
    """Returns the configured database path or defaults to ~/pkm/tasks.db."""
    return Config().tasks_db_path


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates a sqlite3 connection with Row factory."""
    path = db_path or get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection):
    """Initializes the tasks schema, indexes, and full-text search virtual tables."""
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        raw_title TEXT,
        status TEXT NOT NULL CHECK(status IN ('todo', 'completed', 'dropped')),
        priority TEXT,
        priority_rank INTEGER DEFAULT 99,
        tags TEXT,
        scheduled_date TEXT,
        due_date TEXT,
        completed_date TEXT,
        dropped_date TEXT,
        created_date TEXT,
        source TEXT NOT NULL,
        source_type TEXT NOT NULL CHECK(source_type IN ('ticktick', 'vault')),
        line_number INTEGER NOT NULL,
        raw_line TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        updated_date TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_priority_rank ON tasks(priority_rank);
    CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);
    CREATE INDEX IF NOT EXISTS idx_tasks_scheduled_date ON tasks(scheduled_date);
    CREATE INDEX IF NOT EXISTS idx_tasks_source ON tasks(source);
    CREATE INDEX IF NOT EXISTS idx_tasks_updated_date ON tasks(updated_date);
    """)

    # Setup FTS5 full-text search table if supported
    try:
        cursor.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
            id UNINDEXED,
            title,
            tags,
            source,
            content='tasks',
            content_rowid='rowid'
        );

        CREATE TRIGGER IF NOT EXISTS tasks_ai AFTER INSERT ON tasks BEGIN
          INSERT INTO tasks_fts(rowid, id, title, tags, source) 
          VALUES (new.rowid, new.id, new.title, new.tags, new.source);
        END;

        CREATE TRIGGER IF NOT EXISTS tasks_ad AFTER DELETE ON tasks BEGIN
          INSERT INTO tasks_fts(tasks_fts, rowid, id, title, tags, source) 
          VALUES('delete', old.rowid, old.id, old.title, old.tags, old.source);
        END;

        CREATE TRIGGER IF NOT EXISTS tasks_au AFTER UPDATE ON tasks BEGIN
          INSERT INTO tasks_fts(tasks_fts, rowid, id, title, tags, source) 
          VALUES('delete', old.rowid, old.id, old.title, old.tags, old.source);
          INSERT INTO tasks_fts(rowid, id, title, tags, source) 
          VALUES (new.rowid, new.id, new.title, new.tags, new.source);
        END;
        """)
    except Exception as e:
        print(f"Warning: FTS5 setup skipped or encountered error: {e}")

    conn.commit()


def compute_content_hash(task: Dict[str, Any]) -> str:
    """Computes a hash of the task's semantic fields to detect changes."""
    tags_str = json.dumps(sorted(task.get("tags") or []))
    raw_str = "|".join([
        str(task.get("title", "")),
        str(task.get("status", "")),
        str(task.get("priority", "")),
        str(task.get("priority_rank", 99)),
        tags_str,
        str(task.get("scheduled_date", "")),
        str(task.get("due_date", "")),
        str(task.get("completed_date", "")),
        str(task.get("dropped_date", "")),
        str(task.get("source", "")),
        str(task.get("line_number", 0)),
    ])
    return hashlib.md5(raw_str.encode("utf-8")).hexdigest()


def upsert_tasks(conn: sqlite3.Connection, tasks: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Inserts new tasks and updates modified tasks.
    Returns stats dict: {'inserted': int, 'updated': int, 'unchanged': int}.
    """
    cursor = conn.cursor()
    now_iso = datetime.now().isoformat(timespec="seconds")

    # Fetch existing hashes
    cursor.execute("SELECT id, content_hash FROM tasks")
    existing = {row["id"]: row["content_hash"] for row in cursor.fetchall()}

    inserted_count = 0
    updated_count = 0
    unchanged_count = 0

    insert_rows = []
    update_rows = []
    seen_in_batch = set()

    for t in tasks:
        tid = t["id"]
        if tid in seen_in_batch:
            continue
        seen_in_batch.add(tid)

        c_hash = compute_content_hash(t)
        tags_json = json.dumps(t.get("tags") or [])

        row_tuple = (
            t["title"],
            t.get("raw_title", ""),
            t["status"],
            t.get("priority"),
            t.get("priority_rank", 99),
            tags_json,
            t.get("scheduled_date"),
            t.get("due_date"),
            t.get("completed_date"),
            t.get("dropped_date"),
            t.get("created_date"),
            t["source"],
            t["source_type"],
            t["line_number"],
            t.get("raw_line", ""),
            c_hash,
            now_iso,
            tid
        )

        if tid not in existing:
            # Insert
            insert_rows.append((
                tid,
                t["title"],
                t.get("raw_title", ""),
                t["status"],
                t.get("priority"),
                t.get("priority_rank", 99),
                tags_json,
                t.get("scheduled_date"),
                t.get("due_date"),
                t.get("completed_date"),
                t.get("dropped_date"),
                t.get("created_date"),
                t["source"],
                t["source_type"],
                t["line_number"],
                t.get("raw_line", ""),
                c_hash,
                now_iso
            ))
            inserted_count += 1
        elif existing[tid] != c_hash:
            # Update
            update_rows.append(row_tuple)
            updated_count += 1
        else:
            unchanged_count += 1

    if insert_rows:
        cursor.executemany("""
        INSERT INTO tasks (
            id, title, raw_title, status, priority, priority_rank, tags,
            scheduled_date, due_date, completed_date, dropped_date, created_date,
            source, source_type, line_number, raw_line, content_hash, updated_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, insert_rows)

    if update_rows:
        cursor.executemany("""
        UPDATE tasks SET
            title = ?,
            raw_title = ?,
            status = ?,
            priority = ?,
            priority_rank = ?,
            tags = ?,
            scheduled_date = ?,
            due_date = ?,
            completed_date = ?,
            dropped_date = ?,
            created_date = ?,
            source = ?,
            source_type = ?,
            line_number = ?,
            raw_line = ?,
            content_hash = ?,
            updated_date = ?
        WHERE id = ?
        """, update_rows)

    conn.commit()
    return {
        "inserted": inserted_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "total_scanned": len(tasks)
    }


def prune_deleted_tasks(conn: sqlite3.Connection, current_task_ids: List[str]) -> int:
    """
    Deletes records from SQLite whose IDs were not found in the latest scan.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM tasks")
    db_ids = {row["id"] for row in cursor.fetchall()}
    current_set = set(current_task_ids)

    to_delete = db_ids - current_set
    if to_delete:
        cursor.executemany("DELETE FROM tasks WHERE id = ?", [(tid,) for tid in to_delete])
        conn.commit()
    return len(to_delete)


def query_tasks_db(
    conn: sqlite3.Connection,
    status: Optional[str] = "todo",
    tags: Optional[List[str]] = None,
    priority: Optional[str] = None,
    min_priority: Optional[str] = None,
    due_before: Optional[str] = None,
    due_after: Optional[str] = None,
    scheduled_date: Optional[str] = None,
    scheduled_before: Optional[str] = None,
    scheduled_after: Optional[str] = None,
    source: Optional[str] = None,
    search_term: Optional[str] = None,
    order_by: str = "priority_rank ASC, due_date ASC, scheduled_date ASC",
    limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Flexible query method to filter tasks with rich conditions.
    """
    cursor = conn.cursor()
    where_clauses = []
    params = []

    if status and status.lower() != "all":
        where_clauses.append("status = ?")
        params.append(status.lower())

    if priority:
        where_clauses.append("priority = ?")
        params.append(priority)

    from core.tasks.parser import PRIORITY_MAP
    if min_priority and min_priority in PRIORITY_MAP:
        max_rank = PRIORITY_MAP[min_priority]
        where_clauses.append("priority_rank <= ?")
        params.append(max_rank)

    if due_before:
        where_clauses.append("due_date IS NOT NULL AND due_date <= ?")
        params.append(due_before)

    if due_after:
        where_clauses.append("due_date IS NOT NULL AND due_date >= ?")
        params.append(due_after)

    if scheduled_date:
        where_clauses.append("scheduled_date = ?")
        params.append(scheduled_date)

    if scheduled_before:
        where_clauses.append("scheduled_date IS NOT NULL AND scheduled_date <= ?")
        params.append(scheduled_before)

    if scheduled_after:
        where_clauses.append("scheduled_date IS NOT NULL AND scheduled_date >= ?")
        params.append(scheduled_after)

    if source:
        where_clauses.append("source LIKE ?")
        params.append(f"%{source}%")

    if tags:
        for tag in tags:
            clean_t = tag.lstrip("#")
            where_clauses.append("tags LIKE ?")
            params.append(f"%\"{clean_t}\"%")

    if search_term:
        where_clauses.append("(title LIKE ? OR tags LIKE ? OR source LIKE ?)")
        term_param = f"%{search_term}%"
        params.extend([term_param, term_param, term_param])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    
    # Sanitize order_by
    allowed_order_fields = [
        "priority_rank ASC", "priority_rank DESC",
        "due_date ASC", "due_date DESC",
        "scheduled_date ASC", "scheduled_date DESC",
        "updated_date ASC", "updated_date DESC",
        "title ASC", "title DESC",
        "priority_rank ASC, due_date ASC, scheduled_date ASC"
    ]
    if order_by not in allowed_order_fields:
        order_by = "priority_rank ASC, due_date ASC, scheduled_date ASC"

    sql = f"SELECT * FROM tasks {where_sql} ORDER BY {order_by} LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    results = []
    for r in rows:
        d = dict(r)
        if d.get("tags"):
            try:
                d["tags"] = json.loads(d["tags"])
            except Exception:
                pass
        results.append(d)
    return results


def get_task_by_id(conn: sqlite3.Connection, task_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves a single task by ID."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cursor.fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("tags"):
        try:
            d["tags"] = json.loads(d["tags"])
        except Exception:
            pass
    return d


def get_task_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Returns summary metrics on tasks."""
    cursor = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT status, count(*) as count FROM tasks GROUP BY status")
    status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

    cursor.execute("SELECT priority, count(*) as count FROM tasks WHERE status = 'todo' AND priority IS NOT NULL GROUP BY priority")
    priority_counts = {row["priority"]: row["count"] for row in cursor.fetchall()}

    cursor.execute("SELECT count(*) as count FROM tasks WHERE status = 'todo' AND due_date < ?", (today_str,))
    overdue_count = cursor.fetchone()["count"]

    cursor.execute("SELECT count(*) as count FROM tasks WHERE status = 'todo' AND scheduled_date = ?", (today_str,))
    today_scheduled_count = cursor.fetchone()["count"]

    cursor.execute("SELECT count(*) as count FROM tasks WHERE status = 'todo' AND due_date = ?", (today_str,))
    today_due_count = cursor.fetchone()["count"]

    return {
        "total_tasks": sum(status_counts.values()),
        "status": status_counts,
        "priority_todo": priority_counts,
        "overdue_todo": overdue_count,
        "today_scheduled": today_scheduled_count,
        "today_due": today_due_count,
    }


def execute_read_sql(conn: sqlite3.Connection, sql: str, limit: int = 50) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Executes a read-only SQL query against tasks.db safely.
    Rejects any non-SELECT statements.
    """
    clean_sql = sql.strip()
    if not clean_sql.upper().startswith("SELECT"):
        return [], "Error: Only read-only SELECT queries are permitted."

    cursor = conn.cursor()
    try:
        cursor.execute(clean_sql)
        rows = cursor.fetchmany(limit)
        results = [dict(r) for r in rows]
        return results, None
    except Exception as e:
        return [], str(e)
