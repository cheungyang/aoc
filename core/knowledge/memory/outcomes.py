"""Outcome lines: what came of the Switchboard's routes, written to main's memory log.

Code writes one line per outcome, whether a team radio label or a route you
said was missed (core/proactive/README.md). That night, main's own dream
(scripts/dream_standup.py) consolidates the lines into main's MEMORY
(precedents) and FEEDBACK (corrections). The Switchboard runs as main, so it
reads both on every run.

A line written while main's dream is running (around 23:00) is lost with the
logs the dream restores. This is rare, and a label is still counted on the Board.
"""
from typing import Any, Dict, Optional

from core.knowledge.memory.logs import save_agent_memory_log

SWITCHBOARD = "main"
# Why a route was wrong: team radio's poll under a ❌, and `!signals <id> bad <reason>`.
REASONS = ("wrong agent", "wrong timing", "not worth it", "wrong fact")


def route_line(route: Dict[str, Any], label: str, reason: Optional[str] = None, note: Optional[str] = None) -> str:
    """The line for your label on a route: a precedent when it was right, feedback when wrong."""
    what = (f"Switchboard #{route.get('decision_id')}: \"{route.get('gist')}\" from {route.get('source_agent')} "
            f"to {route.get('to_agent')} ({route.get('verdict')}, {route.get('urgency')}"
            f"{', offer: ' + route['offer'] if route.get('offer') else ''})")
    if label == "good":
        return f"[MEMORY] {what} was right, the user said."
    why = f" Why: {reason}." if reason else ""
    said = f" They added: \"{note}\"." if note else ""
    return f"[FEEDBACK] {what} was wrong, the user said.{why}{said}"


def missed_line(text: str) -> str:
    """The line for a route you said the Switchboard should have made."""
    return f"[FEEDBACK] The Switchboard missed a route, the user said: \"{' '.join(text.split())}\"."


def record_outcome(line: str) -> Optional[str]:
    """Appends `line` to main's memory log; the log's path, or None if it couldn't be written."""
    return save_agent_memory_log(SWITCHBOARD, line)
