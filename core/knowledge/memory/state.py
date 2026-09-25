"""`wiki/memory/state.json`: the signals and health records only code writes.

    {
      "events":    [{"date", "kind", "agent", "tag"}],        rolling 14 days
      "suggested": [{"date", "agent", "action", "tag"}],      posted once, kept as history
      "dreams":    {"<agent>": {"last_success", "consecutive_failures", "last_error"}},
      "near_duplicates": [{"tag", "texts": [a, b]}]           for compaction
    }

A suggestion already in `suggested` is never posted again: acting on it makes
it moot, and ignoring it is dismissing it.
"""
import datetime
import json
import os
from collections import Counter
from typing import Dict, Iterable, List, Optional

from core.knowledge.memory import store

EVENT_WINDOW_DAYS = 14
SUGGESTION_THRESHOLD = 3
FAILURE_ALERT_AFTER = 2
STALLED_LOG_DAYS = 7

UNSUBSCRIBED_WRITE = "unsubscribed_write"
UNKNOWN_TAG = "unknown_tag"
UNUSED_SUBSCRIPTION = "unused_subscription"
PROFILE_CAP = "profile_cap"

ADD = "add"
REMOVE = "remove"
NEW_TAG = "new_tag"


def load(pkm_dir: Optional[str] = None) -> Dict:
    path = store.state_path(pkm_dir)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("events", [])
    data.setdefault("suggested", [])
    data.setdefault("dreams", {})
    data.setdefault("near_duplicates", [])
    return data


def save(state: Dict, pkm_dir: Optional[str] = None) -> None:
    store.write_atomic(store.state_path(pkm_dir), json.dumps(state, indent=2, sort_keys=True) + "\n")


def record_event(state: Dict, kind: str, agent: Optional[str], tag: str, today: datetime.date) -> None:
    state["events"].append({"date": today.isoformat(), "kind": kind, "agent": agent, "tag": tag})


def prune_events(state: Dict, today: datetime.date) -> None:
    cutoff = (today - datetime.timedelta(days=EVENT_WINDOW_DAYS)).isoformat()
    state["events"] = [e for e in state["events"] if str(e.get("date", "")) >= cutoff]


def record_dream(state: Dict, agent: str, ok: bool, today: datetime.date, error: Optional[str] = None) -> Dict:
    health = state["dreams"].setdefault(agent, {"last_success": None, "consecutive_failures": 0})
    if ok:
        health["last_success"] = today.isoformat()
        health["consecutive_failures"] = 0
        health.pop("last_error", None)
    else:
        health["consecutive_failures"] = int(health.get("consecutive_failures", 0)) + 1
        health["last_error"] = error
    return health


def failing(state: Dict, agent: str) -> bool:
    health = state["dreams"].get(agent) or {}
    return int(health.get("consecutive_failures", 0)) >= FAILURE_ALERT_AFTER


def flag_near_duplicate(state: Dict, tag: str, a: str, b: str) -> None:
    item = {"tag": tag, "texts": sorted([a, b])}
    if item not in state["near_duplicates"]:
        state["near_duplicates"].append(item)


def _suggested(state: Dict, agent: Optional[str], action: str, tag: str) -> bool:
    return any(s.get("agent") == agent and s.get("action") == action and s.get("tag") == tag
               for s in state["suggested"])


def suggestions(state: Dict, subscriptions: Dict[str, List[str]], tags: Iterable[str],
                today: datetime.date) -> List[str]:
    """New suggestion lines from the rolling window, recorded so none repeats."""
    tags = set(tags)
    counts = Counter((e.get("kind"), e.get("agent"), e.get("tag")) for e in state["events"])
    lines = []
    for (kind, agent, tag), n in sorted(counts.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        if n < SUGGESTION_THRESHOLD:
            continue
        if kind == UNSUBSCRIBED_WRITE:
            action, subject = ADD, agent
            if tag in subscriptions.get(agent, []):
                continue
            text = f"{agent} wrote {n} `[{tag}]` entries in {EVENT_WINDOW_DAYS} days but doesn't read `{tag}` — add it to `memory_topics`?"
        elif kind == UNKNOWN_TAG:
            action, subject = NEW_TAG, None
            if tag in tags:
                continue
            text = f"{n} entries tried tag `{tag}` — add a `## {tag}` section to `TAGS.md`?"
        else:
            continue
        if _suggested(state, subject, action, tag):
            continue
        state["suggested"].append({"date": today.isoformat(), "agent": subject, "action": action, "tag": tag})
        lines.append(text)
    return lines


def unused_subscription_suggestions(state: Dict, subscriptions: Dict[str, List[str]],
                                    topic_mtimes: Dict[str, Optional[datetime.date]],
                                    writers: Dict[str, set], today: datetime.date) -> List[str]:
    """Subscribed topics unchanged for 90 days that the agent never wrote to."""
    lines = []
    for agent, topics in sorted(subscriptions.items()):
        for tag in topics:
            changed = topic_mtimes.get(tag)
            if changed is not None and (today - changed).days < store.STALE_DAYS:
                continue
            if agent in writers.get(tag, set()):
                continue
            if _suggested(state, agent, REMOVE, tag):
                continue
            state["suggested"].append({"date": today.isoformat(), "agent": agent, "action": REMOVE, "tag": tag})
            lines.append(f"{agent} reads `{tag}`, unchanged for {store.STALE_DAYS} days — drop it?")
    return lines


def file_date(path: str) -> Optional[datetime.date]:
    try:
        return datetime.date.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return None
