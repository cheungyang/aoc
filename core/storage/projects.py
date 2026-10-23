"""projects.db: the project cache core/knowledge/projects/sync.py rebuilds from the vault.

ProjectsStore holds every statement on its tables: `projects`, one row per
project note, and `project_commitments`, one row per year a project is
committed to. As in tasks.db, the schema (schemas/projects.sql, plus
projects_fts.sql where SQLite has FTS5) is created by `init()` only, never on
connect.
"""
import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.storage.base import Store
from core.storage.util import decode_json
from core.util.config import Config


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


_JSON_COLUMNS = ("tags", "commitments", "aliases")


class ProjectsStore(Store):
    """projects.db, at `Config().projects_db_path` unless a path is given.

    Each method opens its own connection unless `conn=` is given: a
    `connect()` block to share its transaction, or a `read_only()` one to read.
    """

    schema = ("projects",)
    optional_schema = ("projects_fts",)
    pragmas = ("foreign_keys = ON",)  # deleting a project deletes its commitments
    init_on_connect = False

    def default_path(self) -> str:
        return Config().projects_db_path

    def upsert_projects(self, projects: List[Dict[str, Any]], *,
                        conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
        """
        Inserts new projects and updates modified projects.
        Maintains the project_commitments relational table for multi-year commitments.
        Returns stats dict: {'inserted': int, 'updated': int, 'unchanged': int, 'total_scanned': int}.
        """
        now_iso = datetime.now().isoformat(timespec="seconds")

        with self.connect(conn) as c:
            # Fetch existing hashes
            rows = self.select("projects", columns=("id", "content_hash"), conn=c)
            existing = {row["id"]: row["content_hash"] for row in rows}

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
                c.executemany("""
                INSERT INTO projects (
                    id, name, file_path, status, raw_status, commitment_year, priority, priority_rank,
                    commitments, start_date, last_reviewed, last_updated, category, type, aliases, tags,
                    content_hash, updated_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, insert_rows)

            if update_rows:
                c.executemany("""
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
                c.execute("DELETE FROM project_commitments WHERE id = ?", (pid,))
                if c_list:
                    c_rows = [
                        (pid, item["year"], item.get("priority"), item.get("priority_rank", 99))
                        for item in c_list
                    ]
                    c.executemany("""
                    INSERT OR REPLACE INTO project_commitments (id, year, priority, priority_rank)
                    VALUES (?, ?, ?, ?)
                    """, c_rows)

        return {
            "inserted": inserted_count,
            "updated": updated_count,
            "unchanged": unchanged_count,
            "total_scanned": len(projects)
        }

    def prune_deleted(self, current_project_ids: List[str], *,
                      conn: Optional[sqlite3.Connection] = None) -> int:
        """
        Deletes records from SQLite whose IDs were not found in the latest scan.
        """
        with self.connect(conn) as c:
            db_ids = {row["id"] for row in self.select("projects", columns=("id",), conn=c)}
            current_set = set(current_project_ids)

            to_delete = db_ids - current_set
            if to_delete:
                c.executemany("DELETE FROM projects WHERE id = ?", [(pid,) for pid in to_delete])
                c.executemany("DELETE FROM project_commitments WHERE id = ?", [(pid,) for pid in to_delete])
        return len(to_delete)

    def search(
        self,
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
        limit: int = 10,
        compact: bool = True,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[Dict[str, Any]]:
        """
        Flexible query method to search and filter projects.
        When compact=True (default), returns essential project metadata.
        """
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

        # Imported here: importing core.knowledge.projects runs its __init__, whose sync imports
        # this module, so a module-level import would be a cycle.
        from core.knowledge.projects.parser import PRIORITY_MAP
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

        rows = self.query(sql, params, conn=conn)
        results = []
        for r in rows:
            d = decode_json(dict(r), _JSON_COLUMNS)
            if compact:
                results.append({
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "status": d.get("status"),
                    "priority": d.get("priority"),
                    "commitment_year": d.get("commitment_year"),
                    "category": d.get("category"),
                    "tags": d.get("tags"),
                    "file_path": d.get("file_path"),
                })
            else:
                results.append(d)
        return results

    def get_project(self, project_id: str, *,
                    conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        """Retrieves a single project by ID or file_path."""
        rows = self.query("SELECT * FROM projects WHERE id = ? OR file_path = ?", (project_id, project_id), conn=conn)
        if not rows:
            return None
        return decode_json(dict(rows[0]), _JSON_COLUMNS)

    def get_project_by_name(self, name: str, *,
                            conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        """Retrieves a single project by exact or case-insensitive name."""
        rows = self.query("SELECT * FROM projects WHERE name = ? COLLATE NOCASE", (name,), conn=conn)
        if not rows:
            return None
        return decode_json(dict(rows[0]), _JSON_COLUMNS)

    def count_with_status(self, status: str, *, conn: Optional[sqlite3.Connection] = None) -> int:
        """How many projects have `status`, whatever its case in the file ('Executing' counts); pass it lowercase."""
        return self.query("SELECT count(*) FROM projects WHERE lower(status) = ?", (status,), conn=conn)[0][0]

    def stats(self, *, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
        """Returns summary metrics on projects."""
        with self.connect(conn) as c:
            rows = self.query("SELECT status, count(*) as count FROM projects GROUP BY status", conn=c)
            status_counts = {row["status"] or "unspecified": row["count"] for row in rows}

            rows = self.query("SELECT category, count(*) as count FROM projects "
                              "WHERE category IS NOT NULL GROUP BY category", conn=c)
            category_counts = {row["category"]: row["count"] for row in rows}

            rows = self.query("SELECT year, count(*) as count FROM project_commitments "
                              "GROUP BY year ORDER BY year DESC", conn=c)
            year_counts = {str(row["year"]): row["count"] for row in rows}

            rows = self.query("SELECT priority, count(*) as count FROM projects "
                              "WHERE status = 'executing' AND priority IS NOT NULL GROUP BY priority", conn=c)
            active_priority_counts = {row["priority"]: row["count"] for row in rows}

            total_projects = self.count("projects", conn=c)

        return {
            "total_projects": total_projects,
            "status": status_counts,
            "categories": category_counts,
            "commitment_years": year_counts,
            "active_priorities": active_priority_counts,
        }

    def read_sql(self, sql: str, limit: int = 50, *,
                 conn: Optional[sqlite3.Connection] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Executes a read-only SELECT query against projects.db safely.
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
                results = [decode_json(dict(r), _JSON_COLUMNS) for r in rows]
                return results, None
            except Exception as e:
                return [], str(e)
