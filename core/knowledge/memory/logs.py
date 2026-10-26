"""An agent's memory log: the lines its replies ask it to remember, one file per
day at pkm/agents/<agent_id>/memory_logs/YYYY-MM-DD.md. The dream
(scripts/dream_standup.py) reads them.

Like every file write an agent or a schedule starts, the append goes through the
filesystem tool, which checks that the agent may write there. The tool is found
by name through the tools loader, the way core/permissions/declarations.py reads
a tool's declarations: a package below the runtime never imports tools/.
"""
import importlib
import os
import re
from typing import Optional

from core.config.settings import Config
from core.util.local_time import get_local_now


def save_agent_memory_log(agent_id: str, log_content: str) -> Optional[str]:
    """
    Appends intercepted memory log content to <pkm_dir>/agents/<agent_id>/memory_logs/YYYY-MM-DD.md
    using the filesystem tool.
    """
    if not log_content or not log_content.strip() or not agent_id:
        return None

    try:
        filesystem = _filesystem_tool()

        pkm_dir = Config().pkm_dir
        # User's wall clock, not the container's UTC: the log file is named by
        # date, so a UTC stamp would start "tomorrow's" log mid-evening.
        now = get_local_now()
        today_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        log_file = os.path.join(pkm_dir, "agents", agent_id, "memory_logs", f"{today_str}.md")

        formatted_lines = []
        for raw_line in log_content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # Strip leading list bullets/hyphens
            line_body = re.sub(r"^[-*•]\s*", "", line)
            # Strip placeholder or prior timestamps e.g. [00:00:00], [HH:MM:SS], [12:34:56]
            line_body = re.sub(r"^\[(?:\d{2}:\d{2}:\d{2}|HH:MM:SS|--:--:--)\]\s*", "", line_body, flags=re.IGNORECASE)
            
            formatted_lines.append(f"- [{time_str}] {line_body}")

        if not formatted_lines:
            return None

        payload = "\n".join(formatted_lines) + "\n"

        # This runs after the agent's execution context has been torn down (it is reached from
        # Agent._parse_final_response), so mint one. The agent_id comes from the Agent instance,
        # not from the model, so asserting this identity is legitimate.
        from core.runtime.foundation.context import current_execution_context, try_context
        from core.runtime.foundation.sessions import SessionManager

        ctx = try_context()
        if ctx is None or ctx.agent_id != agent_id:
            ctx = SessionManager().get_session(agent_id=agent_id, source="job")

        token = current_execution_context.set(ctx)
        try:
            result = filesystem.invoke(
                {
                    "instructions": [{
                        "action": "append",
                        "path": log_file,
                        "content": payload
                    }]
                },
                config={"callbacks": []}
            )
        finally:
            current_execution_context.reset(token)

        result_str = str(result)
        errors_match = re.search(r'<errors>(.*?)</errors>', result_str, re.DOTALL)
        if errors_match:
            errors_content = errors_match.group(1).strip()
            if errors_content and errors_content != "None":
                print(f"Error persisting memory log for agent {agent_id}: {errors_content}")
                return None
        elif "Error" in result_str:
            print(f"Error persisting memory log for agent {agent_id}: {result_str}")
            return None

        return log_file
    except Exception as e:
        print(f"Warning: Failed to persist memory log for agent {agent_id}: {e}")
        return None


def _filesystem_tool():
    """The filesystem tool, found by name (see the module docstring). The loader
    is imported here, not at the top: core.loaders' __init__ loads every loader
    (~250 ms), and only a log write needs it."""
    from core.loaders.tools_loader import ToolsLoader

    return importlib.import_module(ToolsLoader().module_path("filesystem")).filesystem
