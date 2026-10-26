import json
import sqlite3
from typing import Optional, List
from langchain_core.tools import tool
from core.loaders.tools_loader import ToolsLoader
from core.tooling import format_tool_response
from core.runtime.foundation.context import try_context
from core.storage.tasks import TasksStore
from core.knowledge.tasks.sync import sync_tasks


@tool
def task_query(
    action: str = "search",
    status: str = "todo",
    query: str = "",
    tags: Optional[List[str]] = None,
    priority: str = "",
    min_priority: str = "",
    due_before: str = "",
    due_after: str = "",
    scheduled_date: str = "",
    source: str = "",
    task_id: str = "",
    sql: str = "",
    limit: int = 10,
    compact: bool = True,
) -> str:
    """
    Search and inspect tasks from the Obsidian PKM SQLite cache (~/pkm/tasks.db).

    Supported Actions:
    - 'search': Searches and filters tasks by status, tags, priority, dates, or keywords.
        Optional args: 'status' ('todo'|'completed'|'dropped'|'all'), 'query', 'tags' (e.g. ['p/aoc']),
        'priority' ('🔺'|'⏫'|'🔼'|'🔽'|'⏬'), 'min_priority', 'due_before', 'due_after', 'scheduled_date',
        'source' (path substring), 'limit'.
    - 'get': Retrieves full task details for a given 'task_id'.
        Requires: 'task_id'.
    - 'stats': Returns summary statistics on tasks (overdue count, scheduled today, priority breakdown).
    - 'sql': Executes a custom read-only SELECT SQL query against tasks.db.
        Requires: 'sql' (e.g., "SELECT title, due_date FROM tasks WHERE status='todo' AND due_date IS NOT NULL").
        Returns at most 'limit' rows whatever the query's own LIMIT, so pass a 'limit' that covers it.
    - 'sync': Triggers an immediate synchronization from Obsidian markdown files into tasks.db.

    'search' and 'sql' return {"count", "tasks" or "rows"}; when more rows match than 'limit',
    a "note" says how many were not shown.

    Args:
        action: The action to perform ('search', 'get', 'stats', 'sql', 'sync'). Defaults to 'search'.
        status: Task status filter ('todo', 'completed', 'dropped', 'all'). Defaults to 'todo'.
        query: Keyword to search within task title, tags, or source file path.
        tags: List of tags to filter by (e.g. ['p/aoc', 'a/learn']).
        priority: Specific priority emoji to match ('🔺', '⏫', '🔼', '🔽', '⏬').
        min_priority: Minimum priority emoji threshold (e.g. '🔼' returns '🔺', '⏫', '🔼').
        due_before: Match tasks with due_date on or before this ISO date (YYYY-MM-DD).
        due_after: Match tasks with due_date on or after this ISO date (YYYY-MM-DD).
        scheduled_date: Match tasks scheduled for this exact ISO date (YYYY-MM-DD).
        source: Filter by file or folder path substring (e.g., 'projects' or 'Inbox.md').
        task_id: Task ID to fetch (required when action='get').
        sql: Custom read-only SELECT query (required when action='sql').
        limit: Maximum rows to return for 'search' and 'sql' (defaults to 10).
    """
    ctx = try_context()
    if ctx is None:
        return format_tool_response("task_query", payload="", errors="Error: no active execution context; this tool must be called from an agent run.")

    tools_loader = ToolsLoader()
    # Check permissions if configured
    merged_perms = tools_loader._merge_tool_permissions(ctx)
    if "task_query" in merged_perms:
        perms = merged_perms["task_query"]
        if isinstance(perms, list) and perms:
            if action not in perms and "*" not in perms:
                return format_tool_response(
                    "task_query",
                    payload="",
                    errors=f"Error: Agent {ctx.agent_id} does not have permission to execute action '{action}' on task_query."
                )

    try:
        if action == "sync":
            sync_result = sync_tasks()
            return format_tool_response("task_query", payload=json.dumps(sync_result, indent=2))

        store = TasksStore()
        with store.connect() as conn:
            store.init(conn)

            if action == "get":
                if not task_id:
                    return format_tool_response("task_query", payload="", errors="Error: 'task_id' is required for action='get'.")
                task = store.get_task(task_id.strip(), conn=conn)
                if not task:
                    return format_tool_response("task_query", payload="", errors=f"Error: Task not found with ID '{task_id}'.")
                return format_tool_response("task_query", payload=json.dumps(task, indent=2))

            elif action == "stats":
                stats = store.stats(conn=conn)
                return format_tool_response("task_query", payload=json.dumps(stats, indent=2))

            elif action == "sql":
                if not sql:
                    return format_tool_response("task_query", payload="", errors="Error: 'sql' query parameter is required for action='sql'.")
                limit = max(1, limit)
                # One row past the cap is enough to tell whether anything was cut.
                results, err = store.read_sql(sql, limit=limit + 1, conn=conn)
                if err:
                    return format_tool_response("task_query", payload="", errors=err)
                hidden = 0
                if len(results) > limit:
                    total = _count_rows(store, conn, sql)
                    hidden = max(1, total - limit) if total is not None else None
                return format_tool_response("task_query", payload=_capped_payload("rows", results[:limit], hidden))

            elif action == "search":
                limit = max(1, limit)
                results = store.search(
                    conn=conn,
                    status=status,
                    tags=tags,
                    priority=priority,
                    min_priority=min_priority,
                    due_before=due_before,
                    due_after=due_after,
                    scheduled_date=scheduled_date,
                    source=source,
                    search_term=query,
                    # Every match, so the overflow can be counted: SQLite reads a
                    # negative LIMIT as no limit. The table is a few thousand rows.
                    limit=-1,
                    compact=compact
                )
                shown = results[:limit]
                return format_tool_response("task_query", payload=_capped_payload("tasks", shown, len(results) - len(shown)))

            else:
                return format_tool_response("task_query", payload="", errors=f"Error: Unknown action '{action}'. Supported actions: 'search', 'get', 'stats', 'sql', 'sync'.")

    except Exception as e:
        return format_tool_response("task_query", payload="", errors=f"Error in task_query: {e}")


def _capped_payload(key: str, shown: list, hidden: Optional[int]) -> str:
    """JSON for a result cut to `limit` rows; `hidden` is how many were left out (None: unknown).

    The note goes before the rows so it is read first: a silently capped result
    reads as the whole answer, and an agent reasons from it as if it were.
    """
    payload = {"count": len(shown)}
    if hidden != 0:
        more = f"{hidden} more row{'' if hidden == 1 else 's'}" if hidden is not None else "More rows"
        payload["note"] = f"{more} not shown; raise 'limit' or narrow the query."
    payload[key] = shown
    return json.dumps(payload, indent=2)


def _count_rows(store: TasksStore, conn: sqlite3.Connection, sql: str) -> Optional[int]:
    """How many rows `sql` returns in total, or None when it cannot be counted.

    SQLite does the counting, so the rows past the cap are never fetched into
    Python. The newlines keep a trailing `--` comment in the agent's query from
    swallowing the closing parenthesis.
    """
    inner = sql.strip().rstrip(";").strip()
    rows, err = store.read_sql(f"SELECT COUNT(*) AS n FROM (\n{inner}\n)", limit=1, conn=conn)
    return None if err or not rows else rows[0]["n"]
