"""`!signals`: what the proactive team decided, and your labels on it.

    !signals                          the last 10 decisions
    !signals <id>                     one decision's trace: what it saw, its signals,
                                      each route and the Governor's verdict, how its
                                      delivery went and what came of it, your labels
    !signals <id> good                its routes were right
    !signals <id> bad [why] [— note]  its routes were wrong; why is one of wrong agent,
                                      wrong timing, not worth it, wrong fact
    !signals missed <what>            a route the Switchboard should have made
    !signals missed                   the ones you reported
    !signals stats                    the last 14 days: decisions, verdicts, the
                                      precision of your labels, tokens, each tap's
                                      delay, when deliveries would land, and for G4,
                                      what came of the posts and how fast they came
    !signals why <message link>       the decision behind a team radio line, or
                                      behind a proactive post

Read from the Board (core/storage/signals.py), with no model. A label here is
the same as a ✅/❌ on team radio: it counts toward acceptance at once, in
place of the route's outcome, and it writes a line in main's memory log
(core/knowledge/memory/outcomes.py). Dropped routes were never offered, so
they take no label.
"""
import asyncio
import re
import time
from datetime import datetime
from statistics import median
from typing import Any, Dict, List, Optional

from core.knowledge.memory.outcomes import REASONS, missed_line, record_outcome, route_line
from core.runtime.commands.base import ChannelCommand, TypedContext
from core.storage.signals import (
    APPROVED,
    DECLINED,
    DUPLICATE_S,
    ENGAGED,
    HANDED,
    IGNORED,
    OUTCOMES,
    RUNNING,
    SignalStore,
)
from core.util.local_time import get_local_timezone

RECENT = 10
STATS_DAYS = 14
PARTS_OF_DAY = ("night", "morning", "afternoon", "evening")
# The urgencies a route is delivered at, soonest first (core/proactive/governor.py).
URGENCIES = ("now", "today", "next")
# G3: at least this many labels, and at least this share of them right.
LABELS_NEEDED = 30
TARGET_PRECISION = 0.8
# G4: at least this share of the posts engaged, at most this many declines per agent a
# week, and a `now` route's reaction and post at most this long after the turn (p95).
TARGET_ENGAGED = 0.5
DECLINES_A_WEEK = 1
REACTION_P95_S = 10
POST_P95_S = 60
_MARK = {"good": "✅", "bad": "❌"}
_LINK_ID = re.compile(r"(\d{15,25})\D*$")


def _urgency_order(urgency: str) -> int:
    return URGENCIES.index(urgency) if urgency in URGENCIES else len(URGENCIES)


def local(at: float) -> datetime:
    """`at` on your wall clock, not the container's."""
    return datetime.fromtimestamp(at, get_local_timezone())


def when(at: Optional[float]) -> str:
    return local(at).strftime("%m-%d %H:%M") if at else "?"


def span(seconds: float) -> str:
    """'40 s', '35 min', '2.5 h'."""
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def part_of_day(at: float) -> str:
    hour = local(at).hour
    if hour >= 22 or hour < 7:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def where(session_id: str) -> str:
    """`day-planner:tool:day-planning` -> "#day-planning (day-planner)"."""
    parts = (session_id or "").split(":")
    return f"#{parts[-1]} ({parts[0]})" if len(parts) >= 3 else (session_id or "?")


def route_summary(route: Dict[str, Any]) -> str:
    verdict = route["verdict"]
    shown = "dropped" if verdict == "drop" else ("absorb" if verdict == "absorb" else route["urgency"])
    return f"{route['to_agent']} {shown}{' ' + _MARK[route['label']] if route.get('label') in _MARK else ''}"


def recent_text(store: SignalStore, limit: int = RECENT) -> str:
    decisions = store.recent_decisions(limit)
    if not decisions:
        return "No decisions yet. They start after the next agent turn."
    lines = [f"**Last {len(decisions)} decision(s)**, newest first"]
    for d in decisions:
        if d.get("errors") and not d["routes"]:
            outcome = f"failed: {d['errors'][:120]}"
        else:
            outcome = ", ".join(route_summary(r) for r in d["routes"]) or "nothing to route"
        lines.append(f"`#{d['id']}` {when(d['at'])} · {d['tap']} in {where(d['session_id'])} · {outcome}")
    lines.append("`!signals <id>` shows one; react ✅/❌ on team radio, or `!signals <id> bad <why>`.")
    return "\n".join(lines)


def delivery_text(delivery: Dict[str, Any], route: Dict[str, Any], decided_at: float) -> str:
    """How a route's delivery went, timed from its decision, and what came of the post:
    "posted +31 s · engaged". A `next` route's row is its scheduled run's."""
    def after(at: Optional[float]) -> str:
        return f" +{span(max(0.0, at - decided_at))}" if at else ""

    status = delivery["status"]
    if status == APPROVED:
        text = f"approved, due {when(delivery['deliver_at'])}"
    elif status == HANDED:
        text = f"handed to a scheduled run{after(delivery.get('claimed_at'))}"
    elif status == RUNNING:
        text = f"running{after(delivery.get('claimed_at'))}"
    else:
        run = " on a scheduled run" if route.get("urgency") == "next" else ""
        text = f"{status}{run}{after(delivery.get('finished_at'))}"
        if delivery.get("error"):
            text += f" ({delivery['error']})"
    if route.get("outcome"):
        text += f" · {route['outcome']}"
    return text


def trace_text(store: SignalStore, decision_id: int) -> str:
    d = store.decision(decision_id)
    if d is None:
        return f"No decision #{decision_id}."
    took = f"{d['took_ms'] / 1000:.1f} s" if d.get("took_ms") is not None else "?"
    tokens = f"{d['tokens_in'] or 0:,} in / {d['tokens_out'] or 0:,} out" if d.get("tokens_in") is not None else "?"
    delay = f"{d['at'] - d['first_turn_at']:.0f} s after the turn" if d.get("first_turn_at") else ""
    lines = [f"**#{d['id']}** · {when(d['at'])} · {d['mode']} · tap: {d['tap']} in {where(d['session_id'])}"
             f" · {delay} · Switchboard {took}, {tokens} tokens"]
    for turn in d["turns"]:
        lines.append(f"Saw: {turn['agent_id']} at {when(turn['at'])}"
                     f"{' (scheduled)' if turn['tap'] == 'scheduled' else ''}")
        if turn.get("user_text"):
            lines.append(f"  asked: {turn['user_text'][:300]}")
        if turn.get("reply_text"):
            lines.append(f"  replied: {turn['reply_text'][:300]}")
        for effect in turn.get("effects") or []:
            lines.append(f"  did: {effect.get('tool')} ({effect.get('role')})")
    if not d["signals"]:
        lines.append("Signals: none" + (f" · {d['errors']}" if d.get("errors") else ""))
    for signal in d["signals"]:
        evidence = "evidence ✓" if signal["evidence_found"] else "evidence ✗"
        until = f" · until {signal['until']}" if signal.get("until") else ""
        lines.append(f"Signal: {signal['kind']} · \"{signal['gist']}\" · {evidence} · tags {signal['tags'] or '-'}{until}")
        for route in [r for r in d["routes"] if r["signal_id"] == signal["id"]]:
            verdict = route["verdict"] if route["verdict"] != "deliver" else f"deliver {route['urgency']}"
            asked = f" (asked {route['asked']})" if route["asked"] != route["urgency"] else ""
            reason = f": {route['reason']}" if route.get("reason") else ""
            delivery = store.delivery(route["id"]) if route["verdict"] == "deliver" else None
            delivered = f" · {delivery_text(delivery, route, d['at'])}" if delivery else ""
            label = ""
            if route.get("label") in _MARK:
                why = f", {route['label_reason']}" if route.get("label_reason") else ""
                label = f" · you: {_MARK[route['label']]}{why}"
            lines.append(f"  → {route['to_agent']} · \"{route['offer']}\" · {verdict}{asked}{reason}{delivered}{label}")
    if d.get("errors") and d["signals"]:
        lines.append(f"Problems: {d['errors']}")
    return "\n".join(lines)


def parse_why(text: str):
    """("wrong timing", "he knew") from "wrong timing — he knew": a known reason, and the note after it."""
    rest = " ".join((text or "").split())
    for reason in REASONS:
        if rest.lower().startswith(reason):
            return reason, rest[len(reason):].strip(" -—–:,") or None
    return None, rest.strip(" -—–:,") or None


def label_text(store: SignalStore, decision_id: int, label: str, why: str = "") -> str:
    d = store.decision(decision_id)
    if d is None:
        return f"No decision #{decision_id}."
    offered = [r for r in d["routes"] if r["verdict"] != "drop"]
    if not offered:
        return (f"No route of #{decision_id} went anywhere, so there's nothing to label. If one should have, "
                "say `!signals missed <what>`.")
    reason, note = parse_why(why) if label == "bad" else (None, None)
    store.label_routes([r["id"] for r in offered], label, reason=reason, note=note)
    for route in offered:
        record_outcome(route_line(dict(route, decision_id=decision_id), label, reason, note))
    reply = f"{_MARK[label]} #{decision_id}: {len(offered)} route(s) labeled {label}."
    if label == "bad" and reason is None:
        reply += f" Why? Add one of: {', '.join(REASONS)}, e.g. `!signals {decision_id} bad wrong timing`."
    return reply


def missed_text(store: SignalStore, what: str) -> str:
    what = " ".join((what or "").split())
    if not what:
        recent = store.missed(RECENT)
        if not recent:
            return ("No missed routes yet. Say what should have been routed: "
                    "`!signals missed Ramsay should have known I ate out tonight`.")
        return "\n".join([f"**Missed routes you reported**, latest {len(recent)}"]
                         + [f"{when(m['at'])} · {m['text']}" for m in recent])
    store.add_missed(what)
    record_outcome(missed_line(what))
    return "Noted as a missed route. It counts toward G3, and goes into the Switchboard's memory tonight."


def stats_text(store: SignalStore, now: Optional[float] = None, days: int = STATS_DAYS) -> str:
    now = time.time() if now is None else now
    stats = store.stats(now - days * 86400)
    if not stats["decisions"]:
        return f"No decisions in the last {days} days."
    verdicts, labels = stats["verdicts"], stats["labels"]
    good, bad = labels.get("good", 0), labels.get("bad", 0)
    labeled = good + bad
    precision = f"{good / labeled:.2f}" if labeled else "-"
    # A day's rate over the days there were decisions: a trial two days old isn't diluted by 14.
    ran = round(min(days, max(1.0, (now - (stats["first_at"] or now)) / 86400)), 1)
    lines = [
        f"**Proactive team, last {days} days**",
        f"Decisions: {stats['decisions']} · tokens {stats['tokens_in']:,} in / {stats['tokens_out']:,} out "
        f"(about {stats['tokens_in'] / ran:,.0f} / {stats['tokens_out'] / ran:,.0f} a day, "
        f"over {ran:g} day{'' if ran == 1 else 's'})",
        f"Routes: {verdicts.get('deliver', 0)} deliver · {verdicts.get('absorb', 0)} absorb · "
        f"{verdicts.get('drop', 0)} dropped",
        f"Your labels: {good} ✅ · {bad} ❌ → precision {precision} (G3 wants ≥ {TARGET_PRECISION} "
        f"over ≥ {LABELS_NEEDED} labels; {labeled} so far)",
        f"Missed routes you reported: {stats['missed']}",
    ]
    lines += delivery_lines(store.would_deliver(now - days * 86400))
    # Declines a week over the weeks posts went out: at least one, so a pilot's first days aren't scaled up.
    weeks = max(1.0, (now - (stats["first_live_at"] or now)) / (7 * 86400))
    lines += outcome_lines(stats["outcomes"], weeks)
    lines += timing_lines(stats["acks"], stats["posts"])
    if stats["first_live_at"] is not None:
        lines.append(f"Duplicate posts: {stats['duplicates']} (G4 wants 0: the same fact posted to one agent "
                     f"twice within {DUPLICATE_S // 3600} h)")
    taps = []
    for tap, rows in sorted(stats["taps"].items()):
        delays = [delay for delay, _ in rows if delay is not None]
        runs = [ms for _, ms in rows if ms is not None]
        if delays:
            taps.append(f"{tap} {median(delays):.0f} s to decide, run {median(runs) / 1000 if runs else 0:.1f} s")
    if taps:
        lines.append("Median per tap: " + " · ".join(taps))
    return "\n".join(lines)


def delivery_lines(rows: List[Dict[str, Any]]) -> List[str]:
    """When the routes to deliver would land: per urgency, and by time of day. The
    evidence for the dormant quiet-hours and delivery-window levers at G3."""
    if not rows:
        return []
    delays: Dict[str, List[float]] = {}
    parts = {part: 0 for part in PARTS_OF_DAY}
    for row in rows:
        delays.setdefault(row["urgency"], []).append(max(0.0, row["deliver_at"] - row["decided_at"]))
        parts[part_of_day(row["deliver_at"])] += 1
    by_urgency = " · ".join(f"{urgency} {len(values)} (median {span(median(values))} after the decision)"
                            for urgency, values in sorted(delays.items(), key=lambda item: _urgency_order(item[0])))
    by_part = " · ".join(f"{part} {count}" for part, count in parts.items())
    return [f"Would deliver: {by_urgency}", f"  by time of day: {by_part} (night is 22:00-07:00)"]


def outcome_lines(outcomes: Dict[str, Dict[str, int]], weeks: float) -> List[str]:
    """What came of the proactive posts, in all and per recipient: G4's engagement, and
    each agent's declines a week, over `weeks`."""
    if not outcomes:
        return []

    def counted(counts: Dict[str, int]) -> str:
        engaged, ignored, declined = (counts.get(outcome, 0) for outcome in OUTCOMES)
        share = engaged / (engaged + ignored + declined)
        return f"{engaged} {ENGAGED} · {ignored} {IGNORED} · {declined} {DECLINED} → {share:.0%} engaged"

    every = {outcome: sum(counts.get(outcome, 0) for counts in outcomes.values()) for outcome in OUTCOMES}
    lines = [f"Outcomes of the posts: {counted(every)} (G4 wants ≥ {TARGET_ENGAGED:.0%} engaged, and "
             f"≤ {DECLINES_A_WEEK} decline per agent a week)"]
    for agent_id, counts in sorted(outcomes.items()):
        lines.append(f"  {agent_id}: {counted(counts)} · {counts.get(DECLINED, 0) / weeks:.1f} declines a week")
    return lines


def p95(values: List[float]) -> float:
    """The 95th percentile, by nearest rank: of 20 values the 19th smallest, of 10 the largest."""
    ordered = sorted(values)
    return ordered[(95 * len(ordered) + 99) // 100 - 1]


def timing_lines(acks: List[float], posts: List[float]) -> List[str]:
    """How fast `now` routes came, from the end of the turn each came from (G4): the
    recipient's emoji on your message, and its post."""
    parts = []
    if acks:
        parts.append(f"reaction p95 {span(p95(acks))} after the turn (G4 wants ≤ {REACTION_P95_S} s; "
                     f"{len(acks)} so far)")
    if posts:
        parts.append(f"post p95 {span(p95(posts))} (G4 wants ≤ {POST_P95_S} s; {len(posts)} so far)")
    return ["`now` routes: " + " · ".join(parts)] if parts else []


def why_text(store: SignalStore, link: str) -> str:
    """The trace of the decision behind a message: a team radio line, or a proactive post."""
    match = _LINK_ID.search(link or "")
    found = match and (store.route_by_radio_message(match.group(1)) or store.post_by_message(match.group(1)))
    if not found:
        return ("That isn't a team radio line or a proactive post I know. Paste its link "
                "(Copy Message Link).")
    return trace_text(store, found["decision_id"])


def signals_text(args: str, store: Optional[SignalStore] = None) -> str:
    """The reply to `!signals <args>` (module docstring)."""
    store = store or SignalStore()
    words = (args or "").split(maxsplit=2)
    if not words:
        return recent_text(store)
    head = words[0].lower().lstrip("#")
    if head == "stats":
        return stats_text(store)
    if head == "missed":
        return missed_text(store, (args or "").split(maxsplit=1)[1] if len(words) > 1 else "")
    if head == "why":
        return why_text(store, words[1] if len(words) > 1 else "")
    if head.isdigit():
        decision_id = int(head)
        if len(words) == 1:
            return trace_text(store, decision_id)
        verb = words[1].lower()
        if verb in ("good", "bad"):
            return label_text(store, decision_id, verb, words[2] if len(words) > 2 else "")
    return f"Usage: `!signals {SignalsCommand.usage}`"


class SignalsCommand(ChannelCommand):
    name = "signals"
    usage = "[<id> [good | bad <why>] | missed [<what>] | stats | why <link>]"
    summary = "What the proactive team decided: the latest decisions, one's trace, your labels, and stats."

    async def run(self, ctx: TypedContext, args: str) -> None:
        # The Board and main's memory log are files: read and written off the event loop.
        await ctx.reply(await asyncio.to_thread(signals_text, args))
