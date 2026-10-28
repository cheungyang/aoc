"""The Switchboard: the Concierge's model, behind the conversation.

After a turn, it reads what happened and names the teammates who could use it,
as signals with routes (core/prompts/templates/proactive/switchboard.md). It
runs as `main`, FLASH, in a stateless session that records no memory, so no
conversation sees it. Its system prompt keeps main's MEMORY and FEEDBACK,
where the dream turns your team radio corrections into rules. The run's
session table is dropped afterwards; the decision keeps the prompt, the reply
and the tokens.

It also sees the open follow-ups, at most MAX_FOLLOW_UPS_SHOWN, and may propose
closing one, with your words as evidence, when what you said ends it: you may
tell Eric "I booked the Madrid flight" in his channel, or "@Eric …" anywhere,
and Miles' award watch is done. Words to another teammate, then: the taps skip
the Concierge's turns (taps.counts), and what it asks a teammate is its words,
not yours (taps.asked_by_agent): the exchange shows that turn as asked by main.

This module renders the prompt's parts from the Board, runs the model, and
parses the reply into proposals: a Reply. What a proposal may become is decided
in code, never here: a route by the Governor (governor.py), a close by
`governor.close_problem`, then the pipeline (pipeline.py).
"""
import asyncio
import html
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.knowledge.memory.entries import parse_tags
from core.knowledge.memory.store import tags_path
from core.loaders.agents_loader import AgentsLoader
from core.prompts.proactive import SwitchboardPrompt
from core.proactive.board import on_board
from core.proactive.cards import PeerCard
from core.proactive.taps import TAP_SCHEDULED, asked_by_agent, clip
from core.runtime.foundation import events
from core.runtime.foundation.sessions import SessionManager
from core.storage.follow_ups import FollowUp
from core.storage.sessions import SessionStore, sanitize_table_name
from core.util.local_time import get_local_timezone

AGENT = "main"
MODEL = "FLASH"
# Matching an exchange to cards needs little depth, and every run costs its
# thinking tokens: `!signals stats` weighs tokens a day against precision.
THINKING = "low"
TIMEOUT_S = 90.0

KINDS = ("situation", "artifact", "request", "event", "focus")
URGENCIES = ("now", "today", "next", "absorb")
GIST_CHARS = 160
EVIDENCE_CHARS = 300
PAYLOAD_BYTES = 4096
# The open follow-ups the prompt shows, soonest `until` first: their part is in every
# prompt while any is open, so it stays short.
MAX_FOLLOW_UPS_SHOWN = 20

_BLOCK = re.compile(r"<switchboard_response>(.*?)</switchboard_response>", re.DOTALL)
_SIGNAL = re.compile(r"<signal\b([^>]*)>(.*?)</signal>", re.DOTALL)
_ROUTE = re.compile(r"<route\b([^>]*?)(?:/>|>(.*?)</route>)", re.DOTALL)
_CLOSE = re.compile(r"<close\b([^>]*?)(?:/>|>(.*?)</close>)", re.DOTALL)
_ATTR = re.compile(r'([A-Za-z_]+)\s*=\s*"([^"]*)"')
_ERRORS = re.compile(r"<errors>(.*?)</errors>", re.DOTALL)
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^#?\s*(\d+)$")


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


@dataclass(frozen=True)
class ProposedClose:
    """A follow-up the Switchboard says your words ended: code checks it before anything
    closes (governor.close_problem)."""
    follow_up_id: int  # the follow-up, by its number
    evidence: str      # your words, verbatim, clipped to EVIDENCE_CHARS
    reason: str = ""   # why, in a few words, clipped to GIST_CHARS; "" if none was given


@dataclass(frozen=True)
class Reply:
    """What a <switchboard_response> proposed, and what was wrong with it (parse_reply)."""
    signals: Tuple[ProposedSignal, ...] = ()
    closes: Tuple[ProposedClose, ...] = ()
    problems: Tuple[str, ...] = ()  # each part left out, and any error the Switchboard reported


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
    elif caller == "main" and not asked_by_agent(turn):  # the Concierge's own calls say main too
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


def render_follow_ups(follow_ups: Sequence[FollowUp], cards: Dict[str, PeerCard]) -> str:
    """The open follow-ups the Switchboard may close, soonest `until` first, at most
    MAX_FOLLOW_UPS_SHOWN, one line each: `#12 ✈️ Miles: "<goal>" · ends early when: <close_when>`.
    An agent with no card is named by its id."""
    lines = []
    for follow_up in sorted(follow_ups, key=lambda f: (f.until, f.id))[:MAX_FOLLOW_UPS_SHOWN]:
        card = cards.get(follow_up.agent_id)
        who = " ".join(part for part in (card.emoji, card.name) if part) if card else follow_up.agent_id
        early = f" · ends early when: {follow_up.close_when}" if follow_up.close_when else ""
        lines.append(f"#{follow_up.id} {who}: \"{follow_up.goal}\"{early}")
    return "\n".join(lines)


def build_prompt(turns: List[Dict[str, Any]], cards: Dict[str, PeerCard], board: List[Dict[str, Any]],
                 now: float, tags: Optional[str] = None, follow_ups: Sequence[FollowUp] = ()) -> Tuple[str, str]:
    """The prompt, and the exchange it shows: the text evidence must come from. `follow_ups`
    are the open ones (FollowUpStore.list); their part is left out when there are none."""
    exchange = render_exchange(turns, cards)
    prompt = SwitchboardPrompt(
        now=f"{local(now):%A %Y-%m-%d %H:%M}",
        teammates="\n".join(card.text() for card in cards.values()),
        tags=render_tags() if tags is None else tags,
        board=render_board(board),
        follow_ups=render_follow_ups(follow_ups, cards),
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

def parse_reply(text: str) -> Reply:
    """The proposals in a <switchboard_response>, and what was wrong with the reply.

    A signal missing what code needs (a known kind, a gist) is left out with a
    reason. A route is kept as written; the Governor judges it. A close needs a
    follow-up's number and your words as evidence, or it's left out the same way,
    as is a second close of one follow-up. Whether a close stands is code's call
    (governor.close_problem).
    """
    block = _BLOCK.search(text or "")
    if block is None:
        return Reply(problems=("no <switchboard_response> in the reply",))
    body = block.group(1)
    problems: List[str] = []
    signals: List[ProposedSignal] = []
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
    closes = _closes(body, problems)
    stated = _ERRORS.search(body)
    if stated and stated.group(1).strip() not in ("", "None"):
        problems.append(f"the Switchboard reported: {clip(stated.group(1).strip(), 300)}")
    return Reply(tuple(signals), tuple(closes), tuple(problems))


def _closes(body: str, problems: List[str]) -> List[ProposedClose]:
    """The usable <close> elements of a reply's body, in order; why each other one was left
    out goes into `problems`."""
    closes: Dict[int, ProposedClose] = {}
    for attrs_text, inner in _CLOSE.findall(body):
        attrs = _attrs(attrs_text)
        number = _ID.match(attrs.get("follow_up", ""))
        if number is None:
            problems.append("a close with no follow-up number was left out")
            continue
        follow_up_id = int(number.group(1))
        evidence = _inner(inner, "evidence")
        if not evidence:
            problems.append(f"a close of follow-up #{follow_up_id} with no evidence was left out")
            continue
        if follow_up_id in closes:
            problems.append(f"a second close of follow-up #{follow_up_id} was left out")
            continue
        closes[follow_up_id] = ProposedClose(follow_up_id, _quote(evidence, EVIDENCE_CHARS),
                                             clip(attrs.get("reason", ""), GIST_CHARS))
    return list(closes.values())


def _quote(text: str, limit: int) -> str:
    """`text` cut to at most `limit` characters at a word, with no marker: a quote's opening
    words are still verbatim, so code can still find them."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut.rsplit(" ", 1)[0] if " " in cut else cut


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
