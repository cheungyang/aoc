import json
from typing import Optional, List
from langchain_core.tools import tool
from core.loaders.tools_loader import ToolsLoader
from core.util import format_tool_response
from core.knowledge.db import (
    init_knowledge_db,
    hybrid_search_vault,
    get_knowledge_db_path,
)
from core.knowledge.indexer import (
    generate_query_embedding,
    get_embedding_client,
)
from core.knowledge.sync import sync_knowledge


@tool
def vault_search(
    agent_id: str,
    query: str = "",
    search_type: str = "hybrid",
    category: str = "all",
    path_filter: str = "",
    limit: int = 5,
    action: str = "search"
) -> str:
    """
    Search and retrieve knowledge chunks from the Obsidian PKM vault using LanceDB Hybrid Search (Vector + BM25).

    Supported Categories:
    - 'all': Searches both personal notes (~/pkm/vault) and agent-synthesized wiki (~/pkm/wiki). Defaults to 'all'.
    - 'vault': Searches ONLY personal core notes (~/pkm/vault).
    - 'wiki': Searches ONLY agent-synthesized knowledge (~/pkm/wiki).

    Supported Actions:
    - 'search': Executes a hybrid, semantic (vector only), or keyword (BM25 only) search across the PKM vault.
        Args: 'query', 'search_type' ('hybrid'|'semantic'|'keyword'), 'category' ('all'|'vault'|'wiki'), 'path_filter', 'limit'.
    - 'sync': Triggers an immediate incremental synchronization of ~/pkm into LanceDB.

    Args:
        agent_id: The ID of the agent executing the tool.
        query: The natural language question, topic, or keyword to search for.
        search_type: Search mode ('hybrid', 'semantic', 'keyword'). Defaults to 'hybrid'.
        category: Note category filter ('all', 'vault', 'wiki'). Defaults to 'all'.
        path_filter: Optional substring to filter file paths (e.g., 'projects', 'vault/notes/').
        limit: Maximum number of results to return (defaults to 5).
        action: Action to perform ('search' or 'sync'). Defaults to 'search'.
    """
    if not agent_id:
        return format_tool_response("vault_search", payload="", errors="Error: agent_id is required.")

    tools_loader = ToolsLoader()
    # Check permissions if configured
    try:
        merged_perms = tools_loader._merge_tool_permissions(agent_id)
        if "vault_search" in merged_perms:
            perms = merged_perms["vault_search"]
            if isinstance(perms, list) and perms:
                if action not in perms and "*" not in perms:
                    return format_tool_response(
                        "vault_search",
                        payload="",
                        errors=f"Error: Agent {agent_id} does not have permission to execute action '{action}' on vault_search."
                    )
    except Exception:
        # If agent is not configured or in unit test, proceed without restrictions
        pass

    try:
        if action == "sync":
            sync_result = sync_knowledge()
            return format_tool_response("vault_search", payload=json.dumps(sync_result, indent=2))

        if not query.strip():
            return format_tool_response("vault_search", payload="", errors="Error: 'query' parameter is required for action='search'.")

        table = init_knowledge_db()
        if table.count_rows() == 0:
            return format_tool_response(
                "vault_search",
                payload="Vault index is empty. Run 'sync' action first to index the vault."
            )

        # Generate query vector if semantic or hybrid search
        query_vector = None
        if search_type.lower() in ("hybrid", "semantic", "vector"):
            client = get_embedding_client()
            query_vector = generate_query_embedding(query, client=client)

        results = hybrid_search_vault(
            table=table,
            query=query,
            query_vector=query_vector,
            limit=limit,
            category=category if category and category.lower() != "all" else None,
            path_filter=path_filter if path_filter.strip() else None,
            search_type=search_type
        )

        if not results:
            cat_note = f" in category '{category}'" if category and category.lower() != "all" else ""
            return format_tool_response(
                "vault_search",
                payload=f"No matching notes found for query: '{query}'{cat_note}"
            )

        # Format output as readable markdown with context
        output_blocks = [f"Found {len(results)} result(s) for '{query}' (Mode: {search_type}, Category: {category}):\n"]
        for idx, res in enumerate(results, 1):
            tags_str = f" `#{', #'.join(res['tags'])}`" if res.get("tags") else ""
            score_val = res.get("score")
            score_str = f" (Score: {score_val:.4f})" if isinstance(score_val, float) else ""
            cat_badge = f"[{res.get('category', 'vault').upper()}]"

            block = (
                f"### {idx}. {cat_badge} {res.get('title', 'Untitled')} ({res.get('file_path')}){score_str}\n"
                f"- **Section**: `{res.get('header_path', 'General')}`{tags_str}\n\n"
                f"{res.get('raw_content', res.get('text', '')).strip()}\n"
            )
            output_blocks.append(block)

        formatted_payload = "\n".join(output_blocks)
        return format_tool_response("vault_search", payload=formatted_payload)

    except Exception as e:
        return format_tool_response("vault_search", payload="", errors=f"Error executing vault_search: {str(e)}")
