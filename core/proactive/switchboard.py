"""The Switchboard: the Concierge's model, behind the conversation.

After a turn, it reads what happened and names the teammates who could use it,
as signals with routes (core/prompts/templates/proactive/switchboard.md). It
runs as `main`, FLASH, in a stateless session that records no memory, so no
conversation sees it. Its system prompt keeps main's MEMORY and FEEDBACK,
where the dream turns your team radio corrections into rules (review record
§13.1). The run's session table is dropped afterwards; the decision keeps the
prompt, the reply and the tokens.

This module renders the prompt's parts from the Board, runs the model, and
parses the reply into proposals. What a proposal may become is decided by the
Governor (governor.py), never here.
"""
import asyncio
import html
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.knowledge.memory.entries import parse_tags
from core.knowledge.memory.store import tags_path
from core.loaders.agents_loader import AgentsLoader
from core.prompts.proactive import SwitchboardPrompt
from core.proactive.board import on_board
from core.proactive.cards import PeerCard
from core.proactive.taps import TAP_SCHEDULED, clip
from core.runtime.foundation import events
from core.runtime.foundation.sessions import SessionManager
from core.storage.sessions import SessionStore, sanitize_table_name
from core.util.local_time import get_local_timezone

AGENT = "main"
MODEL = "FLASH"
# Matching an exchange to cards needs little depth, and every run costs its
# thinking tokens: G3 weighs tokens/day against precision.
THINKING = "low"
TIMEOUT_S = 90.0

KINDS = ("situation", "artifact", "request", "event", "focus")
URGENCIES = ("now", "today", "next", "absorb")
GIST_CHARS = 160
EVIDENCE_CHARS = 300
PAYLOAD_BYTES = 4096

_BLOCK = re.compile(r"<switchboard_response>(.*?)</switchboard_response>", re.DOTALL)
_SIGNAL = re.compile(r"<signal\b([^>]*)>(.*?)</signal>", re.DOTALL)
_ROUTE = re.compile(r"<route\b([^>]*?)(?:/>|>(.*?)</route>)", re.DOTALL)
_ATTR = re.compile(r'([A-Za-z_]+)\s*=\s*"([^"]*)"')
_ERRORS = re.compile(r"<errors>(.*?)</errors>", re.DOTALL)
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ProposedRoute:
    to: str
    offer: str
    urgency: str
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    note: str = ""


@dataclass
class ProposedSignal:
    kind: str
    gist: str
    evidence: str
    tags: List[str]
    until: Optional[str]
    sensitivity: str
    payload: Dict[str, Any]
    routes: List[ProposedRoute] = field(default_factory=list)


@dataclass
class ModelRun:
    reply: str = ""
    took_ms: int = 0
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    error: str = ""


# -- the prompt ----------------------------------------------------------------------

def local(at: float) -> datetime:
    return datetime.fromtimestamp(at, get_local_timezone())


def render_exchange(turns: List[Dict[str, Any]], cards: Dict[str, PeerCard]) -> str:
    """The ledger turns as the Switchboard reads them, one block each, oldest first."""
    return "\n\n".join(render_turn(turn, cards) for turn in turns)


def render_turn(turn: Dict[str, Any], cards: Dict[str, PeerCard]) -> str:
    agent = turn["agent_id"]
    who = cards[agent].name if agent in cards else agent
    caller = turn.get("caller") or ""
    scheduled = turn.get("tap") == TAP_SCHEDULED or turn.get("surface") == "scheduled"
    header = f"[{local(turn['at']):%a %b %d %H:%M}] #{turn.get('channel') or '?'} · {who} ({agent})"
    if scheduled:
        header += " · scheduled run"
        asker = "Schedule"
    elif caller == "main":
        header += " · with the user" + (" (voice)" if turn.get("surface") == "voice" else "")
        asker = "User"
    else:
        asker = cards[caller].name if caller in cards else (caller or "Someone")
        header += f" · asked by {asker}"
    lines = [header]
    if turn.get("user_text"):
        lines.append(f"  {asker}: {turn['user_text']}")
    if turn.get("reply_text"):
        lines.append(f"  {who}: {turn['reply_text']}")
    for line in turn.get("memory") or []:
        lines.append(f"  {who} noted: {line}")
    for effect in turn.get("effects") or []:
        args = json.dumps(effect.get("args") or {}, ensure_ascii=False)
        lines.append(f"  {who} did: {effect.get('tool')}({args})")
    context = turn.get("context") or {}
    if context.get("touched"):
        lines.append(f"  files changed: {', '.join(context['touched'])}")
    if context.get("skills"):
        lines.append(f"  skills used: {', '.join(context['skills'])}")
    return "\n".join(lines)


def render_board(signals: List[Dict[str, Any]]) -> str:
    lines = []
    for signal in signals:
        routes = ", ".join(f"{r['to_agent']} {r['urgency']}" for r in signal.get("routes") or []
                           if r.get("verdict") != "drop") or "nobody"
        until = f" (until {signal['until']})" if signal.get("until") else ""
        lines.append(f"- {signal['kind']}: {signal['gist']}{until} → {routes}")
    return "\n".join(lines)


def render_tags(pkm_dir: Optional[str] = None) -> str:
    """TAGS.md's tags and what each covers, or "" when it can't be read."""
    try:
        with open(tags_path(pkm_dir), encoding="utf-8") as f:
            return "\n".join(f"- {name}: {about}" for name, about in parse_tags(f.read()))
    except Exception as e:
        print(f"[proactive] No tag list for the Switchboard: {type(e).__name__}: {e}")
        return ""


def build_prompt(turns: List[Dict[str, Any]], cards: Dict[str, PeerCard], board: List[Dict[str, Any]],
                 now: float, tags: Optional[str] = None) -> Tuple[str, str]:
    """The prompt, and the exchange it shows: the text evidence must come from."""
    exchange = render_exchange(turns, cards)
    prompt = SwitchboardPrompt(
        now=f"{local(now):%A %Y-%m-%d %H:%M}",
        teammates="\n".join(card.text() for card in cards.values()),
        tags=render_tags() if tags is None else tags,
        board=render_board(board),
        exchange=exchange,
    ).render()
    return prompt, exchange


# -- the run ---------------------------------------------------------------------------

async def run_model(prompt: str, timeout: float = TIMEOUT_S) -> ModelRun:
    """Runs `main` once on `prompt`, statelessly on MODEL at THINKING, and returns its reply.

    Nothing is posted: the session has no output. The run's tokens are read,
    then its session table is dropped (`forget`).
    """
    ctx = SessionManager.get_session(agent_id=AGENT, source="job", stateless=True, model=MODEL,
                                     thinking_level=THINKING, record_memory=False)
    started = time.monotonic()
    run = ModelRun()
    try:
        text = await asyncio.wait_for(AgentsLoader().get_agent(AGENT).execute(prompt, session=ctx), timeout)
        response = ctx.turn_hints.get(events.RESPONSE_HINT)
        if response is None:
            run.error = f"the run failed: {clip(str(text or ''), 300)}"
        else:
            run.reply = response.text or ""
    except asyncio.TimeoutError:
        run.error = f"no reply within {timeout:.0f}s"
    except Exception as e:  # still forget the session below
        run.error = f"the run failed: {type(e).__name__}: {clip(str(e), 300)}"
    run.took_ms = round((time.monotonic() - started) * 1000)
    try:
        run.tokens_in, run.tokens_out = await on_board(forget, ctx.session_id)
    except Exception as e:
        print(f"[proactive] Could not clear the Switchboard's session: {type(e).__name__}: {e}")
    return run


def forget(session_id: str, store: Optional[SessionStore] = None) -> Tuple[int, int]:
    """A finished run's input and output tokens, then its session table dropped."""
    store = store or SessionStore()
    rows = store.load_token_history(session_id, limit=0)
    tokens = (sum(r["input_token"] or 0 for r in rows), sum(r["output_token"] or 0 for r in rows))
    store.drop_table(sanitize_table_name(session_id))
    return tokens


# -- the reply ---------------------------------------------------------------------------

def parse_reply(text: str) -> Tuple[List[ProposedSignal], List[str]]:
    """The proposals in a <switchboard_response>, and what was wrong with the reply.

    A signal missing what code needs (a known kind, a gist) is left out with a
    reason. A route is kept as written; the Governor judges it.
    """
    block = _BLOCK.search(text or "")
    if block is None:
        return [], ["no <switchboard_response> in the reply"]
    body = block.group(1)
    problems, signals = [], []
    for attrs_text, inner in _SIGNAL.findall(body):
        attrs = _attrs(attrs_text)
        kind = attrs.get("kind", "").lower()
        gist = _inner(inner, "gist")
        if kind not in KINDS:
            problems.append(f"a signal of unknown kind {kind!r} was left out")
            continue
        if not gist:
            problems.append(f"a {kind} signal with no gist was left out")
            continue
        until = attrs.get("until") or None
        signals.append(ProposedSignal(
            kind=kind,
            gist=clip(gist, GIST_CHARS),
            evidence=clip(_inner(inner, "evidence"), EVIDENCE_CHARS),
            tags=[t.strip().lower() for t in attrs.get("tags", "").split(",") if t.strip()],
            until=until if until and _DAY.match(until) else None,
            sensitivity="sensitive" if attrs.get("sensitivity", "").lower() == "sensitive" else "normal",
            payload=_payload(_inner(inner, "payload")),
            routes=[_route(a, n) for a, n in _ROUTE.findall(inner)],
        ))
    stated = _ERRORS.search(body)
    if stated and stated.group(1).strip() not in ("", "None"):
        problems.append(f"the Switchboard reported: {clip(stated.group(1).strip(), 300)}")
    return signals, problems


def _attrs(text: str) -> Dict[str, str]:
    return {k.lower(): html.unescape(v).strip() for k, v in _ATTR.findall(text or "")}


def _inner(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text or "", re.DOTALL)
    return " ".join(html.unescape(match.group(1)).split()) if match else ""


def _route(attrs_text: str, note: str) -> ProposedRoute:
    attrs = _attrs(attrs_text)
    start = end = None
    window = attrs.get("window", "")
    if "/" in window:
        first, _, last = window.partition("/")
        start = first.strip() if _DAY.match(first.strip()) else None
        end = last.strip() if _DAY.match(last.strip()) else None
    elif _DAY.match(window):
        start = end = window
    return ProposedRoute(to=attrs.get("to", ""), offer=attrs.get("offer", ""),
                         urgency=attrs.get("urgency", "").lower(), window_start=start, window_end=end,
                         note=" ".join(html.unescape(note or "").split()))


def _payload(text: str) -> Dict[str, Any]:
    if not text or len(text.encode("utf-8")) > PAYLOAD_BYTES:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
