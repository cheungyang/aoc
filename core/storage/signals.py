"""The proactive Board in memory.db: the turn ledger, and what came of it.

The taps (core/proactive/taps.py) write one ledger row per agent turn they
keep. A Switchboard run claims the rows it reads, then records its decision,
the signals it emitted and their routes. The Governor's verdicts and your
labels land on the routes. core/proactive/README.md walks through the flow;
the schema is core/storage/schemas/signals.sql. JSON columns are encoded and
decoded here, so callers pass and get plain lists and dicts.

Acceptance counts are per kind of nudge (`kind_key`, recipient/kind/tag).
Until delivery outcomes are captured (stage S4), your team radio labels are
the outcome: ✅ counts as engaged, ❌ as declined, and changing a label moves
its count.

Live (`PROACTIVE_DELIVERY` on), a route approved for delivery gets a
`deliveries` row: a schedule runner tick claims it once, and its proactive
turn finishes it posted, silent or failed (core/proactive/delivery.py). Live
Context (core/prompts/live_context.py) shows agents the live situations
(`live_situations`).
"""
import json
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from core.config.settings import Config
from core.storage.base import Row, Store
from core.storage.util import placeholders

_JSON = {
    "turn_ledger": {"memory": [], "effects": [], "context": {}},
    "signals": {"payload": {}},
}
# A label, as the acceptance counts see it.
_COUNTED_AS = {"good": "engaged", "bad": "declined"}
# A delivery's statuses: approved → running → posted, silent or failed.
APPROVED, RUNNING, POSTED, SILENT, FAILED = "approved", "running", "posted", "silent", "failed"
FINISHED = (POSTED, SILENT, FAILED)
# Why a delivery left running was failed (fail_stuck).
STUCK = "stuck; not retried"
# Why an approved delivery was failed unrun: it was due too long ago (expire_deliveries).
EXPIRED = "expired; not posted late"
# Tags whose facts reach only the agents that read them (D10, decided at G3): the
# Governor's routes (core/proactive/governor.py) and Live Context's lines (live_situations).
SENSITIVE_TAGS = frozenset({"health", "finance"})


def _encode(table: str, row: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for column in _JSON.get(table, {}):
        if column in out and not isinstance(out[column], str):
            out[column] = json.dumps(out[column], ensure_ascii=False, default=str)
    return out


def _decode(table: str, row: Optional[Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out = dict(row)
    for column, empty in _JSON.get(table, {}).items():
        if column in out:
            try:
                out[column] = json.loads(out[column]) if out[column] else empty
            except (TypeError, ValueError):
                out[column] = empty
    return out


class SignalStore(Store):
    schema = ("signals",)
    pragmas = ("journal_mode = WAL", "synchronous = NORMAL")

    def default_path(self) -> str:
        return Config().memory_db_path

    # -- the turn ledger -----------------------------------------------------

    def record_turn(self, entry: Mapping[str, Any]) -> int:
        """Adds one ledger row; returns its id."""
        return self.insert("turn_ledger", _encode("turn_ledger", entry))

    def pending_turns(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """The rows no Switchboard run has read yet, oldest first: one session's, or every one."""
        where: Dict[str, Any] = {"decision_id": None}
        if session_id is not None:
            where["session_id"] = session_id
        return [_decode("turn_ledger", r) for r in self.select("turn_ledger", where, order_by="id ASC")]

    def claim_turns(self, ids: Sequence[int], decision_id: int) -> List[int]:
        """Marks rows read by `decision_id`, taking only those still unread; returns the ids it took.

        One conditional UPDATE, then a read of what this decision holds: of two
        runs claiming at once, only one can take a row.
        """
        if not ids:
            return []
        marks = placeholders(len(ids))
        with self.connect() as conn:
            conn.execute(f"UPDATE turn_ledger SET decision_id = ? WHERE id IN ({marks}) AND decision_id IS NULL",
                         [decision_id, *ids])
            return [r["id"] for r in conn.execute(
                f"SELECT id FROM turn_ledger WHERE id IN ({marks}) AND decision_id = ? ORDER BY id",
                [*ids, decision_id])]

    def turns_of(self, decision_id: int) -> List[Dict[str, Any]]:
        return [_decode("turn_ledger", r)
                for r in self.select("turn_ledger", {"decision_id": decision_id}, order_by="id ASC")]

    def last_turn_at(self, *, agent_id: Optional[str] = None, user_only: bool = True) -> Optional[float]:
        """When the latest ledger turn ended: with `agent_id`, or with anyone. `user_only`
        counts only turns you started (a message or a spoken turn, not a schedule)."""
        sql, params = "SELECT max(at) FROM turn_ledger WHERE 1 = 1", []
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        if user_only:
            sql += " AND tap != 'scheduled' AND caller = 'main'"
        rows = self.query(sql, params)
        return rows[0][0] if rows and rows[0][0] is not None else None

    def user_turn_times(self, since: float) -> List[float]:
        """When each turn you started ended, since `since`, in order: when you were active."""
        rows = self.query("SELECT at FROM turn_ledger WHERE at >= ? AND tap != 'scheduled' AND caller = 'main' "
                          "ORDER BY at ASC", [since])
        return [r[0] for r in rows]

    # -- decisions -------------------------------------------------------------

    def start_decision(self, *, tap: str, session_id: str, first_turn_at: Optional[float], mode: str,
                       at: Optional[float] = None) -> int:
        return self.insert("decisions", {"at": time.time() if at is None else at, "tap": tap,
                                         "session_id": session_id, "first_turn_at": first_turn_at, "mode": mode})

    def finish_decision(self, decision_id: int, *, took_ms: Optional[int] = None, tokens_in: Optional[int] = None,
                        tokens_out: Optional[int] = None, reply: str = "", prompt: str = "", errors: str = "") -> None:
        self.update("decisions", {"took_ms": took_ms, "tokens_in": tokens_in, "tokens_out": tokens_out,
                                  "reply": reply or "", "prompt": prompt or "", "errors": errors or ""},
                    {"id": decision_id})

    def decision(self, decision_id: int) -> Optional[Dict[str, Any]]:
        """One decision, with its turns, signals and routes."""
        row = self.select_one("decisions", {"id": decision_id})
        if row is None:
            return None
        out = dict(row)
        out["turns"] = self.turns_of(decision_id)
        out["signals"] = [_decode("signals", r)
                          for r in self.select("signals", {"decision_id": decision_id}, order_by="id ASC")]
        out["routes"] = self.routes_of(decision_id)
        return out

    def recent_decisions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """The latest decisions, newest first, each with its routes."""
        rows = self.select("decisions", order_by="id DESC", limit=limit)
        return [dict(r, routes=self.routes_of(r["id"])) for r in rows]

    # -- signals and routes ----------------------------------------------------

    def add_signal(self, signal: Mapping[str, Any]) -> int:
        return self.insert("signals", _encode("signals", signal))

    def add_route(self, route: Mapping[str, Any]) -> int:
        return self.insert("routes", dict(route))

    def routes_of(self, decision_id: int) -> List[Dict[str, Any]]:
        """A decision's routes, each with its signal's kind, gist, tags and source."""
        rows = self.query(
            "SELECT r.*, s.kind, s.gist, s.tags, s.until, s.source_agent, s.source_channel, s.evidence_found "
            "FROM routes r JOIN signals s ON s.id = r.signal_id WHERE r.decision_id = ? ORDER BY r.id ASC",
            [decision_id])
        return [dict(r) for r in rows]

    def route(self, route_id: int) -> Optional[Dict[str, Any]]:
        """One route, with its signal's kind, gist, tags and source, and its decision's mode."""
        rows = self.query("SELECT r.*, s.kind, s.gist, s.tags, s.source_agent, s.source_channel, d.mode "
                          "FROM routes r JOIN signals s ON s.id = r.signal_id "
                          "LEFT JOIN decisions d ON d.id = r.decision_id WHERE r.id = ?", [route_id])
        return dict(rows[0]) if rows else None

    def active_signals(self, *, since: float, today: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Signals emitted since `since` that are still true on `today` (no `until`, or not
        past it), newest first, each with where its routes went: the Board the Switchboard sees."""
        rows = self.query(
            "SELECT * FROM signals WHERE at >= ? AND (until IS NULL OR until = '' OR until >= ?) "
            "ORDER BY id DESC LIMIT ?", [since, today, limit])
        out = []
        for row in rows:
            signal = _decode("signals", row)
            signal["routes"] = [dict(r) for r in self.query(
                "SELECT to_agent, urgency, verdict FROM routes WHERE signal_id = ? ORDER BY id", [row["id"]])]
            out.append(signal)
        return out

    def live_situations(self, topics: Iterable[str], *, today: str, since: float,
                        limit: int = 8) -> List[Dict[str, Any]]:
        """What Live Context shows an agent that reads `topics`, newest first, at most `limit`:
        the situations emitted since `since`, with their evidence found and still true on
        `today` (`until` today or later), whose tags meet `topics`.

        As for the Governor's routes (D10), a health or finance fact shows only to an agent
        that reads that tag, and a sensitive one only to an agent that reads either. A fact
        emitted twice (the same dedupe key) shows once, as its newest wording.
        """
        wanted = set(topics)
        if not wanted:
            return []
        rows = self.query("SELECT * FROM signals WHERE kind = 'situation' AND evidence_found = 1 AND at >= ? "
                          "AND until IS NOT NULL AND until >= ? ORDER BY at DESC, id DESC", [since, today])
        out, seen = [], set()
        for row in rows:
            tags = {t.strip() for t in (row["tags"] or "").split(",") if t.strip()}
            guarded = tags & SENSITIVE_TAGS
            if not tags & wanted or not guarded <= wanted:
                continue
            if not guarded and row["sensitivity"] == "sensitive" and not SENSITIVE_TAGS & wanted:
                continue
            if row["dedupe_key"] and row["dedupe_key"] in seen:
                continue
            seen.add(row["dedupe_key"])
            out.append(_decode("signals", row))
            if len(out) >= limit:
                break
        return out

    def recent_route(self, *, to_agent: str, dedupe_key: str, since: float) -> Optional[Dict[str, Any]]:
        """The latest route to `to_agent` of a signal with `dedupe_key`, since `since`, that
        wasn't dropped: what the Governor's dedupe rule checks."""
        if not dedupe_key:
            return None
        rows = self.query(
            "SELECT r.* FROM routes r JOIN signals s ON s.id = r.signal_id "
            "WHERE r.to_agent = ? AND s.dedupe_key = ? AND s.at >= ? AND r.verdict != 'drop' "
            "ORDER BY r.id DESC LIMIT 1", [to_agent, dedupe_key, since])
        return dict(rows[0]) if rows else None

    def undelivered(self) -> List[Dict[str, Any]]:
        """Routes to deliver whose time isn't set yet: `today` and `next` routes waiting for a quiet stretch or a touchpoint."""
        rows = self.query("SELECT r.*, s.at AS signal_at FROM routes r JOIN signals s ON s.id = r.signal_id "
                          "WHERE r.verdict = 'deliver' AND r.deliver_at IS NULL ORDER BY r.id ASC")
        return [dict(r) for r in rows]

    def set_route_timing(self, route_id: int, *, deliver_at: Optional[float] = None, urgency: Optional[str] = None,
                         reason: Optional[str] = None) -> None:
        """Sets when a route is (or would be) delivered, or moves it to another urgency, with why."""
        values: Dict[str, Any] = {}
        if deliver_at is not None:
            values["deliver_at"] = deliver_at
        if urgency is not None:
            values["urgency"] = urgency
        if reason is not None:
            values["reason"] = reason
        if values:
            self.update("routes", values, {"id": route_id})

    def next_turn_of(self, agent_id: str, *, after: float) -> Optional[Dict[str, Any]]:
        """`agent_id`'s first touchpoint after `after`: a turn you started with it, or its scheduled run."""
        rows = self.query("SELECT * FROM turn_ledger WHERE agent_id = ? AND at > ? "
                          "AND (caller = 'main' OR tap = 'scheduled') ORDER BY at ASC LIMIT 1", [agent_id, after])
        return _decode("turn_ledger", rows[0]) if rows else None

    def set_radio_message(self, route_id: int, message_id: str) -> None:
        self.update("routes", {"radio_message_id": str(message_id)}, {"id": route_id})

    def set_poll_message(self, route_id: int, message_id: str) -> None:
        self.update("routes", {"poll_message_id": str(message_id)}, {"id": route_id})

    def route_by_radio_message(self, message_id: Any) -> Optional[Dict[str, Any]]:
        row = self.select_one("routes", {"radio_message_id": str(message_id)})
        return self.route(row["id"]) if row else None

    def route_by_poll_message(self, message_id: Any) -> Optional[Dict[str, Any]]:
        row = self.select_one("routes", {"poll_message_id": str(message_id)})
        return self.route(row["id"]) if row else None

    # -- deliveries (live mode) ------------------------------------------------

    def approve_delivery(self, route_id: int, *, to_agent: str, channel: str, deliver_at: float,
                         at: Optional[float] = None) -> bool:
        """Approves `route_id` for a proactive turn in `channel`, due at `deliver_at`.
        A route is approved once: a second approval changes nothing. True if this one approved it."""
        row = {"route_id": route_id, "to_agent": to_agent, "channel": channel or "", "deliver_at": deliver_at,
               "status": APPROVED, "approved_at": time.time() if at is None else at}
        return self.insert("deliveries", row, or_="IGNORE") is not None

    def due_deliveries(self, now: float) -> List[Dict[str, Any]]:
        """The approved deliveries due by `now`, soonest first, each with what its turn needs:
        its route's offer, note, urgency and window, and its signal's kind, gist, evidence,
        payload, tags, until and source."""
        rows = self.query(
            "SELECT d.*, r.offer, r.note, r.urgency, r.window_start, r.window_end, s.kind, s.gist, s.evidence, "
            "s.payload, s.tags, s.until, s.source_agent, s.source_channel "
            "FROM deliveries d JOIN routes r ON r.id = d.route_id JOIN signals s ON s.id = r.signal_id "
            "WHERE d.status = 'approved' AND d.deliver_at <= ? ORDER BY d.deliver_at ASC, d.route_id ASC", [now])
        return [_decode("signals", r) for r in rows]

    def claim_delivery(self, route_id: int, now: float) -> bool:
        """Takes a due delivery for one run; True only for the claim that changed the row.

        One conditional UPDATE: of two ticks claiming at once, only one moves it
        from approved to running.
        """
        return self.execute("UPDATE deliveries SET status = 'running', claimed_at = ? "
                            "WHERE route_id = ? AND status = 'approved' AND deliver_at <= ?",
                            [now, route_id, now]) == 1

    def finish_delivery(self, route_id: int, status: str, *, error: str = "", at: Optional[float] = None) -> None:
        """Records how a delivery ended: posted, silent or failed. A post also sets its route's
        `delivered_at`. A run that outlived fail_stuck still records how it really ended."""
        if status not in FINISHED:
            raise ValueError(f"a delivery finishes posted, silent or failed, not {status!r}")
        at = time.time() if at is None else at
        with self.connect() as conn:
            self.update("deliveries", {"status": status, "finished_at": at, "error": error or ""},
                        {"route_id": route_id}, conn=conn)
            if status == POSTED:
                self.update("routes", {"delivered_at": at}, {"id": route_id}, conn=conn)

    def fail_stuck(self, now: float, older_than_s: float) -> List[int]:
        """Fails the deliveries running since before `now - older_than_s`, and returns their
        route ids. A stuck run may have posted already, so it is never run again."""
        failed = []
        with self.connect() as conn:
            stuck = [r[0] for r in conn.execute(
                "SELECT route_id FROM deliveries WHERE status = 'running' AND claimed_at < ? ORDER BY route_id",
                [now - older_than_s])]
            for route_id in stuck:
                if conn.execute("UPDATE deliveries SET status = 'failed', finished_at = ?, error = ? "
                                "WHERE route_id = ? AND status = 'running'", [now, STUCK, route_id]).rowcount:
                    failed.append(route_id)
        return failed

    def expire_deliveries(self, now: float, older_than_s: float) -> int:
        """Fails the approved deliveries due before `now - older_than_s`, unrun, and returns how
        many: a post that late would be stale. One guarded UPDATE, so it can't race a claim."""
        return self.execute("UPDATE deliveries SET status = 'failed', finished_at = ?, error = ? "
                            "WHERE status = 'approved' AND deliver_at < ?", [now, EXPIRED, now - older_than_s])

    def delivery(self, route_id: int) -> Optional[Dict[str, Any]]:
        row = self.select_one("deliveries", {"route_id": route_id})
        return dict(row) if row else None

    # -- labels and acceptance -------------------------------------------------

    def label_routes(self, route_ids: Iterable[int], label: str, *, reason: Optional[str] = None,
                     note: Optional[str] = None, at: Optional[float] = None) -> List[Dict[str, Any]]:
        """Sets your label ("good" or "bad") on routes, and counts it toward each one's
        kind of nudge; a route labeled before moves its count. Returns the routes as they were."""
        if label not in _COUNTED_AS:
            raise ValueError(f"label must be good or bad, not {label!r}")
        at = time.time() if at is None else at
        before = []
        with self.connect() as conn:
            for route_id in route_ids:
                row = conn.execute("SELECT * FROM routes WHERE id = ?", [route_id]).fetchone()
                if row is None:
                    continue
                before.append(dict(row))
                if row["label"] in _COUNTED_AS:
                    self._count(conn, row["kind_key"], _COUNTED_AS[row["label"]], -1, at)
                self._count(conn, row["kind_key"], _COUNTED_AS[label], +1, at)
                values: Dict[str, Any] = {"label": label, "labeled_at": at}
                if label == "good":
                    values.update(label_reason=None, label_note=None)
                if reason is not None:
                    values["label_reason"] = reason
                if note is not None:
                    values["label_note"] = note
                self.update("routes", values, {"id": route_id}, conn=conn)
        return before

    def set_label_reason(self, route_ids: Iterable[int], reason: str, note: Optional[str] = None) -> None:
        values: Dict[str, Any] = {"label_reason": reason}
        if note is not None:
            values["label_note"] = note
        with self.connect() as conn:
            for route_id in route_ids:
                self.update("routes", values, {"id": route_id}, conn=conn)

    def _count(self, conn, kind_key: str, bucket: str, delta: int, at: float) -> None:
        if not kind_key:
            return
        conn.execute("INSERT INTO acceptance (kind_key, updated) VALUES (?, ?) ON CONFLICT (kind_key) DO NOTHING",
                     [kind_key, at])
        conn.execute(f"UPDATE acceptance SET {bucket} = max(0, {bucket} + ?), updated = ? WHERE kind_key = ?",
                     [delta, at, kind_key])

    def acceptance(self, kind_key: str) -> Dict[str, int]:
        row = self.select_one("acceptance", {"kind_key": kind_key})
        return {b: (row[b] if row else 0) for b in ("engaged", "ignored", "declined")}

    def acceptance_table(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.select("acceptance", order_by="kind_key ASC")]

    def add_missed(self, text: str, *, to_agent: str = "", at: Optional[float] = None) -> int:
        return self.insert("missed_routes", {"at": time.time() if at is None else at, "text": text,
                                             "to_agent": to_agent})

    def missed(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.select("missed_routes", order_by="id DESC", limit=limit)]

    # -- what !signals stats shows -------------------------------------------------

    def would_deliver(self, since: float) -> List[Dict[str, Any]]:
        """Each `deliver` route of a decision since `since` whose time is known: its
        urgency, when it was decided, and when it would be (or was) delivered."""
        rows = self.query("SELECT r.urgency, d.at AS decided_at, r.deliver_at FROM routes r "
                          "JOIN decisions d ON d.id = r.decision_id "
                          "WHERE d.at >= ? AND r.verdict = 'deliver' AND r.deliver_at IS NOT NULL "
                          "ORDER BY r.deliver_at ASC", [since])
        return [dict(r) for r in rows]

    def stats(self, since: float) -> Dict[str, Any]:
        """Counts since `since`: decisions (and when the first was), routes by verdict,
        labels, tokens, and each tap's delay (its first turn to the decision's start) and run time."""
        decisions = self.query("SELECT count(*), sum(tokens_in), sum(tokens_out), min(at) FROM decisions WHERE at >= ?",
                               [since])[0]
        verdicts = {r[0]: r[1] for r in self.query(
            "SELECT r.verdict, count(*) FROM routes r JOIN decisions d ON d.id = r.decision_id "
            "WHERE d.at >= ? GROUP BY r.verdict", [since])}
        labels = {r[0]: r[1] for r in self.query(
            "SELECT r.label, count(*) FROM routes r JOIN decisions d ON d.id = r.decision_id "
            "WHERE d.at >= ? AND r.label IS NOT NULL GROUP BY r.label", [since])}
        taps = {}
        for row in self.query("SELECT tap, at - first_turn_at AS delay, took_ms FROM decisions "
                              "WHERE at >= ? AND first_turn_at IS NOT NULL ORDER BY tap", [since]):
            taps.setdefault(row["tap"], []).append((row["delay"], row["took_ms"]))
        return {
            "decisions": decisions[0] or 0,
            "tokens_in": decisions[1] or 0,
            "tokens_out": decisions[2] or 0,
            "first_at": decisions[3],
            "verdicts": verdicts,
            "labels": labels,
            "taps": taps,
            "missed": self.query("SELECT count(*) FROM missed_routes WHERE at >= ?", [since])[0][0],
        }
