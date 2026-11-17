"""What friction_query hands Kaizen, and what its Monday scan finds: views over memory.db.

Each view is a plain dict, read-only, built from the learning store
(core/storage/learning.py) the way `!learn` reads it (core/learning/report.py), so
Kaizen and you see the same patterns:

    patterns   one agent's cards from one detector, recurring ones first, with each
               detector's labeled precision and the checked proposals already open
    cards      the cards themselves, newest first
    episode    one card with the turns around it, as code cut them (core/learning/digests.py)
    ledger     each agent's prompt size today, and the ledger's alerts
    proposals  what was handed in, and what the checks found
"""
import time
from typing import Any, Dict, List, Optional

from core.learning import digests
from core.learning import ledger as ledger_mod
from core.learning import report as R
from core.storage.learning import CHECKED, LearningStore

WEEK = 7 * 86400
DAY = 86400
MAX_DAYS = 30
MAX_LIMIT = 30
EXAMPLES = 3


def pattern_key(agent_id: str, detector: str) -> str:
    """How a pattern is named everywhere: "<agent>:<detector>", "?" when no agent was found."""
    return f"{agent_id or '?'}:{detector}"


def _cards(store: LearningStore, days: float, agent: str = "", detector: str = "",
           now: Optional[float] = None) -> List[Dict[str, Any]]:
    now = time.time() if now is None else now
    since = now - min(max(days, 1), MAX_DAYS) * DAY
    if agent == "?":
        return [c for c in store.friction(since, detector=detector or None) if not c.get("agent_id")]
    return store.friction(since, agent_id=agent or None, detector=detector or None)


def open_proposals(store: LearningStore, now: float) -> Dict[str, List[int]]:
    """Each pattern's checked proposals from the last 7 days: what's waiting for review."""
    found: Dict[str, List[int]] = {}
    for p in store.proposals(since=now - WEEK, status=CHECKED):
        found.setdefault(p["pattern"], []).append(p["id"])
    return found


def precision(store: LearningStore) -> Dict[str, Dict[str, int]]:
    return {detector: {"real": real, "labeled": labeled} for detector, (real, labeled) in store.precision().items()}


def patterns_view(store: LearningStore, agent: str = "", days: float = 14, limit: int = 10,
                  now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    found = R.patterns(_cards(store, days, agent, now=now))
    waiting = open_proposals(store, now)
    shown = []
    for p in found[:min(max(limit, 1), MAX_LIMIT)]:
        key = pattern_key(p.agent_id, p.detector)
        newest = sorted(p.events, key=lambda e: e["at"], reverse=True)[:EXAMPLES]
        shown.append({
            "pattern": key, "cards": p.count, "sessions": p.episodes, "severity": p.weight,
            "extra_turns": p.turns, "recurring": p.recurring, "open_proposals": waiting.get(key, []),
            "examples": [{"id": e["id"], "evidence": R.evidence(e, 160), "when": R.when(e["at"], now)}
                         for e in newest],
        })
    return {"days": days, "patterns": shown, "more": max(0, len(found) - len(shown)),
            "precision": precision(store),
            "note": "recurring: at least 3 cards in 2 sessions, or a correction saying you'd been told before"}


def cards_view(store: LearningStore, agent: str = "", detector: str = "", days: float = 14, limit: int = 10,
               now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    found = _cards(store, days, agent, detector, now)
    shown = found[:min(max(limit, 1), MAX_LIMIT)]
    cards = []
    for card in shown:
        fields = digests.card_fields(card)
        fields["when"] = R.when(card["at"], now)
        fields.pop("at", None)
        cards.append(fields)
    return {"days": days, "cards": cards, "more": max(0, len(found) - len(shown))}


def episode_view(store: LearningStore, card_id: int) -> Optional[Dict[str, Any]]:
    """The card's episode, or None when there's no such card."""
    found = store.friction_by_ids([card_id])
    return digests.digest(store, found[0]) if found else None


def ledger_view(store: LearningStore, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    day, rows, month_ago, _, alerts = R.ledger_changes(store, now)
    agents = []
    for row in rows:
        old = month_ago.get(row["agent_id"])
        agents.append({
            "agent": row["agent_id"], "persona": row["persona_chars"], "preload": row["preload_chars"],
            "memory": row["memory_chars"], "feedback": row["feedback_chars"], "instructions": row["instructions"],
            "total": ledger_mod.total(row),
            "since_month_ago": (ledger_mod.total(row) - ledger_mod.total(old)) if old else None,
        })
    return {"day": day, "agents": agents, "alerts": alerts}


def proposals_view(store: LearningStore, days: float = 14, limit: int = 10,
                   now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    found = store.proposals(since=now - min(max(days, 1), MAX_DAYS) * DAY, limit=min(max(limit, 1), MAX_LIMIT))
    return {"proposals": [{
        "id": p["id"], "status": p["status"], "pattern": p["pattern"], "path": p["path"],
        "delta_chars": p["delta_chars"], "when": R.when(p["at"], now), "rationale": p["rationale"],
        "fixes": p["fixes"], "failed_checks": [{"gate": g.get("gate"), "detail": g.get("detail")}
                                               for g in p.get("gates") or [] if not g.get("ok")],
    } for p in found]}


def open_patterns(store: LearningStore, now: float) -> List[R.Pattern]:
    """Recurring patterns from the last 14 days with no checked proposal this week, worst first."""
    waiting = open_proposals(store, now)
    recurring = [p for p in R.patterns(store.friction(now - R.PATTERN_S)) if p.recurring]
    return [p for p in recurring if pattern_key(p.agent_id, p.detector) not in waiting]
