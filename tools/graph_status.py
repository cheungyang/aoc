import os
from typing import Optional, Dict, Any, List
from langchain_core.tools import tool
from core.loaders.graphs_loader import GraphsLoader
from core.agent.job_manager import current_session_identifier, JobManager
from core.util import format_tool_response

@tool
def graph_status(graph_name: Optional[str] = None, channel: Optional[str] = None) -> str:
    """
    Retrieves the status of active or paused/interrupted subgraphs in the current conversation.
    Use this to check if a multi-agent graph (e.g. 'content_creation', 'coding') is currently 
    running or awaiting user approval/feedback in this channel.

    Args:
        graph_name: (Optional) Specific graph name to query (e.g. 'content_creation', 'coding'). 
                    If omitted, checks all registered subgraphs.
        channel: (Optional) Specific channel name. If omitted, automatically uses the current 
                 channel from context.
    """
    try:
        loader = GraphsLoader()
        active_sess = current_session_identifier.get()
        channel_name = channel or (active_sess.channel_name if active_sess else "") or ""
        job_id = (active_sess.job_id if active_sess else None) or "default"
        
        if graph_name:
            graphs_to_check = [graph_name]
        else:
            graphs_to_check = [name for name in loader.list_graph_names() if name != "main"]

        active_graphs = []
        inactive_graphs = []

        for name in graphs_to_check:
            graph_info = loader.get_graph(name)
            if not graph_info:
                continue

            graph = graph_info.get("graph")
            if graph is None and graph_info.get("create_graph") is not None:
                try:
                    graph = graph_info["create_graph"]()
                except Exception:
                    graph = None

            if not graph or not hasattr(graph, "get_state"):
                continue

            candidate_threads = []
            if active_sess:
                candidate_threads.append(active_sess.get_session_thread_id(name))
            if channel_name:
                clean_ch = channel_name.lstrip("#")
                for cand in [f"{name}:main:discord:{clean_ch}", f"{name}:{clean_ch}", f"graph:{name}:{clean_ch}"]:
                    if cand not in candidate_threads:
                        candidate_threads.append(cand)
            for default_cand in [f"{name}:default", f"graph:{name}:default"]:
                if default_cand not in candidate_threads:
                    candidate_threads.append(default_cand)

            state_found = None
            matched_thread = None

            for tid in candidate_threads:
                try:
                    cfg = {"configurable": {"thread_id": tid}}
                    snapshot = graph.get_state(cfg)
                    if snapshot and (getattr(snapshot, "next", None) or getattr(snapshot, "values", None)):
                        state_found = snapshot
                        matched_thread = tid
                        if getattr(snapshot, "next", None):
                            # Found an interrupted thread, break early
                            break
                except Exception:
                    continue

            if state_found:
                next_nodes = list(getattr(state_found, "next", []) or [])
                values = getattr(state_found, "values", {}) or {}
                
                info = {
                    "graph_name": name,
                    "thread_id": matched_thread,
                    "next_nodes": next_nodes,
                    "is_interrupted": len(next_nodes) > 0,
                    "values": values
                }
                
                if info["is_interrupted"]:
                    active_graphs.append(info)
                elif values:
                    inactive_graphs.append(info)

        # Check JobManager for any running background jobs
        running_jobs = []
        try:
            for job in JobManager().get_jobs():
                if job.status in ["running", "partial"]:
                    running_jobs.append(job)
        except Exception:
            pass

        if not active_graphs and not running_jobs:
            ch_str = f"#{channel_name}" if channel_name else "default channel"
            payload = (
                f"No active or paused subgraphs found in the current conversation context ({ch_str}).\n"
                "Messages sent by the user will be processed normally by Concierge / main orchestrator."
            )
            return format_tool_response("graph_status", payload=payload, errors="None")

        lines = ["=== Active Subgraph Status ==="]
        for ag in active_graphs:
            gname = ag["graph_name"]
            nodes = ", ".join(ag["next_nodes"])
            lines.append(f"• Active Graph: {gname}")
            lines.append(f"  - Status: Paused / Awaiting Human Feedback (Interrupted)")
            lines.append(f"  - Waiting at Node(s): [{nodes}]")
            lines.append(f"  - Thread ID: {ag['thread_id']}")    
            lines.append(
                f"  - Routing Guidance: The user's next message in this conversation will be relayed "
                f"directly to the '{gname}' graph via graph_call to resume execution."
            )

        if running_jobs:
            lines.append("\n=== Running Background Jobs ===")
            for j in running_jobs:
                lines.append(f"• Job ID: {j.job_id} | Agent: {j.agent_id} | Status: {j.status}")

        payload = "\n".join(lines)
        return format_tool_response("graph_status", payload=payload, errors="None")

    except Exception as e:
        return format_tool_response("graph_status", payload="", errors=f"Error checking graph status: {e}")
