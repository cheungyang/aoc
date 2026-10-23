"""tasks.db: the task cache core/knowledge/tasks/sync.py rebuilds from the vault.

TasksStore holds every statement on its table: the hash-diff upsert and the
prune the sync runs, and the reads task_query and goal-setter make. The schema
(schemas/tasks.sql, plus tasks_fts.sql where SQLite has FTS5) is created by
`init()` only, never on connect: the scheduler's preconditions and their tests
open tables built by someone else.
"""
import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.storage.base import Store
from core.storage.util import decode_json
from core.util.config import Config


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


class TasksStore(Store):
    """tasks.db, at `Config().tasks_db_path` unless a path is given.

    Each method opens its own connection unless `conn=` is given: a
    `connect()` block to share its transaction, or a `read_only()` one to read.
    """

    schema = ("tasks",)
    optional_schema = ("tasks_fts",)
    init_on_connect = False

    def default_path(self) -> str:
        return Config().tasks_db_path

    def upsert_tasks(self, tasks: List[Dict[str, Any]], *,
                     conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
        """
        Inserts new tasks and updates modified tasks.
        Returns stats dict: {'inserted': int, 'updated': int, 'unchanged': int}.
        """
        now_iso = datetime.now().isoformat(timespec="seconds")

        with self.connect(conn) as c:
            # Fetch existing hashes
            rows = self.select("tasks", columns=("id", "content_hash"), conn=c)
            existing = {row["id"]: row["content_hash"] for row in rows}

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
                c.executemany("""
                INSERT INTO tasks (
                    id, title, raw_title, status, priority, priority_rank, tags,
                    scheduled_date, due_date, completed_date, dropped_date, created_date,
                    source, source_type, line_number, raw_line, content_hash, updated_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, insert_rows)

            if update_rows:
                c.executemany("""
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

        return {
            "inserted": inserted_count,
            "updated": updated_count,
            "unchanged": unchanged_count,
            "total_scanned": len(tasks)
        }

    def prune_deleted(self, current_task_ids: List[str], *,
                      conn: Optional[sqlite3.Connection] = None) -> int:
        """
        Deletes records from SQLite whose IDs were not found in the latest scan.
        """
        with self.connect(conn) as c:
            db_ids = {row["id"] for row in self.select("tasks", columns=("id",), conn=c)}
            current_set = set(current_task_ids)

            to_delete = db_ids - current_set
            if to_delete:
                c.executemany("DELETE FROM tasks WHERE id = ?", [(tid,) for tid in to_delete])
        return len(to_delete)

    def search(
        self,
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
        limit: int = 10,
        compact: bool = True,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[Dict[str, Any]]:
        """
        Flexible query method to filter tasks with rich conditions.
        When compact=True (default), returns only essential task fields (id, title, status, priority, due_date, scheduled_date, tags, source).
        """
        where_clauses = []
        params = []

        if status and status.lower() != "all":
            where_clauses.append("status = ?")
            params.append(status.lower())

        if priority:
            where_clauses.append("priority = ?")
            params.append(priority)

        # Imported here: core/knowledge imports this module, so it can't import core/knowledge on load.
        from core.knowledge.tasks.parser import PRIORITY_MAP
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

        rows = self.query(sql, params, conn=conn)
        results = []
        for r in rows:
            d = decode_json(dict(r), ("tags",))

            if compact:
                results.append({
                    "id": d.get("id"),
                    "title": d.get("title"),
                    "status": d.get("status"),
                    "priority": d.get("priority"),
                    "due_date": d.get("due_date"),
                    "scheduled_date": d.get("scheduled_date"),
                    "tags": d.get("tags"),
                    "source": d.get("source"),
                })
            else:
                results.append(d)
        return results

    def get_task(self, task_id: str, *, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        """Retrieves a single task by ID."""
        row = self.select_one("tasks", {"id": task_id}, conn=conn)
        return decode_json(dict(row), ("tags",)) if row else None

    def stats(self, *, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
        """Returns summary metrics on tasks."""
        today_str = datetime.now().strftime("%Y-%m-%d")

        with self.connect(conn) as c:
            rows = self.query("SELECT status, count(*) as count FROM tasks GROUP BY status", conn=c)
            status_counts = {row["status"]: row["count"] for row in rows}

            rows = self.query("SELECT priority, count(*) as count FROM tasks "
                              "WHERE status = 'todo' AND priority IS NOT NULL GROUP BY priority", conn=c)
            priority_counts = {row["priority"]: row["count"] for row in rows}

            overdue_count = self.query("SELECT count(*) as count FROM tasks WHERE status = 'todo' AND due_date < ?",
                                       (today_str,), conn=c)[0]["count"]
            today_scheduled_count = self.count("tasks", {"status": "todo", "scheduled_date": today_str}, conn=c)
            today_due_count = self.count("tasks", {"status": "todo", "due_date": today_str}, conn=c)

        return {
            "total_tasks": sum(status_counts.values()),
            "status": status_counts,
            "priority_todo": priority_counts,
            "overdue_todo": overdue_count,
            "today_scheduled": today_scheduled_count,
            "today_due": today_due_count,
        }

    def read_sql(self, sql: str, limit: int = 50, *,
                 conn: Optional[sqlite3.Connection] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Executes a read-only SQL query against tasks.db safely.
        Rejects any non-SELECT statements.
        """
        clean_sql = sql.strip()
        if not clean_sql.upper().startswith("SELECT"):
            return [], "Error: Only read-only SELECT queries are permitted."

        with self.connect(conn) as c:
            cursor = c.cursor()
            try:
                cursor.execute(clean_sql)
                rows = cursor.fetchmany(limit)
                results = [dict(r) for r in rows]
                return results, None
            except Exception as e:
                return [], str(e)
