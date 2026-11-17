"""What `!learn` and the weekly digest say: plain text from memory.db, no model.

    summary_text   `!learn`: the last 7 days at a glance, the top frictions, the last
                   scan, and where Phase 0's exit criteria stand
    friction_text  `!learn friction [agent]`: the last 14 days' patterns, with counts and quotes
    cards_text     `!learn cards [n]`: cards to label, spread across detectors
    label_text     `!learn label <id>... real|not`: your verdict on cards
    ledger_text    `!learn ledger`: each agent's prompt size, and its change over 30 days
    digest_text    the weekly digest in #learning-lab (scripts/learning_digest.py)

A pattern is one agent's cards from one detector. It recurs once it has
RECURRING_CARDS cards in RECURRING_EPISODES sessions within 14 days, or at once
for a correction that says you'd told the agent before. Your labels are what
tune the detectors: one whose cards you keep marking "not" is muted
(detectors.muted), and Phase 0 ends when the labels say the detectors can be
trusted (EXIT_*).
"""
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.config.settings import Config
from core.learning import ledger as ledger_mod
from core.learning import thresholds as T
from core.learning.detectors import clip, muted
from core.storage.learning import NOT_REAL, REAL, LearningStore
from core.util.local_time import get_local_timezone

DAY = 86400
WEEK = 7 * DAY
PATTERN_S = 14 * DAY
RECURRING_CARDS = 3
RECURRING_EPISODES = 2
# Cards a digest asks you to label; `!learn cards` shows as many by default.
LABEL_ASK = 8
TOP = 5
PATTERNS_SHOWN = 8
# Phase 0's exit criteria (the proposal's roadmap).
EXIT_LABELS = 50
EXIT_PRECISION = 0.7
EXIT_PATTERNS = 5
# The ledger's alerts: growth past this share over LEDGER_MONTH_DAYS, and a drop of this
# share from one snapshot to the next.
GROWTH_ALERT = 0.10
DROP_ALERT = 0.25
LEDGER_MONTH_DAYS = 30
# How you say a card was real friction, or wasn't.
_YES = {"real", "yes", "y", "✅", "true", "1", "friction"}
_NO = {"not", "no", "n", "❌", "false", "0", "fine"}
BANNER = "⏸ LEARNING_OBSERVE is off: the hooks record nothing, and token rows get no prompt version."
HELP = ("`!learn friction [agent]` · `!learn cards [n]` · `!learn label <id>… real|not` · `!learn ledger`")


# -- formatting ------------------------------------------------------------------

def local(at: float) -> datetime:
    """`at` on your wall clock."""
    return datetime.fromtimestamp(at, get_local_timezone())


def when(at: float, now: float) -> str:
    """"Tue 09:14" within the last 6 days, else "Nov 3 09:14"."""
    moment = local(at)
    if now - at < 6 * DAY:
        return f"{moment:%a %H:%M}"
    return f"{moment:%b} {moment.day} {moment:%H:%M}"


def day_text(at: float) -> str:
    moment = local(at)
    return f"{moment:%b} {moment.day}"


def kchars(n: int) -> str:
    """2,304 -> "2.3k"; under 1,000 as is."""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def evidence(event: Dict[str, Any], limit: int = 120) -> str:
    """The card's evidence: quoted when it's your words."""
    text = clip(event.get("evidence"), limit)
    return f"\"{text}\"" if event.get("evidence_role") == "user" else text


def card_line(event: Dict[str, Any], now: float) -> str:
    """`#412 day-planner · correction · "just the top three please" · Tue 09:14`"""
    agent = event.get("agent_id") or "?"
    return f"#{event['id']} {agent} · {event['detector']} · {evidence(event)} · {when(event['at'], now)}"


def context_line(event: Dict[str, Any]) -> str:
    """What the card answered, when that helps you judge it: the reply your correction followed."""
    reply = (event.get("details") or {}).get("reply")
    if event.get("detector") in ("correction", "rephrase") and reply:
        return f"   ↳ after: \"{clip(reply, 120)}\""
    return ""


# -- patterns ------------------------------------------------------------------

def episode(event: Dict[str, Any]) -> str:
    """The session a card happened in, or the card itself when it has none (a route, a reaction)."""
    return event.get("session") or f"{event.get('source')}:{event.get('source_id')}"


@dataclass
class Pattern:
    """One agent's cards from one detector."""
    agent_id: str
    detector: str
    events: List[Dict[str, Any]]

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def episodes(self) -> int:
        return len({episode(e) for e in self.events})

    @property
    def weight(self) -> int:
        return sum(int(e.get("severity") or 1) for e in self.events)

    @property
    def turns(self) -> int:
        return sum(int(e.get("cost_turns") or 0) for e in self.events)

    @property
    def recurring(self) -> bool:
        """RECURRING_CARDS cards in RECURRING_EPISODES sessions, or a correction saying you'd
        told the agent before: your words outrank the count."""
        told = any(e["detector"] == "correction" and e.get("category") == "ignored_instruction" for e in self.events)
        return told or (self.count >= RECURRING_CARDS and self.episodes >= RECURRING_EPISODES)

    def line(self) -> str:
        """`day-planner · correction ×4 in 3 sessions · severity 8 · +4 turns · "..." (#412), ...`"""
        parts = [f"{self.agent_id or '?'} · {self.detector} ×{self.count}"
                 + (f" in {self.episodes} sessions" if self.episodes > 1 else "")]
        parts.append(f"severity {self.weight}")
        if self.turns:
            parts.append(f"+{self.turns} turns")
        newest = sorted(self.events, key=lambda e: e["at"], reverse=True)[:2]
        parts.append(", ".join(f"{evidence(e, 80)} (#{e['id']})" for e in newest))
        return " · ".join(parts)


def patterns(events: Iterable[Dict[str, Any]]) -> List[Pattern]:
    """The events grouped by agent and detector: recurring ones first, then by weight."""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        groups[(event.get("agent_id") or "", event["detector"])].append(event)
    found = [Pattern(agent, detector, items) for (agent, detector), items in groups.items()]
    return sorted(found, key=lambda p: (not p.recurring, -p.weight, -p.count, p.agent_id, p.detector))


# -- labels and exit criteria ----------------------------------------------------------

def precision_line(store: LearningStore) -> str:
    """`correction 4/5 · tool_failure 3/3 · 🔇 refusal 2/21`: real out of labeled, per detector."""
    labeled = store.precision()
    if not labeled:
        return "no labels yet"
    silenced = set(muted(store))
    items = sorted(labeled.items(), key=lambda kv: (-kv[1][1], kv[0]))
    return " · ".join(f"{'🔇 ' if d in silenced else ''}{d} {real}/{n}" for d, (real, n) in items)


def exit_line(store: LearningStore, now: float) -> str:
    """Where Phase 0's exit criteria stand: labels, precision, recurring patterns."""
    labeled = store.precision()
    real = sum(r for r, _ in labeled.values())
    count = sum(n for _, n in labeled.values())
    precision = f"precision {real / count:.2f}" if count else "no precision yet"
    recurring = sum(1 for p in patterns(store.friction(now - PATTERN_S)) if p.recurring)
    return (f"**Phase 0 exit:** {count}/{EXIT_LABELS} labeled, {precision} (needs ≥{EXIT_PRECISION}) · "
            f"{recurring}/{EXIT_PATTERNS} recurring patterns")


def parse_label(args: str) -> Tuple[List[int], Optional[str], List[str]]:
    """(`ids`, REAL / NOT_REAL / None, words not understood) from "412 #418,420 real"."""
    ids, verdict, unknown = [], None, []
    for word in (args or "").replace(",", " ").split():
        token = word.strip().lower().lstrip("#")
        if token.isdigit():
            ids.append(int(token))
        elif token in _YES:
            verdict = REAL
        elif token in _NO:
            verdict = NOT_REAL
        else:
            unknown.append(word)
    return ids, verdict, unknown


def label_text(store: LearningStore, args: str) -> str:
    """The reply to `!learn label <id>... real|not`."""
    ids, verdict, unknown = parse_label(args)
    if not ids or verdict is None or unknown:
        return "Usage: `!learn label <id>… real|not` — e.g. `!learn label 412 418 real`"
    done = [i for i in ids if store.label_friction(i, verdict)]
    missing = [i for i in ids if i not in done]
    lines = []
    if done:
        word = "real friction" if verdict == REAL else "not friction"
        lines.append(f"Labeled {', '.join(f'#{i}' for i in done)} as {word}.")
    if missing:
        lines.append(f"No card {', '.join(f'#{i}' for i in missing)}.")
    lines.append(f"Detectors (real/labeled): {precision_line(store)}")
    return "\n".join(lines)


def cards_to_label(store: LearningStore, n: int, now: float) -> List[Dict[str, Any]]:
    """Up to `n` unlabeled cards from the last 30 days, taken in turn from each detector,
    newest first, so every detector gets measured."""
    by_detector: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in store.friction(now - 30 * DAY):
        if event.get("label") is None:
            by_detector[event["detector"]].append(event)
    picked: List[Dict[str, Any]] = []
    queues = [by_detector[d] for d in sorted(by_detector, key=lambda d: -len(by_detector[d]))]
    while len(picked) < n and any(queues):
        for queue in queues:
            if queue and len(picked) < n:
                picked.append(queue.pop(0))
    return picked


def cards_text(store: LearningStore, args: str, now: float) -> str:
    """The reply to `!learn cards [n]`."""
    word = (args or "").strip()
    n = min(int(word), 25) if word.isdigit() and int(word) > 0 else LABEL_ASK
    cards = cards_to_label(store, n, now)
    if not cards:
        return "No unlabeled cards from the last 30 days."
    lines = [f"**{len(cards)} card(s) to label** — real friction, or not?"]
    for card in cards:
        lines.append(card_line(card, now))
        extra = context_line(card)
        if extra:
            lines.append(extra)
    lines.append("Reply `!learn label <id>… real` or `!learn label <id>… not`.")
    return "\n".join(lines)


# -- the ledger ------------------------------------------------------------------

def _days_ago(day: str, days: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")


def ledger_changes(store: LearningStore, now: float) -> Tuple[str, List[Dict[str, Any]], Dict[str, Dict], Dict[str, Dict], List[str]]:
    """(latest day, its rows, each agent's row about a month before, each agent's previous
    row, alerts). The month-old row is the latest on or before LEDGER_MONTH_DAYS ago, else
    the oldest kept (a young ledger compares with its first days)."""
    rows = store.ledger()
    if not rows:
        return "", [], {}, {}, []
    day = rows[0]["day"]
    cutoff = _days_ago(day, LEDGER_MONTH_DAYS)
    month_ago: Dict[str, Dict] = {}
    previous: Dict[str, Dict] = {}
    for row in store.ledger_history(_days_ago(day, LEDGER_MONTH_DAYS + 7)):  # oldest first
        if row["day"] >= day:
            continue
        agent = row["agent_id"]
        previous[agent] = row
        if row["day"] <= cutoff or agent not in month_ago:
            month_ago[agent] = row
    alerts = []
    for row in rows:
        agent, size = row["agent_id"], ledger_mod.total(row)
        old = month_ago.get(agent)
        if old and ledger_mod.total(old) and size > ledger_mod.total(old) * (1 + GROWTH_ALERT):
            grew = size / ledger_mod.total(old) - 1
            alerts.append(f"⚠️ {agent} grew {grew:+.0%} since {old['day']} ({kchars(ledger_mod.total(old))} → {kchars(size)})")
        prior = previous.get(agent)
        if prior and ledger_mod.total(prior) and size < ledger_mod.total(prior) * (1 - DROP_ALERT):
            fell = 1 - size / ledger_mod.total(prior)
            alerts.append(f"⚠️ {agent} shrank {fell:.0%} since {prior['day']} — check nothing needed was lost")
    return day, rows, month_ago, previous, alerts


def ledger_text(store: LearningStore, now: float) -> str:
    """The reply to `!learn ledger`."""
    day, rows, month_ago, _, alerts = ledger_changes(store, now)
    if not rows:
        return "No ledger yet: the nightly scan (scripts/learning_nightly.py) takes the first snapshot."
    lines = [f"🪞 **Prompt ledger** · {day} · characters (≈4 per token), biggest first"]
    for row in sorted(rows, key=lambda r: -ledger_mod.total(r)):
        memory = int(row["memory_chars"]) + int(row["feedback_chars"])
        line = (f"{row['agent_id']} {kchars(ledger_mod.total(row))} · persona {kchars(row['persona_chars'])} · "
                f"preload {kchars(row['preload_chars'])} · memory+feedback {kchars(memory)} · "
                f"{row['instructions']} instructions")
        old = month_ago.get(row["agent_id"])
        if old and ledger_mod.total(old):
            line += f" · {ledger_mod.total(row) / ledger_mod.total(old) - 1:+.0%} since {old['day']}"
        lines.append(line)
    lines.extend(alerts or ["No alerts: nothing grew past 10% in a month, or shrank by a quarter overnight."])
    return "\n".join(lines)


# -- `!learn` and the digest ----------------------------------------------------------

def scan_line(store: LearningStore, now: float) -> str:
    run = store.last_run()
    if run is None:
        return "No nightly scan has run yet (scripts/learning_nightly.py, 02:00)."
    days = max(1, round((run["until"] - run["since"]) / DAY))
    line = f"Last scan: {when(run['at'], now)}, {days} day(s) read, {run['found']} found ({run['added']} new)"
    if run.get("errors"):
        failed = len([e for e in run["errors"].split("\n") if e.strip()])
        line += f" · ⚠️ {failed} part(s) failed"
    return line


def counts_line(events: Sequence[Dict[str, Any]], key: str, limit: int = 8) -> str:
    counts: Dict[str, int] = defaultdict(int)
    for event in events:
        counts[event.get(key) or "?"] += 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return " · ".join(f"{name} {n}" for name, n in ordered[:limit])


def top(events: Sequence[Dict[str, Any]], n: int = TOP) -> List[Dict[str, Any]]:
    """The `n` worst: highest severity, newest first."""
    return sorted(events, key=lambda e: (-int(e.get("severity") or 1), -e["at"]))[:n]


def summary_text(store: LearningStore, now: float) -> str:
    """The reply to `!learn`."""
    lines = ["🪞 **Learning loop** · Phase 0: observing; no agent's behaviour changes"]
    if not Config().learning_observe:
        lines.append(BANNER)
    lines.append(scan_line(store, now))
    week = store.friction(now - WEEK)
    if week:
        lines.append(f"**Last 7 days:** {len(week)} frictions · {counts_line(week, 'detector')}")
        lines.append(f"**By agent:** {counts_line(week, 'agent_id', 6)}")
        lines.append(f"**Top {min(TOP, len(week))}**")
        lines.extend(card_line(e, now) for e in top(week))
    else:
        lines.append("No friction recorded in the last 7 days.")
    lines.append(exit_line(store, now))
    lines.append(HELP)
    return "\n".join(lines)


def friction_text(store: LearningStore, agent: str, now: float) -> str:
    """The reply to `!learn friction [agent]`."""
    events = store.friction(now - PATTERN_S, agent_id=agent or None)
    who = f" for {agent}" if agent else ""
    if not events:
        return f"No friction recorded{who} in the last 14 days."
    found = patterns(events)
    lines = [f"**Friction{who}, last 14 days:** {len(events)} cards in {len(found)} patterns "
             f"(⟳ recurring: ≥{RECURRING_CARDS} cards in ≥{RECURRING_EPISODES} sessions)"]
    lines.extend(("⟳ " if p.recurring else "· ") + p.line() for p in found[:PATTERNS_SHOWN])
    if len(found) > PATTERNS_SHOWN:
        lines.append(f"… and {len(found) - PATTERNS_SHOWN} more")
    return "\n".join(lines)


def digest_text(store: LearningStore, now: float) -> str:
    """The weekly digest (module docstring). "" when there's nothing to say."""
    week = store.friction(now - WEEK)
    before = store.friction(now - 2 * WEEK)
    _, _, _, _, alerts = ledger_changes(store, now)
    runs = store.runs(now - WEEK)
    failed = [r for r in runs if r.get("errors")]
    if not week and not alerts and not failed:
        return ""
    lines = [f"🪞 **Weekly friction digest** · {day_text(now - WEEK + 1)} – {day_text(now)}"]
    if not Config().learning_observe:
        lines.append(BANNER)
    last_week = len(before) - len(week)
    agents = len({e.get("agent_id") for e in week})
    health = f"nightly scans: {len(runs)}" + (f", {len(failed)} with errors" if failed else "")
    lines.append(f"**{len(week)} frictions** ({last_week} the week before) across {agents} agent(s) · {health}")
    recurring = [p for p in patterns(store.friction(now - PATTERN_S)) if p.recurring]
    if recurring:
        lines.append(f"**Recurring patterns** (≥{RECURRING_CARDS} cards in ≥{RECURRING_EPISODES} sessions, 14 days)")
        lines.extend("⟳ " + p.line() for p in recurring[:PATTERNS_SHOWN])
    cards = cards_to_label(store, LABEL_ASK, now)
    if cards:
        lines.append(f"**Label these {len(cards)}** — real friction, or not? `!learn label <id>… real` / `not`")
        for card in cards:
            lines.append(card_line(card, now))
            extra = context_line(card)
            if extra:
                lines.append(extra)
    lines.append(f"**Detectors** (real/labeled): {precision_line(store)}")
    lines.append("**Prompt ledger:** " + (" · ".join(alerts) if alerts else "no alerts"))
    lines.append(exit_line(store, now))
    return "\n".join(lines)


def learn_text(args: str, store: Optional[LearningStore] = None, now: Optional[float] = None) -> str:
    """The reply to `!learn <args>` (core/runtime/commands/typed/learn.py)."""
    store = store or LearningStore()
    now = time.time() if now is None else now
    words = (args or "").split(maxsplit=1)
    head = words[0].lower() if words else ""
    rest = words[1] if len(words) > 1 else ""
    if not head:
        return summary_text(store, now)
    if head == "friction":
        return friction_text(store, rest.strip(), now)
    if head == "cards":
        return cards_text(store, rest, now)
    if head == "label":
        return label_text(store, rest)
    if head == "ledger" and not rest:
        return ledger_text(store, now)
    return f"Usage: `!learn` · {HELP}"
