"""The dream proposes, code disposes.

A dream sees every entry it may act on as `<entry id="e7" tag="food">…</entry>`
and replies with operations on those ids:

    <add tag="food" until="2026-12-05">text</add>
    <confirm id="e7"/>
    <update id="e3">new text</update>
    <retire id="e9" reason="trip ended"/>
    <merge ids="e4 e9">combined text</merge>      (compaction only)

Ids are numbered while the input is built and never written anywhere. Code
validates each op on its own, routes by tag, dedups, enforces budgets, caps
Profile writes and records the signals behind tag/subscription suggestions.
"""
import datetime
import difflib
import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Set, Tuple
from xml.sax.saxutils import escape

from core.knowledge.memory import state as mstate
from core.knowledge.memory import store
from core.knowledge.memory.entries import (
    FEEDBACK, PRIVATE, PROFILE, SCOPE_DESCRIPTIONS, SCOPE_TAGS, Entry, MemoryFile, clean_text,
    new_entry, normalise,
)

STATUS_DREAMED = "Dreamed"
STATUS_EMPTY = "No new memories"

REVIEW_CAP = 5
PROFILE_WRITE_CAP = 2
NEAR_DUPLICATE_RATIO = 0.8
COMPACTION_FILL = 0.8

# A reply that echoes a template back ("[the fact]") instead of filling it in.
PLACEHOLDER = re.compile(r"^\[[^\n]*\]$|^\.\.\.$|^…$")

RESPONSE_FORMAT = """<dream_response>
  <status>Dreamed or No new memories</status>
  <ops>
    <add tag="one tag from the list" until="YYYY-MM-DD, only for time-bound facts">one-sentence fact</add>
    <confirm id="e7"/>
    <update id="e3">corrected fact</update>
    <retire id="e9" reason="why"/>
  </ops>
  <errors>None</errors>
  <learnings>one-line highlight for the standup</learnings>
</dream_response>"""

COMPACTION_FORMAT = """<dream_response>
  <status>Dreamed or No new memories</status>
  <ops>
    <merge ids="e4 e9">one sentence covering both</merge>
    <update id="e3">tighter wording</update>
    <retire id="e9" reason="why"/>
  </ops>
  <errors>None</errors>
  <learnings>one-line summary</learnings>
</dream_response>"""


@dataclass
class DreamInput:
    xml: str
    ids: Dict[str, Tuple[store.Scope, Entry]]
    reviewed: Set[str] = field(default_factory=set)


@dataclass
class Op:
    kind: str
    id: Optional[str] = None
    ids: Tuple[str, ...] = ()
    tag: Optional[str] = None
    text: Optional[str] = None
    until: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class Reply:
    status: Optional[str]
    ops: List[Op]
    learnings: str
    error: Optional[str] = None


@dataclass
class Report:
    applied: int = 0
    rejected: List[str] = field(default_factory=list)
    profile_changes: List[str] = field(default_factory=list)
    archived: Dict[str, int] = field(default_factory=dict)
    unparsed: int = 0
    changed_topics: Set[str] = field(default_factory=set)

    def summary(self) -> str:
        parts = []
        for reason, n in sorted(self.archived.items()):
            parts.append(f"{reason} {n}")
        if self.rejected:
            parts.append(f"rejected {len(self.rejected)}")
        if self.unparsed:
            parts.append(f"{self.unparsed} unreadable line(s) kept")
        return ", ".join(parts)


def find_tag(tag: str, text: str) -> Optional[str]:
    if not text:
        return None
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else None


def _attrs(raw: str) -> Dict[str, str]:
    return {k.lower(): v for k, v in re.findall(r'(\w+)\s*=\s*"([^"]*)"', raw or "")}


def _unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


def parse_reply(raw: str) -> Reply:
    """Reads a dream reply. Structure errors fail the reply; op errors fail the op."""
    if not raw or not raw.strip():
        return Reply(None, [], "", "empty response")
    body = find_tag("dream_response", raw) or raw
    errors = find_tag("errors", body)
    if errors and errors.strip().lower() != "none":
        return Reply(None, [], "", errors)
    status = find_tag("status", body)
    learnings = find_tag("learnings", body) or ""
    if not status:
        return Reply(None, [], "", "no <dream_response> in reply")
    if status.lower() == STATUS_EMPTY.lower():
        return Reply(STATUS_EMPTY, [], learnings)
    if status.lower() != STATUS_DREAMED.lower():
        return Reply(None, [], "", f"unrecognised status: {status}")
    ops_block = find_tag("ops", body)
    if ops_block is None and re.search(r"<ops\s*/>", body, re.IGNORECASE):
        ops_block = ""
    if ops_block is None:
        return Reply(None, [], "", "Dreamed without <ops>; nothing written")
    ops: List[Op] = []
    pattern = re.compile(
        r"<(add|update|merge)\b([^>]*)>(.*?)</\1\s*>|<(confirm|retire)\b([^>]*?)/?>",
        re.DOTALL | re.IGNORECASE,
    )
    for m in pattern.finditer(ops_block):
        if m.group(1):
            kind, attrs, text = m.group(1).lower(), _attrs(m.group(2)), _unescape(m.group(3).strip())
        else:
            kind, attrs, text = m.group(4).lower(), _attrs(m.group(5)), None
        ops.append(Op(
            kind=kind,
            id=attrs.get("id"),
            ids=tuple((attrs.get("ids") or "").split()),
            tag=(attrs.get("tag") or "").strip().lower() or None,
            text=text,
            until=attrs.get("until"),
            reason=attrs.get("reason"),
        ))
    return Reply(STATUS_DREAMED, ops, learnings)


def _entry_xml(entry_id: str, entry: Entry, stale: bool) -> str:
    extra = ' stale="true"' if stale else ""
    until = f' until="{entry.until.isoformat()}"' if entry.until else ""
    return f'    <entry id="{entry_id}" tag="{entry.tag}"{until}{extra}>{escape(entry.text)}</entry>'


def _number(scopes: Sequence[store.Scope], today: datetime.date, review_cap: int) -> DreamInput:
    ids: Dict[str, Tuple[store.Scope, Entry]] = {}
    rows: List[Tuple[str, Entry]] = []
    for scope in scopes:
        for entry in store.load(scope).entries:
            if entry.expired(today):
                continue
            entry_id = f"e{len(ids) + 1}"
            ids[entry_id] = (scope, entry)
            rows.append((entry_id, entry))
    stale = [(i, e) for i, e in rows if e.days_unseen(today) >= store.STALE_DAYS]
    stale.sort(key=lambda row: row[1].seen)
    reviewed = {i for i, _ in stale[:review_cap]}
    xml = "\n".join(_entry_xml(i, e, i in reviewed) for i, e in rows)
    return DreamInput(xml=xml, ids=ids, reviewed=reviewed)


def build_input(agent_id: str, tags: Sequence[Tuple[str, str]], logs: Dict[str, str],
                today: datetime.date, pkm_dir: Optional[str] = None,
                review_cap: int = REVIEW_CAP) -> DreamInput:
    """Everything a dream may act on: Profile, every topic, and the agent's own files.

    Every topic is shown, not only subscribed ones: recognising a fact that is
    already recorded is what keeps agents from adding it twice.
    """
    scopes = [store.profile_scope(pkm_dir)]
    scopes += [store.topic_scope(name, pkm_dir) for name, _ in tags]
    scopes += [store.private_scope(agent_id, PRIVATE, pkm_dir), store.private_scope(agent_id, FEEDBACK, pkm_dir)]
    numbered = _number(scopes, today, review_cap)

    tag_lines = [f'    <tag name="{name}">{escape(SCOPE_DESCRIPTIONS[name])}</tag>' for name in SCOPE_TAGS]
    tag_lines += [f'    <tag name="{name}">{escape(desc)}</tag>' for name, desc in tags]
    log_lines = [f'    <log name="{escape(name)}">\n{escape(text.strip())}\n    </log>' for name, text in logs.items()]
    numbered.xml = (
        f'<dream_input agent_id="{agent_id}" today="{today.isoformat()}">\n'
        f"  <tags>\n" + "\n".join(tag_lines) + "\n  </tags>\n"
        f"  <entries>\n{numbered.xml}\n  </entries>\n"
        f"  <memory_logs>\n" + "\n".join(log_lines) + "\n  </memory_logs>\n"
        "</dream_input>"
    )
    return numbered


def build_compaction_input(tags: Sequence[str], flagged: List[Dict], today: datetime.date,
                           pkm_dir: Optional[str] = None) -> DreamInput:
    numbered = _number([store.topic_scope(t, pkm_dir) for t in tags], today, review_cap=0)
    by_text = {(scope.tag, entry.text): i for i, (scope, entry) in numbered.ids.items()}
    pairs = []
    for item in flagged:
        ids = [by_text.get((item.get("tag"), t)) for t in item.get("texts", [])]
        if all(ids):
            pairs.append(f'    <near_duplicate ids="{" ".join(ids)}"/>')
    budgets = "\n".join(
        f'    <topic tag="{t}" budget="{store.BUDGET_TOPIC}" size="{store.load(store.topic_scope(t, pkm_dir)).size()}"/>'
        for t in tags
    )
    numbered.xml = (
        f'<compaction_input today="{today.isoformat()}">\n'
        f"  <topics>\n{budgets}\n  </topics>\n"
        f"  <entries>\n{numbered.xml}\n  </entries>\n"
        f"  <flagged>\n" + "\n".join(pairs) + "\n  </flagged>\n"
        "</compaction_input>"
    )
    return numbered


def _valid_text(text: Optional[str]) -> Optional[str]:
    text = clean_text(text or "")
    if not text or PLACEHOLDER.match(text):
        return None
    return text


def _until(value: Optional[str]) -> Optional[datetime.date]:
    try:
        return datetime.date.fromisoformat(value.strip()) if value and value.strip() else None
    except ValueError:
        return None


class _Workspace:
    """Scopes loaded fresh at apply time, tracked for commit."""

    def __init__(self):
        self.files: Dict[str, Tuple[store.Scope, MemoryFile]] = {}
        self.archived: Dict[str, Dict[str, List[Entry]]] = {}

    def get(self, scope: store.Scope) -> MemoryFile:
        if scope.path not in self.files:
            self.files[scope.path] = (scope, store.load(scope))
        return self.files[scope.path][1]

    def retire(self, scope: store.Scope, entry: Entry, reason: str) -> None:
        self.archived.setdefault(scope.path, {}).setdefault(reason, []).append(entry)


def _resolve(ws: _Workspace, dream_input: DreamInput, entry_id: Optional[str]):
    """(scope, file, index) of the entry behind an id, or an error string."""
    if not entry_id or entry_id not in dream_input.ids:
        return None, f"unknown id {entry_id!r}"
    scope, original = dream_input.ids[entry_id]
    memory = ws.get(scope)
    try:
        return (scope, memory, memory.entries.index(original)), None
    except ValueError:
        return None, f"{entry_id} changed underneath (another dream touched it)"


def apply_reply(agent_id: str, reply: Reply, dream_input: DreamInput, tags: Sequence[str],
                subscriptions: Sequence[str], today: datetime.date, state: Dict,
                pkm_dir: Optional[str] = None, compaction: bool = False) -> Report:
    report = Report()
    ws = _Workspace()
    known_tags = set(tags)
    touched: Set[str] = set()
    profile_writes = 0

    def reject(op: Op, why: str):
        report.rejected.append(f"{op.kind} {op.id or op.tag or ''}: {why}".strip())

    for op in reply.ops:
        if compaction and op.kind in ("add", "confirm"):
            reject(op, "not allowed in compaction")
            continue
        if not compaction and op.kind == "merge":
            reject(op, "merge is for compaction only")
            continue

        if op.kind == "add":
            text = _valid_text(op.text)
            if not text:
                reject(op, "empty or placeholder text")
                continue
            tag = op.tag or PRIVATE
            if tag not in known_tags and tag not in SCOPE_TAGS:
                mstate.record_event(state, mstate.UNKNOWN_TAG, agent_id, tag, today)
                tag = PRIVATE
            if tag == PROFILE:
                if profile_writes >= PROFILE_WRITE_CAP:
                    mstate.record_event(state, mstate.PROFILE_CAP, agent_id, PROFILE, today)
                    tag = PRIVATE
                else:
                    profile_writes += 1
            if tag in known_tags and tag not in subscriptions:
                mstate.record_event(state, mstate.UNSUBSCRIBED_WRITE, agent_id, tag, today)
            scope = store.scope_for(tag, agent_id, pkm_dir)
            memory = ws.get(scope)
            key = normalise(text)
            same = next((i for i, e in enumerate(memory.entries) if normalise(e.text) == key), None)
            if same is not None:
                memory.entries[same] = memory.entries[same].confirmed(today)
            else:
                for e in memory.entries:
                    if difflib.SequenceMatcher(None, normalise(e.text), key).ratio() >= NEAR_DUPLICATE_RATIO:
                        if scope.shared and scope.tag != PROFILE:
                            mstate.flag_near_duplicate(state, scope.tag, e.text, text)
                        break
                memory.entries.append(new_entry(scope.tag, text, agent_id, today, _until(op.until)))
                if tag == PROFILE:
                    report.profile_changes.append(f"PROFILE + {text}")
            touched.add(scope.path)
            report.applied += 1
            continue

        if op.kind == "merge":
            if len(op.ids) < 2:
                reject(op, "merge needs at least two ids")
                continue
            text = _valid_text(op.text)
            if not text:
                reject(op, "empty or placeholder text")
                continue
            found = []
            for entry_id in op.ids:
                hit, err = _resolve(ws, dream_input, entry_id)
                if err:
                    break
                found.append(hit)
            if len(found) != len(op.ids) or len({h[0].path for h in found}) != 1:
                reject(op, "ids unknown, changed, or in different files")
                continue
            scope, memory = found[0][0], found[0][1]
            olds = [memory.entries[i] for _, _, i in found]
            for old in olds:
                memory.entries.remove(old)
                ws.retire(scope, old, "merged")
            merged = Entry(tag=scope.tag, text=text, src=olds[0].src,
                           first=min(e.first for e in olds), seen=max(e.seen for e in olds),
                           count=sum(e.count for e in olds),
                           until=max((e.until for e in olds if e.until), default=None))
            memory.entries.append(merged)
            touched.add(scope.path)
            report.applied += 1
            continue

        hit, err = _resolve(ws, dream_input, op.id)
        if err:
            reject(op, err)
            continue
        scope, memory, index = hit
        entry = memory.entries[index]
        if op.kind == "confirm":
            memory.entries[index] = entry.confirmed(today)
        elif op.kind == "update":
            text = _valid_text(op.text)
            if not text:
                reject(op, "empty or placeholder text")
                continue
            if scope.tag == PROFILE:
                if profile_writes >= PROFILE_WRITE_CAP:
                    mstate.record_event(state, mstate.PROFILE_CAP, agent_id, PROFILE, today)
                    reject(op, "Profile write cap reached")
                    continue
                profile_writes += 1
                report.profile_changes.append(f"PROFILE ~ {text}")
            memory.entries[index] = replace(entry.confirmed(today), text=text,
                                            until=_until(op.until) or entry.until)
        elif op.kind == "retire":
            memory.entries.pop(index)
            ws.retire(scope, entry, f"retired: {clean_text(op.reason or 'no reason given')}")
            if scope.tag == PROFILE:
                report.profile_changes.append(f"PROFILE − {entry.text}")
        else:
            reject(op, "unknown operation")
            continue
        touched.add(scope.path)
        report.applied += 1

    # Silence means keep: a stale entry the dream neither confirmed nor retired
    # is treated as still true, so a weak model can't forget by omission.
    for entry_id in dream_input.reviewed:
        hit, err = _resolve(ws, dream_input, entry_id)
        if err:
            continue
        scope, memory, index = hit
        memory.entries[index] = replace(memory.entries[index], seen=today)
        touched.add(scope.path)

    for path in touched:
        scope, memory = ws.files[path]
        counts = store.commit(scope, memory, today, ws.archived.get(path))
        for reason, n in counts.items():
            key = "evicted" if reason == "budget" else reason.split(":")[0]
            report.archived[key] = report.archived.get(key, 0) + n
        report.unparsed += len(memory.unparsed)
        if scope.shared and scope.tag != PROFILE:
            report.changed_topics.add(scope.tag)
    return report


def needs_compaction(tag: str, state: Dict, pkm_dir: Optional[str] = None) -> bool:
    if any(item.get("tag") == tag for item in state.get("near_duplicates", [])):
        return True
    return store.load(store.topic_scope(tag, pkm_dir)).size() > store.BUDGET_TOPIC * COMPACTION_FILL


def clear_flags(state: Dict, tags: Sequence[str]) -> None:
    state["near_duplicates"] = [i for i in state.get("near_duplicates", []) if i.get("tag") not in set(tags)]
