import json
import sqlite3
from typing import Optional, List
from langchain_core.tools import tool
from core.loaders.tools_loader import ToolsLoader
from core.util import format_tool_response
from core.tasks.db import (
    get_connection,
    init_db,
    query_tasks_db,
    get_task_by_id,
    get_task_stats,
    execute_read_sql,
    get_db_path,
)
from core.tasks.sync import sync_tasks


@tool
def task_query(
    agent_id: str,
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
    limit: int = 50,
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
    - 'sync': Triggers an immediate synchronization from Obsidian markdown files into tasks.db.

    Args:
        agent_id: The ID of the agent executing the tool.
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
        limit: Maximum number of records to return (defaults to 50).
    """
    if not agent_id:
        return format_tool_response("task_query", payload="", errors="Error: agent_id is required.")

    tools_loader = ToolsLoader()
    # Check permissions if configured
    merged_perms = tools_loader._merge_tool_permissions(agent_id)
    if "task_query" in merged_perms:
        perms = merged_perms["task_query"]
        if isinstance(perms, list) and perms:
            if action not in perms and "*" not in perms:
                return format_tool_response(
                    "task_query",
                    payload="",
                    errors=f"Error: Agent {agent_id} does not have permission to execute action '{action}' on task_query."
                )

    try:
        if action == "sync":
            sync_result = sync_tasks()
            return format_tool_response("task_query", payload=json.dumps(sync_result, indent=2))

        conn = get_connection()
        init_db(conn)

        if action == "get":
            if not task_id:
                conn.close()
                return format_tool_response("task_query", payload="", errors="Error: 'task_id' is required for action='get'.")
            task = get_task_by_id(conn, task_id.strip())
            conn.close()
            if not task:
                return format_tool_response("task_query", payload="", errors=f"Error: Task not found with ID '{task_id}'.")
            return format_tool_response("task_query", payload=json.dumps(task, indent=2))

        elif action == "stats":
            stats = get_task_stats(conn)
            conn.close()
            return format_tool_response("task_query", payload=json.dumps(stats, indent=2))

        elif action == "sql":
            if not sql:
                conn.close()
                return format_tool_response("task_query", payload="", errors="Error: 'sql' query parameter is required for action='sql'.")
            results, err = execute_read_sql(conn, sql, limit=limit)
            conn.close()
            if err:
                return format_tool_response("task_query", payload="", errors=err)
            return format_tool_response("task_query", payload=json.dumps(results, indent=2))

        elif action == "search":
            results = query_tasks_db(
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
                limit=limit
            )
            conn.close()
            payload = {
                "count": len(results),
                "tasks": results
            }
            return format_tool_response("task_query", payload=json.dumps(payload, indent=2))

        else:
            conn.close()
            return format_tool_response("task_query", payload="", errors=f"Error: Unknown action '{action}'. Supported actions: 'search', 'get', 'stats', 'sql', 'sync'.")

    except Exception as e:
        return format_tool_response("task_query", payload="", errors=f"Error in task_query: {e}")
