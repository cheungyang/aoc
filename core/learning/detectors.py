"""The friction detectors: deterministic rules over what memory.db already holds.

No model runs here. Each detector reads rows through LearningStore, applies a
fixed rule (the numbers are in core/learning/thresholds.py) and returns
FrictionEvents. `scan` runs them all over one window, and one detector that
fails never stops the others: its error comes back with the events. The
README's table lists each detector with its source and rule:

    correction          your next turn corrects the reply ("no, I meant ...")
    rephrase            your next turn says the same thing again
    abandoned_question  a reply asked you something you never answered, though you talked elsewhere
    agent_switch        you asked another agent the same thing soon after
    contract_break      the reply you saw leaked markup; a voice reply came without <speech>;
                        a Switchboard run failed
    tool_failure        a tool reported failed or partial
    tool_loop           the same call, twice or more in one turn
    round_trips         more model calls in one turn than its surface's budget
    voice_cutoff        a voice reply cut off: a barge-in, "stop", or words it didn't catch
    slow_turn           a voice reply far slower than the agent's usual
    refusal             an action the agent's grants refused; worse when it tried again
    misroute            a route you labeled "wrong agent", or "go back" in the voice room
    explicit_label      your ❌ on a route, a declined or ignored proactive post, your 👎
    interrupt           you killed a job
    unmet_follow_up     a follow-up you cancelled soon after it opened, or one that expired finding nothing
    post_edit           you edited a file the agent wrote, soon after

A card's evidence is what code saw, never a model's paraphrase: your words,
the reply's question, a tool's first error line. Its severity follows the
rubric: 1 cosmetic, 2 an extra turn or 30 s, 3 a failed task or a wrong action.
"""
import json
import os
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from core.knowledge.memory.entries import normalise
from core.learning import thresholds as T
from core.proactive.outcomes import NOT_DECLINE, declined_with
from core.prompts.system import TIME_STAMP
from core.runtime.observability.transcript import tool_status
from core.storage.learning import COMMAND, REACTION, REFUSAL, SPEECH_FALLBACK, LearningStore
from core.storage.sessions import ARCHIVED, PREFIX, sanitize_table_name

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# The closed list of categories a card may carry (core/learning/README.md).
CATEGORIES = (
    "misread_request", "wrong_or_unverified_fact", "ignored_instruction", "over_asking", "under_asking",
    "verbosity_or_format", "tool_failure", "process_inefficiency", "routing_handoff", "proactive_misfire",
    "memory_error", "safety_boundary",
)
USER, AGENT, TOOL, SYSTEM = "user", "agent", "tool", "system"

# correction: how a correction starts, and what it says anywhere. Matched on your words, lower case.
_CORRECTION_START = re.compile(
    r"^(?:no|nope|nah|wrong|incorrect|not (?:that|this|what|quite|right)|that'?s (?:not|wrong|incorrect)"
    r"|i (?:said|meant|asked|told you)|again|stop|actually|shorter|too long|too much|you (?:forgot|missed|didn'?t))\b")
_CORRECTION_ANYWHERE = re.compile(
    r"\b(?:as i (?:said|mentioned)|i (?:already|just) (?:said|told you|asked)|i told you|you forgot|you missed"
    r"|that'?s not what i|not what i (?:asked|meant|wanted)|i didn'?t ask)\b")
# ...and the ones that say you'd told it before: a rule it didn't follow.
_REPEATED_RULE = re.compile(r"\b(?:i told you|as i (?:said|mentioned)|i (?:already|just) (?:said|told you))\b")
# abandoned_question: a closing question that only offers more. Leaving it unanswered is no friction.
_OFFER = re.compile(r"^(?:want me to|would you like|shall i|should i|do you want|let me know|anything else|"
                    r"need anything|or should i|if you(?:'d)? like)\b")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")
# contract_break: system markup in a reply you saw.
_LEAKED = re.compile(r"</?(system_memory_log|dream_response|switchboard_response|speech|poll|images|videos)\b")
# tool_failure: a tool's errors, and a session row that names a call or its output.
_ERRORS = re.compile(r"<errors>(.*?)</errors>", re.DOTALL)
_OUTPUT = "Tool Output"
_CALL = re.compile(r"^Tool (\S+?)(?=[ :\[]|$)")
# tool_failure: replies that aren't a call's result (not_a_failure): a skill's text, a template's
# placeholder, and a refusal, in the tools' wording ("does not have permission", "lacks permission").
_NOT_RESULTS = {"load_skill"}
_PLACEHOLDER = re.compile(r"^\[[^\[\]]*\]$")
_REFUSED = re.compile(r"\b(?:does not have permission|lacks permission)\b", re.IGNORECASE)
# first_error: the tags a tool wraps an error in (`<instruction_error action=... path=...>`).
_WRAPPER_TAG = re.compile(r"</?[a-z_]+(?:\s[^<>]*)?>")
# voice_cutoff: how a voice turn ended (core/runtime/observability/turn_trace.py Outcome).
_CUTOFFS = {
    "cancelled": ("You cut the reply off (a barge-in, or a newer turn).", ""),
    "stop_cancel": ("You said \"stop\".", ""),
    "stt_failed": ("Your words weren't caught.", "process_inefficiency"),
}
# post_edit: the filesystem actions that change a file (tools/filesystem.py).
_WRITES = {"write", "overwrite", "append", "replace_block", "insert", "edit", "create", "move", "copy"}
_TOUCHED = re.compile(r"^(?P<path>.+) \((?P<action>[a-z_]+)\)$")
# scrub(): a secret after its name and a ':' or '=' ("api_key=...", "Authorization: Bearer ..."),
# and the shapes keys come in: provider prefixes, bearer tokens, JWTs, 32+ hex characters,
# and 40+ letters and digits with no separator (so file paths survive).
_SECRET_ASSIGNED = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd|authorization)"
    r"(\s*[:=]\s*)(?:bearer\s+)?[^\s,;'\"]{6,}")
_SECRET_SHAPED = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|ya29\.[0-9A-Za-z_-]{20,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/-]{16,}=*|[A-Fa-f0-9]{32,}|[A-Za-z0-9]{40,})")


@dataclass(frozen=True)
class FrictionEvent:
    """One friction a detector found: a friction_events row (core/storage/schemas/learning.sql)."""
    at: float
    detector: str
    source: str
    source_id: str
    agent_id: str = ""
    session: str = ""
    category: str = ""
    evidence: str = ""
    evidence_role: str = ""
    severity: int = 1
    cost_turns: Optional[int] = None
    cost_seconds: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict, hash=False)

    def row(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Scan:
    """What one scan found, and what went wrong in it."""
    events: List[FrictionEvent] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    muted: List[str] = field(default_factory=list)


# -- helpers -------------------------------------------------------------------

def scrub(text: str) -> str:
    """`text` with secret-shaped strings replaced by [secret]: keys, tokens, passwords after
    their name, and long runs of key-like characters (README: security and privacy)."""
    text = _SECRET_ASSIGNED.sub(lambda m: f"{m.group(1)}{m.group(2)}[secret]", text)
    return _SECRET_SHAPED.sub("[secret]", text)


def clip(text: Any, limit: int = T.EVIDENCE_CHARS) -> str:
    """`text` on one line, scrubbed of secrets, cut to `limit` characters."""
    text = scrub(" ".join(str(text or "").split()))
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def your_words(text: Any) -> str:
    """A turn's user text as you wrote it: the time stamp in front removed."""
    return TIME_STAMP.sub("", str(text or ""), count=1).strip()


def word_count(text: str) -> int:
    return len(normalise(text).split())


def alike(a: str, b: str) -> float:
    """How alike two texts read: difflib's ratio over their normalised forms."""
    return SequenceMatcher(None, normalise(a), normalise(b)).ratio()


def is_yours(turn: Dict[str, Any]) -> bool:
    """A turn you started: a message or a spoken turn routed from a channel, not a schedule,
    and not one another agent asked for (README: the detectors)."""
    context = turn.get("context") or {}
    return (turn.get("caller") == "main" and turn.get("tap") != "scheduled"
            and turn.get("surface") in ("text", "voice") and not context.get("called"))


def consecutive(turns: Iterable[Dict[str, Any]]) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Each two of your turns in a row, in the same session."""
    by_session: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for turn in turns:
        if is_yours(turn) and turn.get("session_id"):
            by_session[turn["session_id"]].append(turn)
    for session_turns in by_session.values():
        session_turns.sort(key=lambda t: t["id"])
        yield from zip(session_turns, session_turns[1:])


def agent_ids(repo: str = REPO) -> List[str]:
    agents_dir = os.path.join(repo, "agents")
    try:
        names = os.listdir(agents_dir)
    except OSError:
        return []
    return sorted(n for n in names if os.path.isfile(os.path.join(agents_dir, n, "agent.json")))


def table_agent(table: str, agents: Sequence[str]) -> str:
    """The agent whose session a ctx_ table holds: the longest agent id its name starts with."""
    name = table[len(PREFIX):] if table.startswith(PREFIX) else table
    name = name.split(ARCHIVED, 1)[0]
    best, best_clean = "", ""
    for agent in agents:
        clean = sanitize_table_name(agent)[len(PREFIX):]
        if (name == clean or name.startswith(clean + "_")) and len(clean) > len(best_clean):
            best, best_clean = agent, clean
    return best


def p90(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]


# -- the Board's turn ledger ------------------------------------------------------

def corrections_and_rephrases(turns: Sequence[Dict[str, Any]]) -> List[FrictionEvent]:
    """correction and rephrase: your next turn in a session, soon after the reply.
    A correction wins over a rephrase on the same pair."""
    events = []
    for before, after in consecutive(turns):
        if after["at"] - before["at"] > T.PAIR_GAP_S:
            continue
        said = your_words(after.get("user_text"))
        lower = said.lower().replace("\u2019", "'")
        asked = (before.get("reply_text") or "").rstrip().endswith("?")
        base = dict(at=after["at"], source="turn_ledger", source_id=str(after["id"]), agent_id=before.get("agent_id", ""),
                    session=after.get("session_id", ""), evidence=clip(said), evidence_role=USER, severity=2,
                    cost_turns=1, details={"reply": clip(before.get("reply_text"), 200), "turn": before["id"]})
        not_a_no = any(lower.startswith(phrase) for phrase in NOT_DECLINE)
        is_answer = asked and declined_with(said) is not None
        if not not_a_no and not is_answer and (_CORRECTION_START.search(lower) or _CORRECTION_ANYWHERE.search(lower)):
            category = "ignored_instruction" if _REPEATED_RULE.search(lower) else "misread_request"
            events.append(FrictionEvent(detector="correction", category=category, **base))
            continue
        earlier = your_words(before.get("user_text"))
        if (word_count(said) >= T.MIN_WORDS and word_count(earlier) >= T.MIN_WORDS
                and alike(earlier, said) >= T.REPHRASE_RATIO):
            events.append(FrictionEvent(detector="rephrase", category="misread_request",
                                        **{**base, "details": {**base["details"], "ratio": round(alike(earlier, said), 2)}}))
    return events


def closing_question(reply: str) -> str:
    """The reply's last sentence when it's a question, else ""."""
    sentences = [s.strip() for s in _SENTENCE.findall(reply or "") if s.strip()]
    return sentences[-1] if sentences and sentences[-1].endswith("?") else ""


def abandoned_questions(turns: Sequence[Dict[str, Any]], now: float) -> List[FrictionEvent]:
    """abandoned_question: a reply that ended asking you something (not just offering more),
    with no turn of yours in that session for ABANDON_S, though you had turns elsewhere."""
    yours = sorted((t for t in turns if is_yours(t)), key=lambda t: t["at"])
    events = []
    for i, turn in enumerate(yours):
        if turn["at"] > now - T.ABANDON_S:
            break
        question = closing_question(turn.get("reply_text") or "")
        if not question or _OFFER.search(question.lower()):
            continue
        later = [t for t in yours[i + 1:] if t["at"] <= turn["at"] + T.ABANDON_S]
        if any(t.get("session_id") == turn.get("session_id") for t in later):
            continue
        if not later:  # you talked to no one: away, not abandoning
            continue
        events.append(FrictionEvent(
            at=turn["at"], detector="abandoned_question", source="turn_ledger", source_id=str(turn["id"]),
            agent_id=turn.get("agent_id", ""), session=turn.get("session_id", ""), evidence=clip(question),
            evidence_role=AGENT, severity=1, details={"you_said": clip(your_words(turn.get("user_text")), 200)}))
    return events


def agent_switches(turns: Sequence[Dict[str, Any]]) -> List[FrictionEvent]:
    """agent_switch: the same request to another agent within SWITCH_GAP_S. The first agent's card."""
    yours = sorted((t for t in turns if is_yours(t)), key=lambda t: t["at"])
    events = []
    for i, first in enumerate(yours):
        asked = your_words(first.get("user_text"))
        if word_count(asked) < T.MIN_WORDS:
            continue
        for second in yours[i + 1:]:
            if second["at"] - first["at"] > T.SWITCH_GAP_S:
                break
            if second.get("agent_id") == first.get("agent_id"):
                continue
            again = your_words(second.get("user_text"))
            if word_count(again) >= T.MIN_WORDS and alike(asked, again) >= T.REPHRASE_RATIO:
                events.append(FrictionEvent(
                    at=second["at"], detector="agent_switch", source="turn_ledger", source_id=str(first["id"]),
                    agent_id=first.get("agent_id", ""), session=first.get("session_id", ""), evidence=clip(again),
                    evidence_role=USER, severity=2, cost_turns=1,
                    details={"to_agent": second.get("agent_id", ""), "turn": second["id"]}))
                break
    return events


def leaked_markup(turns: Sequence[Dict[str, Any]]) -> List[FrictionEvent]:
    """contract_break: system markup in a reply you saw (the ledger's reply_text is the parsed text)."""
    events = []
    for turn in turns:
        found = _LEAKED.search(turn.get("reply_text") or "")
        if found:
            events.append(FrictionEvent(
                at=turn["at"], detector="contract_break", source="turn_ledger", source_id=str(turn["id"]),
                agent_id=turn.get("agent_id", ""), session=turn.get("session_id", ""),
                category="verbosity_or_format", evidence=f"<{found.group(1)}> reached the channel",
                evidence_role=AGENT, severity=2, details={"tag": found.group(1)}))
    return events


# -- the Board's routes and decisions, follow-ups, and the logged events -------------------

def judged_routes(routes: Sequence[Dict[str, Any]]) -> List[FrictionEvent]:
    """misroute and explicit_label from your labels and the posts' outcomes. A label is the
    Switchboard's (main's) friction; a declined or ignored post, its recipient's."""
    events = []
    for route in routes:
        gist = clip(route.get("gist"), 200)
        to_agent = route.get("to_agent", "")
        if route.get("label") == "bad":
            reason = route.get("label_reason") or ""
            wrong_agent = reason == "wrong agent"
            events.append(FrictionEvent(
                at=route.get("labeled_at") or 0.0, detector="misroute" if wrong_agent else "explicit_label",
                source="routes", source_id=str(route["id"]), agent_id="main",
                category="routing_handoff" if wrong_agent else "proactive_misfire",
                evidence=clip(f"❌ {gist} → {to_agent}" + (f" ({reason})" if reason else "")), evidence_role=USER,
                severity=2, details={"to_agent": to_agent, "reason": reason, "note": route.get("label_note") or "",
                                     "decision": route.get("decision_id")}))
        elif route.get("label") is None and route.get("outcome") in ("declined", "ignored"):
            outcome = route["outcome"]
            events.append(FrictionEvent(
                at=route.get("outcome_at") or 0.0, detector="explicit_label", source="routes",
                source_id=f"{route['id']}:{outcome}", agent_id=to_agent, category="proactive_misfire",
                evidence=clip(f"You {outcome} the post: {gist}"), evidence_role=USER,
                severity=2 if outcome == "declined" else 1, details={"decision": route.get("decision_id")}))
    return events


def switchboard_errors(decisions: Sequence[Dict[str, Any]]) -> List[FrictionEvent]:
    """contract_break: a Switchboard run whose run or reply failed."""
    return [FrictionEvent(at=d["at"], detector="contract_break", source="decisions", source_id=str(d["id"]),
                          agent_id="main", category="verbosity_or_format", evidence=clip(d.get("errors"), 200),
                          evidence_role=SYSTEM, severity=1) for d in decisions]


def speech_fallbacks(events_logged: Sequence[Dict[str, Any]]) -> List[FrictionEvent]:
    """contract_break: a voice reply that came without its <speech>, so the fallback was spoken."""
    return [FrictionEvent(at=e["at"], detector="contract_break", source="learning_events", source_id=str(e["id"]),
                          agent_id=e.get("agent_id", ""), category="verbosity_or_format",
                          evidence="A voice reply came without <speech>; its first sentence was spoken instead.",
                          evidence_role=SYSTEM, severity=1) for e in events_logged]


def reactions(events_logged: Sequence[Dict[str, Any]]) -> List[FrictionEvent]:
    """explicit_label: your 👎 on an agent's message, once per message."""
    events = []
    for e in events_logged:
        detail = e.get("detail") or {}
        message = detail.get("message_id") or str(e["id"])
        events.append(FrictionEvent(
            at=e["at"], detector="explicit_label", source="learning_events", source_id=f"reaction:{message}",
            agent_id=e.get("agent_id", ""), session=e.get("session_id", ""),
            evidence=f"{e.get('name') or '👎'} on a message in #{e.get('channel') or '?'}", evidence_role=USER,
            severity=2, details={"message_id": message, "channel_id": detail.get("channel_id", "")}))
    return events


def commands(events_logged: Sequence[Dict[str, Any]]) -> List[FrictionEvent]:
    """interrupt (you killed a job) and misroute ("go back" in the voice room) from the command log."""
    events = []
    for e in events_logged:
        detail = e.get("detail") or {}
        name = e.get("name")
        if name == "kill":
            events.append(FrictionEvent(
                at=e["at"], detector="interrupt", source="learning_events", source_id=str(e["id"]),
                agent_id=e.get("agent_id", ""), session=e.get("session_id", ""),
                evidence=clip(f"!kill {detail.get('args', '')}".strip()), evidence_role=USER, severity=2))
        elif name == "go back":
            events.append(FrictionEvent(
                at=e["at"], detector="misroute", source="learning_events", source_id=str(e["id"]),
                agent_id="main", category="routing_handoff",
                evidence=clip(detail.get("heard") or "go back"), evidence_role=USER, severity=2))
    return events


def refusals(events_logged: Sequence[Dict[str, Any]]) -> List[FrictionEvent]:
    """refusal: each refused action; severity 2 when it repeats one the same agent and tool
    were refused within RETRY_GAP_S (the rule says not to retry)."""
    events = []
    last: Dict[Tuple[str, str], float] = {}
    for e in sorted(events_logged, key=lambda e: (e["at"], e["id"])):
        detail = e.get("detail") or {}
        key = (e.get("agent_id", ""), e.get("name", ""))
        retry = key in last and e["at"] - last[key] <= T.RETRY_GAP_S
        last[key] = e["at"]
        what = f"{e.get('name')}: '{detail.get('action', '')}' {detail.get('path', '')}".strip()
        events.append(FrictionEvent(
            at=e["at"], detector="refusal", source="learning_events", source_id=str(e["id"]),
            agent_id=e.get("agent_id", ""), session=e.get("session_id", ""), category="process_inefficiency",
            evidence=clip(("Refused again: " if retry else "Refused: ") + what), evidence_role=SYSTEM,
            severity=2 if retry else 1, details={"retry": retry, **detail}))
    return events


def follow_up_outcomes(follow_ups: Sequence[Dict[str, Any]]) -> List[FrictionEvent]:
    """unmet_follow_up: one you cancelled within QUICK_CANCEL_S of its opening, or a watch that
    ran to its end and found nothing."""
    events = []
    for f in follow_ups:
        closed_at, opened_at = f.get("closed_at") or 0.0, f.get("opened_at") or 0.0
        goal = clip(f.get("goal"), 200)
        if f.get("closed_by") == "you" and closed_at - opened_at <= T.QUICK_CANCEL_S:
            reason = f.get("close_reason") or ""
            events.append(FrictionEvent(
                at=closed_at, detector="unmet_follow_up", source="follow_ups", source_id=str(f["id"]),
                agent_id=f.get("agent_id", ""), session=f.get("session_id", ""), category="proactive_misfire",
                evidence=clip(f"You cancelled it within a day: {goal}" + (f" ({reason})" if reason else "")),
                evidence_role=USER, severity=2))
        elif f.get("closed_by") == "until" and f.get("kind", "watch") == "watch" and not f.get("finds"):
            events.append(FrictionEvent(
                at=closed_at, detector="unmet_follow_up", source="follow_ups", source_id=str(f["id"]),
                agent_id=f.get("agent_id", ""), session=f.get("session_id", ""),
                evidence=clip(f"Watched until {f.get('until')} and found nothing: {goal}"), evidence_role=SYSTEM,
                severity=1, details={"checks": f.get("checks", 0)}))
    return events


# -- the ctx_ session tables ---------------------------------------------------------

def errors_of(text: str) -> str:
    """The body of a tool reply's first <errors> block, stripped."""
    found = _ERRORS.search(text or "")
    return (found.group(1) if found else "").strip()


def first_error(text: str) -> str:
    """The first line of a tool reply's errors, without the tags a tool wraps it in."""
    line = next((line for line in errors_of(text).splitlines() if line.strip()), "")
    return " ".join(_WRAPPER_TAG.sub(" ", line).split())


def not_a_failure(tool: str, errors: str) -> bool:
    """A reply tool_status() calls failed that isn't a failed call: a skill's own text,
    whose output template shows an <errors> block; a template's placeholder quoted in a
    reply ("[Any errors, or 'None']"); and a refused action, which `refusal` counts."""
    return tool in _NOT_RESULTS or bool(_PLACEHOLDER.match(errors)) or bool(_REFUSED.search(errors))


def _turns_of(rows: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """A session's rows cut into turns: each `user` message starts one."""
    turns: List[List[Dict[str, Any]]] = []
    for row in rows:
        if row["entry_type"] == "message" and row.get("from_role") == "user" or not turns:
            turns.append([])
        turns[-1].append(row)
    return turns


def session_friction(table: str, agent_id: str, rows: Sequence[Dict[str, Any]]) -> List[FrictionEvent]:
    """tool_failure, tool_loop, round_trips and voice_cutoff in one session table's rows."""
    events = []
    for turn in _turns_of(rows):
        pending: List[str] = []           # the calls whose output hasn't come, in order
        calls: Dict[str, List[int]] = defaultdict(list)
        tokens = [r for r in turn if r["entry_type"] == "token"]
        for row in turn:
            text = row.get("message") or ""
            if row["entry_type"] == "message" and row.get("from_role") == "system":
                if text.startswith(_OUTPUT):
                    tool = pending.pop(0) if pending else ""
                    status = tool_status(text)
                    if status in ("failed", "partial") and not not_a_failure(tool, errors_of(text)):
                        events.append(FrictionEvent(
                            at=row["created_at"], detector="tool_failure", source=table, source_id=str(row["id"]),
                            agent_id=agent_id, session=table, category="tool_failure",
                            evidence=clip(first_error(text) or status, 200), evidence_role=TOOL,
                            severity=2 if status == "failed" else 1, details={"tool": tool, "status": status}))
                else:
                    call = _CALL.match(text)
                    if call:
                        pending.append(call.group(1))
                        calls[text[:500]].append(row["id"])
            elif row["entry_type"] == "turn_trace":
                trace = _json(text)
                outcome = trace.get("outcome")
                if outcome in _CUTOFFS:
                    said, category = _CUTOFFS[outcome]
                    events.append(FrictionEvent(
                        at=row["created_at"], detector="voice_cutoff", source=table, source_id=str(row["id"]),
                        agent_id=str(trace.get("agent") or agent_id), session=table, category=category,
                        evidence=said, evidence_role=SYSTEM, severity=2 if outcome == "stop_cancel" else 1,
                        details={"outcome": outcome}))
        for call, ids in calls.items():
            if len(ids) >= 2:
                events.append(FrictionEvent(
                    at=next(r["created_at"] for r in turn if r["id"] == ids[-1]), detector="tool_loop", source=table,
                    source_id=f"{ids[0]}-{ids[-1]}", agent_id=agent_id, session=table,
                    category="process_inefficiency", evidence=clip(call, 200), evidence_role=SYSTEM,
                    severity=2 if len(ids) >= 3 else 1, details={"times": len(ids)}))
        surface = next((r.get("surface") for r in tokens if r.get("surface")), "")
        budget = T.ROUND_TRIP_BUDGET.get(surface)
        if budget is not None and len(tokens) > budget:
            events.append(FrictionEvent(
                at=tokens[-1]["created_at"], detector="round_trips", source=table,
                source_id=f"{turn[0]['id']}-{turn[-1]['id']}", agent_id=agent_id, session=table,
                category="process_inefficiency", evidence=f"{len(tokens)} model calls in one {surface} turn "
                                                          f"(budget {budget})", evidence_role=SYSTEM,
                severity=2 if len(tokens) > 2 * budget else 1, details={"calls": len(tokens), "budget": budget}))
    return events


def slow_turns(table: str, agent_id: str, rows: Sequence[Dict[str, Any]],
               usual: Dict[str, float]) -> List[FrictionEvent]:
    """slow_turn: time to first audio above SLOW_FACTOR × the agent's usual p90."""
    events = []
    for row in rows:
        if row["entry_type"] != "turn_trace" or row.get("execution_time") is None:
            continue
        trace = _json(row.get("message"))
        who = str(trace.get("agent") or agent_id)
        limit = usual.get(who)
        took = float(row["execution_time"])
        if limit is not None and took > T.SLOW_FACTOR * limit:
            events.append(FrictionEvent(
                at=row["created_at"], detector="slow_turn", source=table, source_id=str(row["id"]), agent_id=who,
                session=table, category="process_inefficiency",
                evidence=f"{took:.1f} s to first audio (usual p90 {limit:.1f} s)", evidence_role=SYSTEM,
                severity=1, cost_seconds=round(took - limit, 1)))
    return events


def usual_ttfa(samples: Iterable[Tuple[str, float]]) -> Dict[str, float]:
    """Each agent's p90 time to first audio, for agents with SLOW_MIN_SAMPLES traces."""
    by_agent: Dict[str, List[float]] = defaultdict(list)
    for agent, seconds in samples:
        by_agent[agent].append(seconds)
    return {agent: p90(values) for agent, values in by_agent.items() if len(values) >= T.SLOW_MIN_SAMPLES}


def _json(text: Any) -> Dict[str, Any]:
    try:
        value = json.loads(text) if isinstance(text, str) and text else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# -- the vault: your edits to what agents wrote --------------------------------------

def _git(vault: str, *args: str) -> Optional[str]:
    try:
        done = subprocess.run(["git", "-C", vault, *args], capture_output=True, text=True, timeout=T.GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def post_edits(turns: Sequence[Dict[str, Any]], vault: str, now: float,
               git: Callable[..., Optional[str]] = _git) -> List[FrictionEvent]:
    """post_edit: a commit by someone other than the host's vault identity changes a file an
    agent wrote, within POST_EDIT_S, by at least POST_EDIT_MIN_LINES lines."""
    if not vault or not os.path.isdir(vault):
        return []
    host = (git(vault, "config", "user.email") or "").strip()
    if not host:
        return []  # no identity to tell the host's commits from yours
    events = []
    for turn in turns:
        for entry in (turn.get("context") or {}).get("touched") or []:
            found = _TOUCHED.match(str(entry))
            if not found or found.group("action") not in _WRITES:
                continue
            path = found.group("path")
            if os.path.isabs(path):
                continue  # outside the vault
            since, until = int(turn["at"]) + 60, int(min(turn["at"] + T.POST_EDIT_S, now))
            log = git(vault, "log", f"--since=@{since}", f"--until=@{until}", "--format=%H%x09%ae%x09%at", "--", path)
            mine = [line.split("\t") for line in (log or "").splitlines() if line.count("\t") == 2]
            yours = [(sha, int(at)) for sha, email, at in mine if email.strip() != host and at.isdigit()]
            if not yours:
                continue
            sha, at = min(yours, key=lambda c: c[1])
            stat = git(vault, "show", "--numstat", "--format=", sha, "--", path) or ""
            lines = sum(int(a) + int(d) for a, d, *_ in (row.split("\t") for row in stat.splitlines() if row.count("\t") >= 2)
                        if a.isdigit() and d.isdigit())
            if lines < T.POST_EDIT_MIN_LINES:
                continue
            hours = (at - turn["at"]) / 3600
            events.append(FrictionEvent(
                at=float(at), detector="post_edit", source="turn_ledger", source_id=f"{turn['id']}:{path}",
                agent_id=turn.get("agent_id", ""), session=turn.get("session_id", ""),
                evidence=clip(f"You changed {lines} lines of {path}, {hours:.0f} h after it was written"),
                evidence_role=USER, severity=2, details={"commit": sha, "lines": lines, "path": path}))
    return events


# -- the scan -------------------------------------------------------------------

def muted(store: LearningStore) -> List[str]:
    """Detectors whose labeled precision fell below MUTE_PRECISION after MUTE_AFTER labels."""
    return sorted(d for d, (real, labeled) in store.precision().items()
                  if labeled >= T.MUTE_AFTER and real / labeled < T.MUTE_PRECISION)


def scan(store: LearningStore, since: float, until: float, *, now: Optional[float] = None,
         agents: Optional[Sequence[str]] = None, vault: Optional[str] = None) -> Scan:
    """Every detector over [since, until). One that fails is reported in `errors`, and the
    others still run. Events of a muted detector are left out."""
    now = time.time() if now is None else now
    agents = agent_ids() if agents is None else agents
    result = Scan()

    def run(name: str, detect: Callable[[], List[FrictionEvent]]) -> None:
        try:
            result.events.extend(detect())
        except Exception as e:
            result.errors.append(f"{name}: {type(e).__name__}: {e}")

    turns = store.turns(since, until)
    run("corrections", lambda: corrections_and_rephrases(turns))
    run("abandoned_questions", lambda: abandoned_questions(turns, now))
    run("agent_switches", lambda: agent_switches(turns))
    run("leaked_markup", lambda: leaked_markup(turns))
    run("judged_routes", lambda: judged_routes(store.judged_routes(since, until)))
    run("switchboard_errors", lambda: switchboard_errors(store.decision_errors(since, until)))
    run("speech_fallbacks", lambda: speech_fallbacks(store.events(SPEECH_FALLBACK, since, until)))
    run("reactions", lambda: reactions(store.events(REACTION, since, until)))
    run("commands", lambda: commands(store.events(COMMAND, since, until)))
    run("refusals", lambda: refusals(store.events(REFUSAL, since, until)))
    run("follow_ups", lambda: follow_up_outcomes(store.closed_follow_ups(since, until)))
    if vault:
        run("post_edits", lambda: post_edits(turns, vault, now))

    try:
        usual = usual_ttfa(store.trace_times(store.session_tables(now - T.SLOW_HISTORY_S), now - T.SLOW_HISTORY_S))
    except Exception as e:
        usual = {}
        result.errors.append(f"usual_ttfa: {type(e).__name__}: {e}")
    try:
        tables = store.session_tables(since)
    except Exception as e:
        tables = []
        result.errors.append(f"session_tables: {type(e).__name__}: {e}")
    for table in tables:
        owner = table_agent(table, agents)

        def detect(table=table, owner=owner) -> List[FrictionEvent]:
            rows = store.session_rows(table, since, until)
            return session_friction(table, owner, rows) + slow_turns(table, owner, rows, usual)

        run(table, detect)

    result.muted = muted(store)
    if result.muted:
        result.events = [e for e in result.events if e.detector not in result.muted]
    return result
