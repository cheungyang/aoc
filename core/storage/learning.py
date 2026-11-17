"""The learning loop's tables in memory.db, and the reads its detectors make.

Phase 0 of the self-learning loop (core/learning/README.md) only observes.
The schema is core/storage/schemas/learning.sql:

- `friction_events`: what the nightly detectors found (core/learning/detectors.py),
  one row per (detector, source, source_id), so a rerun never adds one twice;
- `learning_events`: what the runtime tells the loop that no other table keeps
  -- commands, refused actions, voice replies without <speech>, your 👎
  (core/learning/events.py);
- `prompt_versions` and `prompt_files`: which prompt each model call ran on
  (core/learning/versions.py), and every repo prompt file's text by hash;
- `prompt_ledger`: a daily snapshot of each agent's prompt size (core/learning/ledger.py);
- `learning_runs`: when the nightly scan ran, and what it found;
- `proposals`: each change the analyst (agents/agent-learner/) hands in, with what the
  static checks found (core/learning/proposals.py). Saving one edits no file.

The detectors also read other stores' tables -- the Board's turn ledger and
routes, the Switchboard's decisions, the follow-ups and the ctx_ session
tables -- through the queries at the end of this module. Those queries only
SELECT: the tables belong to their own stores. JSON columns are encoded and
decoded here, so callers pass and get plain dicts and lists.
"""
import json
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from core.config.settings import Config
from core.storage.base import WAL_PRAGMAS, Row, Store
from core.storage.sessions import ARCHIVED, SessionStore
from core.storage.util import ident, table_exists

# A label on a friction event: it was real friction, or it wasn't.
REAL, NOT_REAL = "real", "not"
LABELS = (REAL, NOT_REAL)
# The kinds of learning_events rows (core/learning/events.py).
COMMAND, REFUSAL, SPEECH_FALLBACK, REACTION = "command", "refusal", "speech_fallback", "reaction"
# A proposal's status: its checks failed, or passed and it waits for review. Later
# steps add approved, applied, rejected and reverted.
FAILED, CHECKED = "failed", "checked"
# The proposals table's JSON columns, and what each holds when empty.
_PROPOSAL_JSON = {"edits": [], "fixes": [], "retires": [], "dropped": [], "gates": []}
# The turn_ledger's JSON columns, as the Board writes them (core/storage/signals.py).
_LEDGER_JSON = {"memory": [], "effects": [], "context": {}}
# A session row's message, cut to its two ends past this many characters: a tool's
# envelope opens with its status and closes with its errors (core/tooling/report.py).
_ENDS = 4000


def _loads(text: Any, empty: Any) -> Any:
    """`text` as JSON, or `empty` when it's blank, broken, or not the same kind of value."""
    if not isinstance(text, str) or not text:
        return empty
    try:
        value = json.loads(text)
    except ValueError:
        return empty
    return value if isinstance(value, type(empty)) else empty


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _friction(row: Row) -> Dict[str, Any]:
    out = dict(row)
    out["details"] = _loads(out.get("details"), {})
    return out


def _ledger_turn(row: Row) -> Dict[str, Any]:
    out = dict(row)
    for column, empty in _LEDGER_JSON.items():
        if column in out:
            out[column] = _loads(out[column], empty)
    return out


def _proposal(row: Row) -> Dict[str, Any]:
    out = dict(row)
    for column, empty in _PROPOSAL_JSON.items():
        out[column] = _loads(out.get(column), empty)
    return out


def archived_at(table: str) -> Optional[int]:
    """When a ctx_ table was archived (`<table>_archived_<epoch>`), or None for an active one."""
    head, marker, tail = table.rpartition(ARCHIVED)
    if not marker:
        return None
    digits = tail.split("_", 1)[0]  # a name the archive renamed gets `_2`, `_3`... after the epoch
    return int(digits) if digits.isdigit() else None


class LearningStore(Store):
    schema = ("learning",)
    pragmas = WAL_PRAGMAS

    def default_path(self) -> str:
        return Config().memory_db_path

    # -- friction events -----------------------------------------------------

    def record_friction(self, events: Iterable[Mapping[str, Any]], found_at: Optional[float] = None) -> int:
        """Adds each event not already recorded (same detector, source and source_id); how many were new."""
        now = time.time() if found_at is None else found_at
        added = 0
        with self.connect() as conn:
            for event in events:
                row = dict(event)
                row["details"] = _dumps(row.get("details"))
                row["found_at"] = now
                if self.insert("friction_events", row, or_="IGNORE", conn=conn) is not None:
                    added += 1
        return added

    def friction(self, since: float, *, agent_id: Optional[str] = None, detector: Optional[str] = None,
                 limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """The events that happened at or after `since`, newest first."""
        sql, params = "SELECT * FROM friction_events WHERE at >= ?", [since]
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        if detector:
            sql += " AND detector = ?"
            params.append(detector)
        sql += " ORDER BY at DESC, id DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [_friction(r) for r in self.query(sql, params)]

    def friction_summary(self, since: float) -> List[Dict[str, Any]]:
        """Per agent and detector since `since`: how many events, and their summed severity."""
        rows = self.query("SELECT agent_id, detector, count(*) AS events, sum(severity) AS weight "
                          "FROM friction_events WHERE at >= ? GROUP BY agent_id, detector", [since])
        return [dict(r) for r in rows]

    def label_friction(self, event_id: int, label: str, at: Optional[float] = None) -> bool:
        """Records your verdict on an event (REAL or NOT_REAL); False if there's no such event."""
        if label not in LABELS:
            raise ValueError(f"a label is one of {', '.join(LABELS)}, not {label!r}")
        return self.update("friction_events", {"label": label, "labeled_at": time.time() if at is None else at},
                           {"id": event_id}) > 0

    def precision(self) -> Dict[str, Tuple[int, int]]:
        """Per detector: (labeled real, labeled at all)."""
        rows = self.query("SELECT detector, sum(label = ?) AS real, count(*) AS labeled FROM friction_events "
                          "WHERE label IS NOT NULL GROUP BY detector", [REAL])
        return {r["detector"]: (int(r["real"] or 0), int(r["labeled"])) for r in rows}

    # -- what the runtime logged ---------------------------------------------

    def record_event(self, kind: str, *, at: Optional[float] = None, agent_id: str = "", channel: str = "",
                     session_id: str = "", name: str = "", detail: Optional[Mapping[str, Any]] = None) -> int:
        return self.insert("learning_events", {
            "at": time.time() if at is None else at, "kind": kind, "agent_id": agent_id or "",
            "channel": channel or "", "session_id": session_id or "", "name": name or "", "detail": _dumps(detail)})

    def events(self, kind: str, since: float, until: Optional[float] = None) -> List[Dict[str, Any]]:
        """The `kind` events from `since` (until `until`), oldest first."""
        sql, params = "SELECT * FROM learning_events WHERE kind = ? AND at >= ?", [kind, since]
        if until is not None:
            sql += " AND at < ?"
            params.append(until)
        out = []
        for row in self.query(sql + " ORDER BY at, id", params):
            event = dict(row)
            event["detail"] = _loads(event.get("detail"), {})
            out.append(event)
        return out

    # -- prompt versions -----------------------------------------------------

    def note_prompt_version(self, agent_id: str, version: str, files: Mapping[str, str], *,
                            static_chars: int = 0, memory_chars: int = 0, at: Optional[float] = None) -> bool:
        """Records a version the first time it's seen; False if it was known."""
        return self.insert("prompt_versions", {
            "agent_id": agent_id, "version": version, "first_seen": time.time() if at is None else at,
            "static_chars": static_chars, "memory_chars": memory_chars, "files": _dumps(dict(files))},
            or_="IGNORE") is not None

    def keep_prompt_files(self, files: Mapping[str, Tuple[str, str]], at: Optional[float] = None) -> int:
        """Keeps each file's text under its hash (`{sha: (path, text)}`), once; how many were new."""
        now = time.time() if at is None else at
        added = 0
        with self.connect() as conn:
            for sha, (path, content) in files.items():
                if self.insert("prompt_files", {"sha": sha, "path": path, "first_seen": now, "content": content},
                               or_="IGNORE", conn=conn) is not None:
                    added += 1
        return added

    def prompt_file(self, sha: str) -> Optional[str]:
        row = self.select_one("prompt_files", {"sha": sha}, columns=("content",))
        return row["content"] if row else None

    def prompt_versions(self, agent_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """An agent's versions, newest first."""
        rows = self.select("prompt_versions", {"agent_id": agent_id}, order_by="first_seen DESC", limit=limit)
        out = []
        for row in rows:
            version = dict(row)
            version["files"] = _loads(version.get("files"), {})
            out.append(version)
        return out

    # -- the ledger and the runs ---------------------------------------------

    def record_ledger(self, day: str, rows: Iterable[Mapping[str, Any]]) -> None:
        """Writes the day's snapshot, one row per agent, replacing that day's earlier one."""
        with self.connect() as conn:
            for row in rows:
                self.upsert("prompt_ledger", {"day": day, **row}, ("day", "agent_id"), conn=conn)

    def ledger(self, day: Optional[str] = None) -> List[Dict[str, Any]]:
        """One day's snapshot (the latest when `day` is None), by agent."""
        with self.connect() as conn:
            if day is None:
                found = conn.execute("SELECT max(day) FROM prompt_ledger").fetchone()[0]
                if found is None:
                    return []
                day = found
            rows = self.select("prompt_ledger", {"day": day}, order_by="agent_id", conn=conn)
        return [dict(r) for r in rows]

    def ledger_history(self, since_day: str) -> List[Dict[str, Any]]:
        """Every snapshot from `since_day` (YYYY-MM-DD) on, oldest first."""
        rows = self.query("SELECT * FROM prompt_ledger WHERE day >= ? ORDER BY day, agent_id", [since_day])
        return [dict(r) for r in rows]

    def runs(self, since: float) -> List[Dict[str, Any]]:
        """The nightly scans that ran at or after `since`, oldest first."""
        return [dict(r) for r in self.query("SELECT * FROM learning_runs WHERE at >= ? ORDER BY id", [since])]

    def record_run(self, *, since: float, until: float, found: int, added: int, took_s: Optional[float] = None,
                   errors: str = "", at: Optional[float] = None) -> int:
        return self.insert("learning_runs", {"at": time.time() if at is None else at, "since": since, "until": until,
                                             "found": found, "added": added, "took_s": took_s, "errors": errors})

    def last_run(self) -> Optional[Dict[str, Any]]:
        row = self.select_one("learning_runs", order_by="id DESC")
        return dict(row) if row else None

    # -- proposals ---------------------------------------------------------------

    def add_proposal(self, row: Mapping[str, Any], at: Optional[float] = None) -> int:
        """Saves a proposal (core/learning/proposals.py) with its checks; its id."""
        values = dict(row)
        for column in _PROPOSAL_JSON:
            values[column] = _dumps(values.get(column, _PROPOSAL_JSON[column]))
        values["at"] = time.time() if at is None else at
        return self.insert("proposals", values)

    def proposals(self, *, since: Optional[float] = None, status: Optional[str] = None,
                  pattern: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Proposals, newest first, by when they were handed in, their status and their pattern."""
        sql, params = "SELECT * FROM proposals WHERE 1 = 1", []
        if since is not None:
            sql += " AND at >= ?"
            params.append(since)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if pattern:
            sql += " AND pattern = ?"
            params.append(pattern)
        sql += " ORDER BY at DESC, id DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [_proposal(r) for r in self.query(sql, params)]

    def proposal(self, proposal_id: int) -> Optional[Dict[str, Any]]:
        row = self.select_one("proposals", {"id": proposal_id})
        return _proposal(row) if row else None

    # -- what the episode digests read -------------------------------------------

    def friction_by_ids(self, ids: Iterable[int]) -> List[Dict[str, Any]]:
        """The friction events with these ids, in id order; unknown ids are left out."""
        wanted = sorted({int(i) for i in ids})
        if not wanted:
            return []
        marks = ", ".join("?" for _ in wanted)
        return [_friction(r) for r in self.query(f"SELECT * FROM friction_events WHERE id IN ({marks}) ORDER BY id",
                                                 wanted)]

    def learning_event(self, event_id: int) -> Optional[Dict[str, Any]]:
        row = self.select_one("learning_events", {"id": event_id})
        if not row:
            return None
        event = dict(row)
        event["detail"] = _loads(event.get("detail"), {})
        return event

    def ledger_turns_around(self, turn_id: int, before: int = 2) -> List[Dict[str, Any]]:
        """The Board's turn `turn_id` and up to `before` earlier turns in its session, oldest first."""
        with self.connect() as conn:
            if not table_exists(conn, "turn_ledger"):
                return []
            row = conn.execute("SELECT * FROM turn_ledger WHERE id = ?", (turn_id,)).fetchone()
            if row is None:
                return []
            earlier = []
            if row["session_id"] and before > 0:
                earlier = conn.execute("SELECT * FROM turn_ledger WHERE session_id = ? AND id < ? "
                                       "ORDER BY id DESC LIMIT ?", (row["session_id"], turn_id, before)).fetchall()
        return [_ledger_turn(r) for r in reversed(earlier)] + [_ledger_turn(row)]

    # -- what the detectors read (other stores' tables, SELECT only) ---------

    def turns(self, since: float, until: float) -> List[Dict[str, Any]]:
        """The Board's turn_ledger rows that ended in [since, until), oldest first."""
        with self.connect() as conn:
            if not table_exists(conn, "turn_ledger"):
                return []
            rows = conn.execute("SELECT * FROM turn_ledger WHERE at >= ? AND at < ? ORDER BY id",
                                (since, until)).fetchall()
        return [_ledger_turn(r) for r in rows]

    def judged_routes(self, since: float, until: float) -> List[Dict[str, Any]]:
        """Routes you labeled, or whose proactive post was declined or ignored, in [since, until),
        each with its signal's gist."""
        with self.connect() as conn:
            if not (table_exists(conn, "routes") and table_exists(conn, "signals")):
                return []
            columns = {r[1] for r in conn.execute("PRAGMA table_info(routes)")}
            sql = ("SELECT r.*, s.gist AS gist FROM routes r LEFT JOIN signals s ON s.id = r.signal_id "
                   "WHERE (r.label IS NOT NULL AND r.labeled_at >= ? AND r.labeled_at < ?)")
            params: List[Any] = [since, until]
            if "outcome_at" in columns:
                sql += " OR (r.outcome IN ('declined', 'ignored') AND r.outcome_at >= ? AND r.outcome_at < ?)"
                params += [since, until]
            return [dict(r) for r in conn.execute(sql + " ORDER BY r.id", params).fetchall()]

    def decision_errors(self, since: float, until: float) -> List[Dict[str, Any]]:
        """Switchboard runs in [since, until) whose run or reply failed."""
        with self.connect() as conn:
            if not table_exists(conn, "decisions"):
                return []
            rows = conn.execute("SELECT id, at, errors FROM decisions WHERE at >= ? AND at < ? AND errors != '' "
                                "ORDER BY id", (since, until)).fetchall()
        return [dict(r) for r in rows]

    def closed_follow_ups(self, since: float, until: float) -> List[Dict[str, Any]]:
        """Follow-ups that closed in [since, until)."""
        with self.connect() as conn:
            if not table_exists(conn, "follow_ups"):
                return []
            rows = conn.execute("SELECT * FROM follow_ups WHERE status = 'closed' AND closed_at >= ? AND closed_at < ? "
                                "ORDER BY id", (since, until)).fetchall()
        return [dict(r) for r in rows]

    def session_tables(self, since: float) -> List[str]:
        """The ctx_ tables in this file that can hold rows from `since` on: every active one,
        and the archived ones archived since. archive.db isn't read (README: the detectors)."""
        with self.connect() as conn:
            names = SessionStore(self.path).tables(include_archived=True, conn=conn)
        return [name for name in names if (archived_at(name) is None or archived_at(name) >= since)]

    def session_rows(self, table: str, since: float, until: float) -> List[Dict[str, Any]]:
        """`table`'s message, token and turn_trace rows made in [since, until), in order. A message
        longer than twice _ENDS comes back as its two ends: what tool_status() reads."""
        with self.connect() as conn:
            if not table_exists(conn, table):
                return []
            columns = {r[1] for r in conn.execute(f"PRAGMA table_info({ident(table)})")}
            surface = "surface" if "surface" in columns else "NULL AS surface"
            execution = "execution_time" if "execution_time" in columns else "NULL AS execution_time"
            rows = conn.execute(
                f"SELECT id, entry_type, from_role, created_at, {surface}, {execution}, "
                f"CASE WHEN length(message) > ? THEN substr(message, 1, ?) || char(10) || '[...]' || char(10) "
                f"|| substr(message, ?) ELSE message END AS message "
                f"FROM {ident(table)} WHERE entry_type IN ('message', 'token', 'turn_trace') "
                f"AND created_at >= ? AND created_at < ? ORDER BY id",
                (2 * _ENDS, _ENDS, -_ENDS, since, until)).fetchall()
        return [dict(r) for r in rows]

    def trace_times(self, tables: Iterable[str], since: float) -> List[Tuple[str, float]]:
        """(the trace's agent, seconds to first audio) of every voice turn trace since `since`,
        in `tables`: the distribution a slow turn is measured against."""
        out: List[Tuple[str, float]] = []
        with self.connect() as conn:
            for table in tables:
                if not table_exists(conn, table):
                    continue
                try:
                    rows = conn.execute(f"SELECT message, execution_time FROM {ident(table)} "
                                        f"WHERE entry_type = 'turn_trace' AND created_at >= ? "
                                        f"AND execution_time IS NOT NULL", (since,)).fetchall()
                except sqlite3.OperationalError:  # a table too old to have execution_time
                    continue
                for row in rows:
                    agent = _loads(row["message"], {}).get("agent") or ""
                    out.append((str(agent), float(row["execution_time"])))
        return out
