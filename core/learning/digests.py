"""Episode digests: the turns around a friction card, cut by code, for the analyst.

The analyst never reads memory.db itself: `friction_query` (agents/agent-learner/)
hands it one card's episode as code cut it, so a 100-message session costs a few
hundred characters, not 50,000:

    from a session table   the turn the card sits in, and up to TURNS_BEFORE before
                           it: your words, each tool call with its status and first
                           error line, and the agent's last reply
    from the turn ledger   that turn and up to TURNS_BEFORE before it in its session
    from the runtime log   the logged event: the command, the refused action, the 👎
    anything else          the card alone (a route, a Switchboard decision)

Every text is clipped and has secret-shaped strings scrubbed (detectors.scrub).
What it holds is still private: your words and the agents' replies. It goes to the
analyst only, which can't send it anywhere (core/learning/boundaries.py), and into
the personal-information check (core/learning/gates.py), which keeps it out of
anything a proposal would make public.
"""
import re
from typing import Any, Dict, List, Optional, Sequence

from core.learning.detectors import clip, errors_of, first_error, scrub, your_words
from core.runtime.observability.transcript import tool_status
from core.storage.learning import LearningStore

# How many turns before the card's own a digest shows, and how much of each text.
TURNS_BEFORE = 2
YOUR_CHARS = 300
REPLY_CHARS = 400
ERROR_CHARS = 160
# The window of a session table read around a card: a turn rarely spans more.
WINDOW_BEFORE_S = 2 * 3600
WINDOW_AFTER_S = 15 * 60

_CALLER = re.compile(r"<caller>.*?</caller>\s*", re.DOTALL)
_OUTPUT = "Tool Output"
_CALL = re.compile(r"^Tool (\S+?)(?=[ :\[]|$)")


def _text(value: Any, limit: int) -> str:
    """A message as the digest shows it: no caller tag or time stamp, secrets scrubbed, clipped."""
    return clip(scrub(your_words(_CALLER.sub("", str(value or "")))), limit)


def _row_ids(source_id: str) -> List[int]:
    """The session rows a card names: "412" or "231-236"."""
    parts = [p for p in str(source_id or "").split("-") if p.strip().isdigit()]
    if len(parts) == 2:
        first, last = int(parts[0]), int(parts[1])
        return list(range(first, last + 1)) if 0 <= last - first <= 500 else [first, last]
    return [int(parts[0])] if parts else []


def _turns(rows: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """A session's rows cut into turns: each message of yours starts one."""
    turns: List[List[Dict[str, Any]]] = []
    for row in rows:
        if (row.get("entry_type") == "message" and row.get("from_role") == "user") or not turns:
            turns.append([])
        turns[-1].append(row)
    return turns


def session_turn(turn: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """One turn of a session table: your words, the tool calls, the agent's last reply."""
    you, reply, tools, pending = "", "", [], []
    for row in turn:
        if row.get("entry_type") != "message":
            continue
        role, text = row.get("from_role"), row.get("message") or ""
        if role == "user" and not you:
            you = _text(text, YOUR_CHARS)
        elif role == "system":
            if text.startswith(_OUTPUT):
                tool = pending.pop(0) if pending else ""
                status = tool_status(text)
                entry = {"tool": tool, "status": status}
                if status in ("failed", "partial"):
                    entry["error"] = clip(scrub(first_error(text) or errors_of(text)), ERROR_CHARS)
                tools.append(entry)
            else:
                call = _CALL.match(text)
                if call:
                    pending.append(call.group(1))
        elif role not in ("user", "system") and text.strip():
            reply = text
    return {"you": you, "tools": tools, "agent": _text(reply, REPLY_CHARS)}


def _from_session(store: LearningStore, card: Dict[str, Any], before: int) -> Dict[str, Any]:
    at = float(card["at"])
    rows = store.session_rows(card["source"], at - WINDOW_BEFORE_S, at + WINDOW_AFTER_S)
    wanted = set(_row_ids(card.get("source_id")))
    turns = _turns(rows)
    hit = next((i for i, turn in enumerate(turns) if wanted & {r["id"] for r in turn}), None)
    if hit is None:
        return {"turns": [], "note": "the card's rows are no longer in its session table"}
    shown = []
    for i in range(max(0, hit - before), hit + 1):
        entry = session_turn(turns[i])
        if i == hit:
            entry["card"] = True
        shown.append(entry)
    return {"turns": shown}


def _from_ledger(store: LearningStore, card: Dict[str, Any], before: int) -> Dict[str, Any]:
    head = str(card.get("source_id") or "").split(":", 1)[0]
    if not head.isdigit():
        return {"turns": [], "note": "the card names no turn"}
    rows = store.ledger_turns_around(int(head), before)
    if not rows:
        return {"turns": [], "note": "the turn is no longer in the turn ledger"}
    shown = []
    for row in rows:
        tools = [{"tool": e.get("tool", ""), "status": e.get("status", "")}
                 for e in (row.get("effects") or []) if isinstance(e, dict)]
        entry = {"surface": row.get("surface", ""), "you": _text(row.get("user_text"), YOUR_CHARS),
                 "tools": tools, "agent": _text(row.get("reply_text"), REPLY_CHARS)}
        if row["id"] == int(head):
            entry["card"] = True
        shown.append(entry)
    return {"turns": shown}


def _from_events(store: LearningStore, card: Dict[str, Any]) -> Dict[str, Any]:
    head = str(card.get("source_id") or "")
    event = store.learning_event(int(head)) if head.isdigit() else None
    if event is None:
        return {"turns": [], "note": "the event is no longer in the runtime log"}
    detail = {k: clip(scrub(str(v)), ERROR_CHARS) for k, v in (event.get("detail") or {}).items()}
    return {"turns": [], "event": {"kind": event["kind"], "name": event.get("name", ""),
                                   "channel": event.get("channel", ""), "detail": detail}}


def digest(store: LearningStore, card: Dict[str, Any], before: int = TURNS_BEFORE) -> Dict[str, Any]:
    """The card's episode (module docstring): {"card": its fields, "turns": [...]}, plus
    "event" for a logged event, and "note" when its rows can no longer be read."""
    source = str(card.get("source") or "")
    if source.startswith("ctx_"):
        body = _from_session(store, card, before)
    elif source == "turn_ledger":
        body = _from_ledger(store, card, before)
    elif source == "learning_events":
        body = _from_events(store, card)
    else:
        body = {"turns": []}
    return {"card": card_fields(card), **body}


def card_fields(card: Dict[str, Any]) -> Dict[str, Any]:
    """What the analyst sees of a card: what was found, where, and how much it cost."""
    fields = {key: card.get(key) for key in ("id", "at", "agent_id", "detector", "category", "evidence_role",
                                              "severity", "cost_turns", "cost_seconds", "session", "label")}
    fields["evidence"] = clip(scrub(str(card.get("evidence") or "")), 300)
    fields["agent_id"] = fields["agent_id"] or "?"
    return {key: value for key, value in fields.items() if value not in (None, "")}


def as_text(digests: Sequence[Dict[str, Any]]) -> str:
    """Every word in the digests, one block: what the personal-information check compares against."""
    lines: List[str] = []
    for item in digests:
        lines.append(str((item.get("card") or {}).get("evidence") or ""))
        for turn in item.get("turns") or []:
            lines.extend([str(turn.get("you") or ""), str(turn.get("agent") or "")])
            lines.extend(str(t.get("error") or "") for t in turn.get("tools") or [])
        event: Optional[Dict[str, Any]] = item.get("event")
        if event:
            lines.extend(str(v) for v in (event.get("detail") or {}).values())
    return "\n".join(line for line in lines if line)
