import json
import sqlite3
from typing import Optional, List
from langchain_core.tools import tool
from core.loaders.tools_loader import ToolsLoader
from core.util import format_tool_response
from core.knowledge.projects.db import (
    get_connection,
    init_db,
    query_projects_db,
    get_project_by_id,
    get_project_by_name,
    get_project_stats,
    execute_read_sql,
    get_db_path,
)
from core.knowledge.projects.sync import sync_projects


@tool
def project_query(
    agent_id: str,
    action: str = "search",
    status: str = "",
    query: str = "",
    commitment_year: Optional[int] = None,
    priority: str = "",
    min_priority: str = "",
    category: str = "",
    tags: Optional[List[str]] = None,
    id: str = "",
    project_id: str = "",
    name: str = "",
    sql: str = "",
    limit: int = 10,
    compact: bool = True,
) -> str:
    """
    Search and inspect projects from the Obsidian PKM SQLite cache (~/pkm/projects.db).

    Supported Actions:
    - 'search': Searches and filters projects by status, commitment_year, priority, category, tags, or keywords.
        Optional args: 'status' ('executing'|'planning'|'considering'|'paused'|'done'|'discontinued'|'all'),
        'commitment_year' (e.g. 2025, 2026), 'priority' ('🔺'|'⏫'|'🔼'|'🔽'|'⏬'), 'min_priority',
        'category' (e.g. 'Personal', 'Gmail', 'Family'), 'tags', 'query' (search keyword), 'limit'.
    - 'get': Retrieves full project details for a given 'id' or 'name'.
        Requires: 'id' or 'name'.
    - 'stats': Returns summary statistics on projects (total, status breakdown, categories, commitment years).
    - 'sql': Executes a custom read-only SELECT SQL query against projects.db.
        Requires: 'sql' (e.g., "SELECT name, status, commitment_year, priority FROM projects WHERE status='executing'").
    - 'sync': Triggers an immediate synchronization from Obsidian markdown files into projects.db.

    Args:
        agent_id: The ID of the agent executing the tool.
        action: The action to perform ('search', 'get', 'stats', 'sql', 'sync'). Defaults to 'search'.
        status: Project status filter ('executing', 'planning', 'considering', 'paused', 'done', 'discontinued', 'all').
        query: Keyword to search within project name, tags, aliases, category, or file path.
        commitment_year: Match projects committed for this year (e.g., 2025, 2026).
        priority: Specific priority emoji to match ('🔺', '⏫', '🔼', '🔽', '⏬').
        min_priority: Minimum priority emoji threshold (e.g. '🔼' returns '🔺', '⏫', '🔼').
        category: Filter by category (e.g. 'Personal', 'Gmail', 'Family').
        tags: List of tags to filter by (e.g. ['p/aoc', 'c/🔺2026']).
        id: Project UUID or file path to fetch (used when action='get').
        project_id: Alias for 'id'.
        name: Project name to fetch (used when action='get').
        sql: Custom read-only SELECT query (required when action='sql').
        limit: Maximum number of records to return (defaults to 50).
    """
    if not agent_id:
        return format_tool_response("project_query", payload="", errors="Error: agent_id is required.")

    tools_loader = ToolsLoader()
    # Check permissions if configured
    try:
        merged_perms = tools_loader._merge_tool_permissions(agent_id)
        if "project_query" in merged_perms:
            perms = merged_perms["project_query"]
            if isinstance(perms, list) and perms:
                if action not in perms and "*" not in perms:
                    return format_tool_response(
                        "project_query",
                        payload="",
                        errors=f"Error: Agent {agent_id} does not have permission to execute action '{action}' on project_query."
                    )
    except Exception:
        pass

    try:
        if action == "sync":
            sync_result = sync_projects()
            return format_tool_response("project_query", payload=json.dumps(sync_result, indent=2))

        conn = get_connection()
        try:
            init_db(conn)

            if action == "get":
                target_id = (id or project_id or name or "").strip()
                if not target_id:
                    return format_tool_response("project_query", payload="", errors="Error: 'id' or 'name' is required for action='get'.")

                proj = get_project_by_id(conn, target_id)
                if not proj:
                    proj = get_project_by_name(conn, target_id)

                if not proj:
                    return format_tool_response("project_query", payload="", errors=f"Error: Project not found with ID or name '{target_id}'.")
                return format_tool_response("project_query", payload=json.dumps(proj, indent=2, ensure_ascii=False))

            elif action == "stats":
                stats = get_project_stats(conn)
                return format_tool_response("project_query", payload=json.dumps(stats, indent=2, ensure_ascii=False))

            elif action == "sql":
                if not sql:
                    return format_tool_response("project_query", payload="", errors="Error: 'sql' query parameter is required for action='sql'.")
                results, err = execute_read_sql(conn, sql, limit=limit)
                if err:
                    return format_tool_response("project_query", payload="", errors=err)
                return format_tool_response("project_query", payload=json.dumps(results, indent=2, ensure_ascii=False))

            elif action == "search":
                results = query_projects_db(
                    conn=conn,
                    status=status if status else None,
                    commitment_year=commitment_year,
                    priority=priority if priority else None,
                    min_priority=min_priority if min_priority else None,
                    category=category if category else None,
                    tags=tags,
                    search_term=query if query else None,
                    limit=limit,
                    compact=compact
                )
                payload = {
                    "count": len(results),
                    "projects": results
                }
                return format_tool_response("project_query", payload=json.dumps(payload, indent=2, ensure_ascii=False))

            else:
                return format_tool_response(
                    "project_query",
                    payload="",
                    errors=f"Error: Unknown action '{action}'. Supported actions: 'search', 'get', 'stats', 'sql', 'sync'."
                )
        finally:
            conn.close()

    except Exception as e:
        return format_tool_response("project_query", payload="", errors=f"Error in project_query: {e}")
