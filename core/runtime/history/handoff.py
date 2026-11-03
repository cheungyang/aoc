"""Hand-off capsules: the conversation a hand-off came from, built without a model.

"Ted, create a syllabus on THIS topic", typed in #pkm-wiki, means little to Ted
on its own: THIS is whatever you and William were just discussing. When the
router hands such a message to Ted in his own channel (routing.py, HANDOFF),
graphs/main appends this capsule to it: an excerpt of #pkm-wiki's
conversation, read from the session that answers there
(`channel_posts.reply_sessions`).

    <handoff_context from="wiki-gardener" channel="pkm-wiki" captured="2026-10-24 08:12" via="handoff">
    <summary>...</summary>
    <recent>
    [07:00] William (scheduled post 'wiki-ingest'): Ingested "Effective context ..."
    [08:09] You: how does it relate to the memory consolidation stuff we have?
    [08:10] William: Close cousins. Compaction trades old turns for a summary; ...
    </recent>
    <touched>
    pkm/wiki/topics/AI_Memory_and_Dreaming.md (read, 08:10)
    </touched>
    </handoff_context>

An agent can hand you over too, its reply ending with <switch to="...">
(core/prompts/templates/system/team.md). graphs/main then parks a capsule of
the conversation (core/storage/handoff_capsules.py) for the teammate's first
turn in its own channel. Its `via` is "switch", where a router hand-off's is
"handoff", and it opens with what the agent wrote inside the tag for the
teammate, if anything -- the request, the decisions made, the constraints:

    <brief from="William">
    ...
    </brief>

`summary` is the pruner's summary of older history, if the session has one.
`recent` holds the last MAX_EXCHANGES exchanges: a message of yours with the
agent's reply to it, or a post a scheduled job left in the channel. `touched`
lists the files the agent's tools named during them. The capsule is capped at
about CAPSULE_TOKENS tokens for a router hand-off, and SWITCH_CAPSULE_TOKENS
for a switch's, the teammate's only account of the work. Lines stay whole while
the capsule fits; over the cap they are shortened first (LINE_CHARS), then the
summary goes, then the oldest exchanges. The brief stays whole through all of
it. A session with nothing in it, and no brief, gives no capsule.

The source session is only read: the end of its transcript
(core/storage/sessions.py, `recent_history`), which keeps timestamps and tool
calls, and its checkpoint, for the summary. The capsule reaches the callee
once, as part of your message, in the callee's own session. Nothing about the
hand-off is written where it came from.
"""
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from core.channel.response_parser import AgentResponse
from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
from core.runtime.history.budget import estimate_message_tokens
from core.runtime.history.channel_posts import reply_sessions
from core.runtime.history.summary import SUMMARY_PREFIX, SUMMARY_SUFFIX
from core.storage.sessions import SessionStore
from core.util.local_time import get_local_now, get_local_timezone

Prompt = Union[str, list]

# Exchanges in `recent`: a message of yours with its reply, or a channel post.
MAX_EXCHANGES = 3
# The capsule's size, estimated as the pruner does (budget.py): a router
# hand-off's, and a switch's, which is all the teammate gets of the work.
CAPSULE_TOKENS = 1500
SWITCH_CAPSULE_TOKENS = 2500
# The lengths a line is cut to in characters, tried in turn while the capsule is
# over its cap. Until then lines are whole.
LINE_CHARS = (600, 300, 150)
SUMMARY_CHARS = 1200
MAX_TOUCHED = 8
# Transcript rows read from the end: room for MAX_EXCHANGES exchanges with tool calls between.
HISTORY_ROWS = 400
# A transcript row longer than this is skipped unread: in practice, a tool's
# output logged whole (one Home Assistant check ran to 150 MB). Outputs never go
# into a capsule; a tool call this large only loses its `touched` line.
ROW_BYTES = 64 * 1024
YOU = "You"

_POST = re.compile(r"^\[Posted to this channel by scheduled job '(?P<origin>[^']*)' at [^\]]*\]\s*")
_CALLER = re.compile(r"<caller>.*?</caller>\s*", re.S | re.I)
_REPLYING_TO = re.compile(r"\s*<replying_to\b[^>]*>.*?</replying_to>", re.S | re.I)
_THREAD_STARTER = re.compile(r"^\s*\[Thread starter message from .*?\"\]\s*", re.S)
# A capsule the source session got from an earlier hand-off: never nested in a new one.
_CAPSULE = re.compile(r"\s*<handoff_context\b[^>]*>.*?</handoff_context>", re.S | re.I)
# When a message was sent, as the prompt stamps it ("[Mon 17:08] "): two tries
# at the same words differ in it (`_superseded`).
_STAMP = re.compile(r"^\[[A-Za-z]{3} \d{1,2}:\d{2}\]\s*")
_TOOL = re.compile(r"^Tool (?P<tool>[^\s\[:]+)(?: \[(?P<extra>.*?)\])?:(?P<input>.*)\Z", re.S)
_SUMMARY_ITEM = re.compile(r'"(?P<action>[^"]+)" on (?P<path>[^,\]]+)|(?<![\w"])on (?P<bare>[^,\]]+)|path: (?P<keyed>[^,\]]+)')


@dataclass
class _Line:
    ts: int
    speaker: str
    text: str


@dataclass
class _Exchange:
    lines: List[_Line] = field(default_factory=list)
    tool_calls: List[Tuple[str, int]] = field(default_factory=list)  # transcript lines, parsed only if kept


def source_session(channel: Any, loader: Any = None) -> Optional[Any]:
    """The session a hand-off from `channel` came out of: the one that answers
    an untagged message there (the specialist's, or the host's own)."""
    sessions = reply_sessions(channel, loader=loader)
    return sessions[0] if sessions else None


def capsule_for(channel: Any, loader: Any = None, agent_name: Optional[str] = None, **kwargs: Any) -> str:
    """The capsule for a hand-off from `channel`, or "" if there is none or it
    can't be built: a hand-off without context still reaches its agent.
    `kwargs` go to `build_capsule`: a brief, how the user is handed over, a cap."""
    try:
        session = source_session(channel, loader=loader)
        if session is None:
            return ""
        if agent_name is None and loader is not None:
            agent_name = (loader.get_agent_config(session.agent_id) or {}).get("name")
        return build_capsule(session, agent_name=agent_name, **kwargs)
    except Exception as e:
        print(f"[handoff] Could not build a capsule for #{getattr(channel, 'name', channel)}: {e}", file=sys.stderr)
        return ""


def with_capsule(prompt: Prompt, capsule: str) -> Prompt:
    """`prompt` with the capsule after the user's own words; images and other
    parts of a multimodal prompt are kept."""
    if not capsule:
        return prompt
    if isinstance(prompt, list):
        return list(prompt) + [{"type": "text", "text": f"\n\n{capsule}"}]
    return f"{prompt}\n\n{capsule}" if prompt else capsule


def build_capsule(session: Any, *, agent_name: Optional[str] = None, brief: Optional[str] = None,
                  via: Optional[str] = None, max_tokens: Optional[int] = None, store: Any = None,
                  checkpointer: Any = None, now: Optional[datetime] = None) -> str:
    """The capsule for `session`'s conversation (see the module docstring), or "".

    `agent_name` is how the session's agent is named in it ("William"); its id
    by default. `brief` is what that agent wrote inside its <switch> for the
    teammate, and `via` how the user is being handed over ("handoff",
    "switch"), shown on the tag when given. `max_tokens` is the cap,
    CAPSULE_TOKENS by default. `store`, `checkpointer` and `now` are for tests.
    """
    name = agent_name or session.agent_id
    cap = max_tokens or CAPSULE_TOKENS
    brief = _squash(brief or "")
    store = store or SessionStore()
    rows = store.recent_history(session.session_id, limit=HISTORY_ROWS, max_bytes=ROW_BYTES)
    exchanges = _exchanges(rows, name)[-MAX_EXCHANGES:]
    summary = _summary(session, checkpointer)
    if not exchanges and not summary and not brief:
        return ""
    now = now or get_local_now()

    # Smallest change first: whole lines, then shorter ones, then no summary,
    # then fewer exchanges. The brief is in every attempt, whole.
    attempts = [(exchanges, summary, None)] + [(exchanges, summary, chars) for chars in LINE_CHARS]
    attempts.append((exchanges, "", LINE_CHARS[-1]))
    attempts += [(exchanges[-keep:], "", LINE_CHARS[-1]) for keep in range(len(exchanges) - 1, 0, -1)]
    capsule = ""
    for kept, kept_summary, chars in attempts:
        capsule = _render(session, kept, kept_summary, chars, now, brief=brief, name=name, via=via)
        if estimate_message_tokens(capsule) <= cap:
            break
    return capsule


# --- reading the transcript ---------------------------------------------------------

def _exchanges(rows: Sequence[Dict[str, Any]], agent_name: str) -> List[_Exchange]:
    """The transcript as exchanges, oldest first. The agent's last words in an
    exchange are its reply; what it said before a tool call is left out, and so
    is a message of yours that the next one repeats (`_superseded`)."""
    exchanges: List[_Exchange] = []
    current: Optional[_Exchange] = None
    for row in rows:
        role, message = row.get("from"), row.get("message")
        text = message if isinstance(message, str) else ""
        ts = int(row.get("ts") or 0)
        if role == "user":
            words = _user_words(text)
            if words:
                if _superseded(current, words):
                    exchanges.pop()  # `current` is always the last exchange
                current = _Exchange([_Line(ts, YOU, words)])
                exchanges.append(current)
        elif role == "ai":
            post = _POST.match(text)
            words = _agent_words(text[post.end():] if post else text)
            if not words:
                continue
            if post:
                exchanges.append(_Exchange([_Line(ts, f"{agent_name} (scheduled post '{post.group('origin')}')", words)]))
                current = None
                continue
            if current is None:
                current = _Exchange()
                exchanges.append(current)
            if current.lines and current.lines[-1].speaker == agent_name:
                current.lines[-1] = _Line(ts, agent_name, words)
            else:
                current.lines.append(_Line(ts, agent_name, words))
        elif role == "system" and current is not None and text.startswith("Tool ") \
                and not text.startswith("Tool Output"):
            current.tool_calls.append((text, ts))
    return [e for e in exchanges if e.lines]


def _superseded(current: Optional[_Exchange], words: str) -> bool:
    """Whether `current` is a message of yours that got no reply, and that
    `words` -- your next message -- repeats from its start. In voice, a turn
    cut off by more speech runs again with all of it ("They should" ->
    "They should ideally ..."): the first is the same request, cut short, and
    would take one of the capsule's few exchanges for nothing."""
    if current is None or current.tool_calls or len(current.lines) != 1 or current.lines[0].speaker != YOU:
        return False
    earlier = _STAMP.sub("", current.lines[0].text)
    return bool(earlier) and _STAMP.sub("", words).startswith(earlier)


def _user_words(text: str) -> str:
    text = _parts_text(text, images=True)
    for wrapper in (_CAPSULE, _REPLYING_TO, _CALLER, _THREAD_STARTER):
        text = wrapper.sub("", text)
    return _squash(text)


def _agent_words(text: str) -> str:
    """What the reader saw of a reply: no <speech>, poll markup or memory log."""
    text = _parts_text(text)
    if not text.strip():
        return ""  # a turn that only called tools ("[]")
    response = AgentResponse.from_string(text)
    words = response.text
    question = (response.poll_data or {}).get("question")
    if question:
        words = f"{words} [poll: {question}]"
    return _squash(words)


def _parts_text(text: str, images: bool = False) -> str:
    """A message the store saved as a JSON list of content parts, as text."""
    stripped = text.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return text
    try:
        parts = json.loads(stripped)
    except ValueError:
        return text  # text that only looks like a list: "[draft] ..."
    if not isinstance(parts, list):
        return text
    out = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            out.append(part.get("text") or "")
        elif isinstance(part, dict) and images and part.get("type") in ("image_url", "image"):
            out.append(" [image] ")
    return "".join(out)


def _squash(text: str) -> str:
    return " ".join((text or "").split())


def _summary(session: Any, checkpointer: Any = None) -> str:
    """The pruner's summary at the head of the session's checkpoint, or ""."""
    try:
        checkpointer = checkpointer or SqliteCheckpointer()
        found = checkpointer.get_tuple(
            {"configurable": {"thread_id": session.get_session_thread_id(), "checkpoint_ns": ""}})
    except Exception as e:
        print(f"[handoff] Could not read {session.session_id}'s checkpoint: {e}", file=sys.stderr)
        return ""
    checkpoint = (found.checkpoint if found else None) or {}
    messages = (checkpoint.get("channel_values") or {}).get("messages") or []
    first = messages[0] if messages else None
    content = getattr(first, "content", None)
    if getattr(first, "type", "") != "system" or not isinstance(content, str) or SUMMARY_PREFIX not in content:
        return ""
    return _squash(_CALLER.sub("", content.replace(SUMMARY_PREFIX, "").replace(SUMMARY_SUFFIX, "")))


# --- the files the agent touched ------------------------------------------------------

def _touched(exchanges: Sequence[_Exchange]) -> List[Tuple[str, str, int]]:
    """(path, action, when) for each file the exchanges' tool calls named,
    newest first, each path once."""
    found: Dict[str, Tuple[str, str, int]] = {}
    for exchange in exchanges:
        for line, ts in exchange.tool_calls:
            for path, action in _paths_in_call(line):
                found.pop(path, None)
                found[path] = (path, action, ts)
    return list(reversed(list(found.values())))


def _paths_in_call(line: str) -> List[Tuple[str, str]]:
    """(path, action) named by one transcript tool line: from its arguments, or
    else from the summary the log prints before them."""
    match = _TOOL.match(line)
    if not match:
        return []
    tool = match.group("tool")
    found = _paths_in_args(_parse(match.group("input")), tool)
    if not found and match.group("extra"):
        for item in _SUMMARY_ITEM.finditer(match.group("extra")):
            path = item.group("path") or item.group("bare") or item.group("keyed")
            found.append((path.strip(), item.group("action") or tool))
    return [(path, action) for path, action in found if path]


def _paths_in_args(args: Any, tool: str) -> List[Tuple[str, str]]:
    if not isinstance(args, dict):
        return []
    found: List[Tuple[str, str]] = []
    instructions = args.get("instructions")
    if isinstance(instructions, str):
        instructions = _parse(instructions)
    if isinstance(instructions, list):
        for step in instructions:
            if isinstance(step, dict) and isinstance(step.get("path"), str):
                found.append((step["path"].strip(), str(step.get("action") or tool)))
    action = str(args.get("action") or tool)
    for key in ("path", "file_path"):
        if isinstance(args.get(key), str):
            found.append((args[key].strip(), action))
    if isinstance(args.get("paths"), list):
        found += [(p.strip(), action) for p in args["paths"] if isinstance(p, str)]
    return found


def _parse(raw: str) -> Any:
    """A tool's logged arguments: JSON, or the dict's repr."""
    raw = (raw or "").strip()
    if not raw.startswith(("{", "[")):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        pass
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None


# --- writing the capsule ------------------------------------------------------------

def _render(session: Any, exchanges: Sequence[_Exchange], summary: str, line_chars: Optional[int], now: datetime,
            *, brief: str = "", name: str = "", via: Optional[str] = None) -> str:
    attrs = f'from="{session.agent_id}" channel="{session.channel_name}" captured="{now:%Y-%m-%d %H:%M}"'
    if via:
        attrs += f' via="{via}"'
    out = [f"<handoff_context {attrs}>"]
    if brief:
        out += [f'<brief from="{name or session.agent_id}">', brief, "</brief>"]
    if summary:
        out += ["<summary>", _cut(summary, SUMMARY_CHARS), "</summary>"]
    lines = [line for exchange in exchanges for line in exchange.lines]
    if lines:
        out.append("<recent>")
        out += [f"[{_stamp(line.ts, now)}] {line.speaker}: {_cut(line.text, line_chars)}" for line in lines]
        out.append("</recent>")
    touched = _touched(exchanges)[:MAX_TOUCHED]
    if touched:
        out.append("<touched>")
        out += [f"{_cut(path, line_chars)} ({action}, {_stamp(ts, now)})" for path, action, ts in touched]
        out.append("</touched>")
    out.append("</handoff_context>")
    return "\n".join(out)


def _cut(text: str, limit: Optional[int]) -> str:
    """`text`, cut to `limit` characters; whole when `limit` is None."""
    return text if limit is None or len(text) <= limit else text[:limit].rstrip() + " [...]"


def _stamp(ts: int, now: datetime) -> str:
    """'08:10' for today, else '2026-10-23 08:10', in local time."""
    when = datetime.fromtimestamp(ts, tz=get_local_timezone())
    return when.strftime("%H:%M") if when.date() == now.date() else when.strftime("%Y-%m-%d %H:%M")
