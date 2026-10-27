"""The taps: what the proactive team learns from each finished agent turn.

When a turn ends, `SignalHook` (a TurnHook, core/runtime/hooks.py) turns it
into one ledger entry and hands it to the proactive service
(core/proactive/service.py). The service writes it to the Board
(core/storage/signals.py) and wakes the Switchboard. Nothing here calls a
model or touches the database, and the hook runs only after the reply is out.

What a turn contributes:

  exchange  what you said and what the agent replied, for the Switchboard to
            read, so it can catch a fact the agent forgot to log
  memory    the reply's memory-log lines ([CONTEXT], [MEMORY], ...)
  effects   successful calls of the tools in the effects table (effects.py),
            with their arguments. A trigger wakes the Switchboard at once (a
            receipt saved, food logged); context does not (a review written)
  context   paths that the turn's filesystem calls changed, and the skills
            its tool calls point to. Never a trigger on their own. And your
            message that started the turn, when the surface names one
            (`source_message`): a live `now` route's reaction goes on it.

The taps hear about each tool call through the tool hooks
(core/tooling/hooks.py): `CallLog` notes every finished call in its turn's
hints, and `entry_for` reads them when the turn ends.

The tap decides when the Switchboard runs: at once for a trigger or the end of
a scheduled run, and after 90 s of quiet for a conversation (service.py).

A proactive delivery's turn (source "proactive", delivery.py) counts too. It
is read at once, with the scheduled tap, and its entry has hops = 1: the
Governor absorbs any signal it starts, so a delivery never starts another.

Which turns count: an agent's own work. These are skipped:
  - the host's routing turn, and any agent reachable everywhere
    (`channels: ["*"]`: the Concierge, the graph workers);
  - stateless workers;
  - runs that record no memory (a dream, a replay);
  - steps inside a graph run;
  - background jobs.
"""
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.config.settings import Config
from core.loaders.skills_loader import SkillsLoader
from core.proactive.effects import TRIGGER, effects_for, keeps_paths, role_of
from core.runtime.foundation.addressing import message_text
from core.runtime.hooks import Turn, TurnHook
from core.tooling.hooks import ToolCall, ToolHook

TAP_EFFECT = "effect"
TAP_SCHEDULED = "scheduled"
TAP_CHAT = "chat"

# Where CallLog notes the turn's finished tool calls, in `ctx.turn_hints`, in
# order: {"tool", "args", "status"}. Never the output.
CALLS_HINT = "proactive_calls"

# How much of what you said and of the reply the ledger keeps. The Switchboard
# reads gists, and a turn's full text stays in its own session.
TEXT_CHARS = 2000
# How much of one argument value the ledger keeps, how many list items, and
# how deep it follows nested structures.
ARG_CHARS = 200
ARG_ITEMS = 20
ARG_DEPTH = 3
MAX_EFFECTS = 12
MAX_TOUCHED = 8

# Sources whose runs are never an agent's own conversation or schedule: a
# background job (run_tool, graph_call without a caller, the coding tick), a
# graph's own steps, and the voice replay.
SKIPPED_SOURCES = frozenset({"job", "graph", "voice_replay"})
# The source of a proactive delivery's turn (delivery.py). It counts: hops = 1.
PROACTIVE_SOURCE = "proactive"
# Tools too common to tell which skill a turn used.
GENERIC_TOOLS = frozenset({"filesystem", "load_skill", "web_search", "web_fetch", "bash", "agent_call"})
# Statuses of a call that changed something (transcript.tool_status).
DONE = frozenset({"ok", "partial"})

_CALLER = re.compile(r"<caller>\s*([^<]*?)\s*</caller>")
_CAPSULE = re.compile(r"<handoff_context\b.*?</handoff_context>", re.DOTALL)
_MEMORY_LINE = re.compile(r"^\s*(?:[-*•]\s*)?(?:\[\d{2}:\d{2}:\d{2}\]\s*)?(.+?)\s*$")


@dataclass
class LedgerEntry:
    """One finished turn, as the Board's turn ledger keeps it."""
    at: float
    agent_id: str
    channel: str
    session_id: str
    source: str
    surface: str
    caller: str
    tap: str
    user_text: str = ""
    reply_text: str = ""
    memory: List[str] = field(default_factory=list)
    effects: List[Dict[str, Any]] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    hops: int = 0

    def row(self) -> Dict[str, Any]:
        return asdict(self)


def counts(turn: Turn) -> bool:
    """Whether the taps keep this turn (see the module docstring)."""
    ctx, config = turn.ctx, turn.agent_config or {}
    if ctx is None:
        return False
    channels = [str(c).lower() for c in (config.get("channels") or [])]
    if not channels or "*" in channels:
        return False
    if config.get("stateless") or ctx.is_stateless() or not ctx.record_memory:
        return False
    if ctx.graph_id or ctx.source in SKIPPED_SOURCES:
        return False
    return True


def entry_for(turn: Turn, now: Optional[float] = None) -> Optional[LedgerEntry]:
    """The ledger entry for a finished turn, or None for one the taps skip."""
    if not counts(turn):
        return None
    ctx = turn.ctx
    calls = list(ctx.turn_hints.get(CALLS_HINT) or [])
    effects, touched = effects_of(calls)
    surface = ctx.get_surface()
    proactive = ctx.source == PROACTIVE_SOURCE
    if proactive:
        tap = TAP_SCHEDULED  # read at once, like a scheduled run, whatever it called
    elif any(effect["role"] == TRIGGER for effect in effects):
        tap = TAP_EFFECT
    elif ctx.source == "scheduled" or surface == "scheduled":
        tap = TAP_SCHEDULED
    else:
        tap = TAP_CHAT
    context = {}
    if touched:
        context["touched"] = touched
    skills = skill_labels(turn.agent_config or {}, calls)
    if skills:
        context["skills"] = skills
    message = getattr(ctx.output, "source_message", None)
    if isinstance(message, str) and message:
        context["message"] = message
    return LedgerEntry(
        at=time.time() if now is None else now,
        agent_id=turn.agent_id,
        channel=ctx.channel_name or "",
        session_id=ctx.session_id,
        source=ctx.source or "",
        surface=surface or "",
        caller=caller_of(turn.prompt),
        tap=tap,
        user_text=clip(user_words(turn.prompt), TEXT_CHARS),
        reply_text=clip(reply_of(turn), TEXT_CHARS),
        memory=memory_lines(getattr(turn.response, "system_memory_log", "") or ""),
        effects=effects,
        context=context,
        hops=1 if proactive else 0,
    )


# -- the exchange ------------------------------------------------------------------

def caller_of(prompt: Any) -> str:
    """Who asked: the <caller> tag a delegation adds ("main" for a message routed from a channel)."""
    if isinstance(prompt, list):
        text = "".join(part.get("text") or "" for part in prompt
                       if isinstance(part, dict) and part.get("type") == "text")
    else:
        text = prompt if isinstance(prompt, str) else ""
    match = _CALLER.search(text)
    return match.group(1) if match else ""


def user_words(prompt: Any) -> str:
    """What was asked, without the wrappers, and without a hand-off's capsule:
    that conversation was tapped where it happened."""
    return _CAPSULE.sub("", message_text(prompt)).strip()


def reply_of(turn: Turn) -> str:
    response = turn.response
    if response is None:
        return ""
    return (getattr(response, "text", "") or getattr(response, "speech", "") or "").strip()


def memory_lines(log: str) -> List[str]:
    """The memory log's lines, without bullets or time stamps."""
    lines = []
    for raw in (log or "").splitlines():
        match = _MEMORY_LINE.match(raw)
        if match and match.group(1):
            lines.append(clip(match.group(1), 400))
    return lines


def clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + " […]"


# -- effects and context ---------------------------------------------------------

def call_actions(args: Dict[str, Any]) -> List[str]:
    """The actions a call performs: each instruction's, its op, or one unnamed action."""
    instructions = args.get("instructions")
    if isinstance(instructions, list):
        return [str(i.get("action") or "") for i in instructions if isinstance(i, dict)]
    for key in ("op", "action"):
        if isinstance(args.get(key), str):
            return [args[key]]
    return [""]


def effects_of(calls: List[Dict[str, Any]]):
    """The turn's effects (successful calls the effects table counts) and the
    paths its path-keeping calls changed, in call order."""
    effects, touched = [], []
    for call in calls:
        tool, args, status = call.get("tool") or "", call.get("args") or {}, call.get("status")
        if status not in DONE:
            continue
        actions = call_actions(args)
        role = role_of(tool, actions)
        if role is None:
            continue
        if keeps_paths(tool):
            for path in changed_paths(tool, args):
                if path not in touched:
                    touched.append(path)
            continue
        if len(effects) < MAX_EFFECTS:
            effects.append({"tool": tool, "actions": [a for a in actions if a], "role": role,
                            "status": status, "args": compact(args)})
    return effects, touched[-MAX_TOUCHED:]


def changed_paths(tool: str, args: Dict[str, Any]) -> List[str]:
    """The paths a call's changing instructions name, relative to the vault, with
    the action: "wiki/topics/Prompt_Caching.md (write)"."""
    rows = [effect for effect in effects_for(tool) if effect.paths]
    pkm = (Config().pkm_dir or "").rstrip("/") + "/"
    paths = []
    for instruction in args.get("instructions") or []:
        if not isinstance(instruction, dict):
            continue
        action = str(instruction.get("action") or "")
        if not any(effect.covers([action]) for effect in rows):
            continue
        for key in ("path", "destination"):
            path = instruction.get(key)
            if isinstance(path, str) and path:
                shown = path[len(pkm):] if pkm != "/" and path.startswith(pkm) else path
                paths.append(f"{shown} ({action})")
    return paths


def skill_labels(agent_config: Dict[str, Any], calls: List[Dict[str, Any]]) -> List[str]:
    """The skills the turn's calls point to: one it loaded, or the one skill of the
    agent's that lists a tool it called. A preloaded skill shows only this way."""
    labels = [str((c.get("args") or {}).get("skill_id")) for c in calls
              if c.get("tool") == "load_skill" and (c.get("args") or {}).get("skill_id")]
    called = {c.get("tool") for c in calls} - GENERIC_TOOLS
    if called:
        loader = SkillsLoader()
        owners: Dict[str, List[str]] = {}
        for skill_id in agent_config.get("skills") or {}:
            for tool in loader.get_skill_tools(skill_id) or {}:
                owners.setdefault(tool, []).append(skill_id)
        for tool in sorted(called):
            skills = owners.get(tool) or []
            if len(skills) == 1:
                labels.append(skills[0])
    return list(dict.fromkeys(labels))


def compact(value: Any, depth: int = 0) -> Any:
    """`value` trimmed for the ledger: long strings cut, long lists shortened, deep structures elided."""
    if isinstance(value, str):
        return clip(value, ARG_CHARS)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= ARG_DEPTH:
        return "…"
    if isinstance(value, dict):
        return {str(k): compact(v, depth + 1) for k, v in list(value.items())[:ARG_ITEMS]}
    if isinstance(value, (list, tuple)):
        items = [compact(v, depth + 1) for v in list(value)[:ARG_ITEMS]]
        if len(value) > ARG_ITEMS:
            items.append(f"… {len(value) - ARG_ITEMS} more")
        return items
    return clip(str(value), ARG_CHARS)


# -- the hooks -----------------------------------------------------------------------

class CallLog(ToolHook):
    """Notes each finished tool call in its turn's hints (CALLS_HINT), for
    `entry_for` to read when the turn ends.

    A tool hook (core/tooling/hooks.py): it runs inline, after the call's reply,
    so it only appends. The arguments are kept only where the taps read them:
    calls of tools in the effects table, and load_skill's skill.
    """

    def called(self, call: ToolCall) -> None:
        hints = getattr(call.session, "turn_hints", None)
        if not isinstance(hints, dict):
            return
        args = call.args if (effects_for(call.tool) or call.tool == "load_skill") else {}
        hints.setdefault(CALLS_HINT, []).append({"tool": call.tool, "args": args, "status": call.status})


class SignalHook(TurnHook):
    """Hands each finished turn's ledger entry to `sink` (ProactiveService.turn_ended).

    It works in `ended`, which runs however the turn ended, after the reply is
    out: building the entry reads only what the turn left in memory, and the
    sink must not block. A turn the taps skip costs a few attribute reads.
    """

    def __init__(self, sink: Callable[[LedgerEntry], None]):
        self.sink = sink

    def ended(self, turn: Turn) -> None:
        try:
            entry = entry_for(turn)
            if entry is not None:
                self.sink(entry)
        except Exception as e:
            print(f"[proactive] Could not tap {turn.agent_id}'s turn: {type(e).__name__}: {e}")
