"""The proactive Board in memory.db: the turn ledger, and what came of it.

The taps (core/proactive/taps.py) write one ledger row per agent turn they
keep. A Switchboard run claims the rows it reads, then records its decision,
the signals it emitted and their routes. The Governor's verdicts and your
labels land on the routes. core/proactive/README.md walks through the flow;
the schema is core/storage/schemas/signals.sql. JSON columns are encoded and
decoded here, so callers pass and get plain lists and dicts. What a delivery
reads comes back as frozen dataclasses: DueDelivery (`due_deliveries`) and
NextRoute (`pending_next`).

Acceptance counts are per kind of nudge (`kind_key`, recipient/kind/tag), and
a route counts once: your label if you gave one (✅ as engaged, ❌ as
declined), else its outcome, once a live post has one (engaged, ignored or
declined: core/proactive/outcomes.py). Labeling a route moves its outcome's
count to the label, and changing a label moves it again.

Live (`PROACTIVE_DELIVERY` on), a route approved for delivery gets a
`deliveries` row: a schedule runner tick claims it once, and its proactive
turn finishes it posted, silent or failed (core/proactive/delivery.py), with
the ids of the messages it posted (`post_by_message`). A `now` route's row
also keeps when the recipient's emoji went on your message (`ack_delivery`).
A `next` route's row is made when the recipient's next scheduled run takes it
(`pending_next`, `hand_over`), and that run finishes it. Live Context
(core/prompts/live_context.py) shows agents the live situations
(`live_situations`).
"""
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from core.config.settings import Config
from core.storage.base import Row, Store
from core.storage.util import placeholders

_JSON = {
    "turn_ledger": {"memory": [], "effects": [], "context": {}},
    "signals": {"payload": {}},
    "deliveries": {"message_ids": []},
}
# A label, as the acceptance counts see it.
_COUNTED_AS = {"good": "engaged", "bad": "declined"}
# What came of a live post (core/proactive/outcomes.py): each is also its acceptance bucket.
ENGAGED, IGNORED, DECLINED = "engaged", "ignored", "declined"
OUTCOMES = (ENGAGED, IGNORED, DECLINED)
# A delivery's statuses: approved → running → posted, silent or failed. A `next`
# route's row starts handed (hand_over), and its scheduled run finishes it the same way.
APPROVED, RUNNING, HANDED, POSTED, SILENT, FAILED = "approved", "running", "handed", "posted", "silent", "failed"
FINISHED = (POSTED, SILENT, FAILED)
# Why a delivery left running was failed (fail_stuck).
STUCK = "stuck; not retried"
# Why an approved delivery was failed unrun: it was due too long ago (expire_deliveries).
EXPIRED = "expired; not posted late"
# Tags whose facts reach only the agents that read them (health and finance): the
# Governor's routes (core/proactive/governor.py) and Live Context's lines (live_situations).
SENSITIVE_TAGS = frozenset({"health", "finance"})
# Two posts of the same fact (dedupe key) to one agent this close together are a
# duplicate: the Governor's dedupe window (core/proactive/governor.py). The target is none.
DUPLICATE_S = 72 * 3600
# Columns added to a table after it shipped, with their definitions. signals.sql
# makes a missing table whole, but never changes one a database already holds.
_ADDED_COLUMNS = (
    ("deliveries", "acked_at", "REAL"),
    ("deliveries", "message_ids", "TEXT NOT NULL DEFAULT '[]'"),
    ("routes", "outcome_at", "REAL"),
)


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


def _payload(text: Optional[str]) -> Dict[str, Any]:
    """A signal's payload column as a dict, read as `_decode` reads it: {} when it's blank or
    can't be read, and when it isn't an object."""
    try:
        payload = json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


@dataclass(frozen=True)
class DueDelivery:
    """An approved delivery that's due, with what its proactive turn needs (`due_deliveries`):
    its route's offer, note, urgency and window, and its signal's fact and source."""
    route_id: int                       # routes.id: a route is delivered once
    to_agent: str                       # the recipient: whose proactive turn it is
    urgency: str                        # now or today: the urgency the Governor granted
    kind: str                           # situation, artifact, request, event or focus
    gist: str                           # the fact, in one line
    channel: str = ""                   # where it posts: the recipient's home channel
    evidence: str = ""                  # the words the fact came from; "" if none
    # The fact's details, with the `tool` it was learned by. Left out of the hash: a dict has none.
    payload: Dict[str, Any] = field(default_factory=dict, hash=False)
    tags: str = ""                      # comma-separated, from TAGS.md
    until: Optional[str] = None         # YYYY-MM-DD: when the fact stops being true, if it says
    source_agent: str = ""              # who learned it: never named to the recipient
    source_channel: str = ""            # where it was learned: never named either
    offer: str = ""                     # what the recipient could offer
    note: str = ""                      # the idea, in the Switchboard's words
    window_start: Optional[str] = None  # YYYY-MM-DD: when it starts to matter, if it says
    window_end: Optional[str] = None    # YYYY-MM-DD: when it stops mattering, if it says


def _due_delivery(record: Row) -> DueDelivery:
    return DueDelivery(
        route_id=record["route_id"],
        to_agent=record["to_agent"],
        urgency=record["urgency"],
        kind=record["kind"],
        gist=record["gist"],
        channel=record["channel"],
        evidence=record["evidence"],
        payload=_payload(record["payload"]),
        tags=record["tags"],
        until=record["until"],
        source_agent=record["source_agent"],
        source_channel=record["source_channel"],
        offer=record["offer"],
        note=record["note"],
        window_start=record["window_start"],
        window_end=record["window_end"],
    )


@dataclass(frozen=True)
class NextRoute:
    """A live `next` route no scheduled run has taken yet (`pending_next`): what its recipient's
    next scheduled run is handed, as findings from the team."""
    route_id: int                       # routes.id
    to_agent: str                       # the recipient: whose scheduled run takes it
    kind: str                           # its signal's kind: situation, artifact, request, event or focus
    gist: str                           # the fact, in one line
    signal_at: float                    # when its signal was emitted, epoch seconds: the oldest go first
    # The fact's details, with the `tool` it was learned by. Left out of the hash: a dict has none.
    payload: Dict[str, Any] = field(default_factory=dict, hash=False)
    until: Optional[str] = None         # YYYY-MM-DD: when the fact stops being true, if it says
    offer: str = ""                     # what the recipient could offer
    note: str = ""                      # the idea, in the Switchboard's words
    window_start: Optional[str] = None  # YYYY-MM-DD: when it starts to matter, if it says
    window_end: Optional[str] = None    # YYYY-MM-DD: when it stops mattering, if it says


def _next_route(record: Row) -> NextRoute:
    return NextRoute(
        route_id=record["route_id"],
        to_agent=record["to_agent"],
        kind=record["kind"],
        gist=record["gist"],
        signal_at=record["signal_at"],
        payload=_payload(record["payload"]),
        until=record["until"],
        offer=record["offer"],
        note=record["note"],
        window_start=record["window_start"],
        window_end=record["window_end"],
    )


class SignalStore(Store):
    schema = ("signals",)
    pragmas = ("journal_mode = WAL", "synchronous = NORMAL")

    def default_path(self) -> str:
        return Config().memory_db_path

    def _create(self, conn: sqlite3.Connection, **identifiers: str) -> None:
        """The schema, then the columns an older table lacks (_ADDED_COLUMNS), as
        SessionStore.ensure_table adds them."""
        super()._create(conn, **identifiers)
        for table, column, definition in _ADDED_COLUMNS:
            if column in self._columns(conn, table):
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError:
                if column not in self._columns(conn, table):  # else another connection added it first
                    raise

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]

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

        As for the Governor's routes, a health or finance fact shows only to an agent
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

    def due_deliveries(self, now: float) -> List[DueDelivery]:
        """The approved deliveries due by `now`, soonest first, each a DueDelivery with what its
        turn needs: its route's offer, note, urgency and window, and its signal's kind, gist,
        evidence, payload, tags, until and source."""
        rows = self.query(
            "SELECT d.route_id, d.to_agent, d.channel, r.offer, r.note, r.urgency, r.window_start, r.window_end, "
            "s.kind, s.gist, s.evidence, s.payload, s.tags, s.until, s.source_agent, s.source_channel "
            "FROM deliveries d JOIN routes r ON r.id = d.route_id JOIN signals s ON s.id = r.signal_id "
            "WHERE d.status = 'approved' AND d.deliver_at <= ? ORDER BY d.deliver_at ASC, d.route_id ASC", [now])
        return [_due_delivery(r) for r in rows]

    def claim_delivery(self, route_id: int, now: float) -> bool:
        """Takes a due delivery for one run; True only for the claim that changed the row.

        One conditional UPDATE: of two ticks claiming at once, only one moves it
        from approved to running.
        """
        return self.execute("UPDATE deliveries SET status = 'running', claimed_at = ? "
                            "WHERE route_id = ? AND status = 'approved' AND deliver_at <= ?",
                            [now, route_id, now]) == 1

    def ack_delivery(self, route_id: int, at: Optional[float] = None) -> None:
        """Records when the recipient's emoji went on your message (the `now` reaction):
        what the acknowledgment time is measured on. The first one counts."""
        self.execute("UPDATE deliveries SET acked_at = ? WHERE route_id = ? AND acked_at IS NULL",
                     [time.time() if at is None else at, route_id])

    def finish_delivery(self, route_id: int, status: str, *, error: str = "", at: Optional[float] = None,
                        message_ids: Optional[Sequence[str]] = None) -> None:
        """Records how a delivery ended: posted, silent or failed. A proactive turn's, or a
        handed row's scheduled run's. A post also sets its route's `delivered_at`. A run that
        outlived fail_stuck still records how it really ended. `message_ids` are the messages a
        proactive turn posted: a ✅ or ❌ on one of them is its outcome."""
        if status not in FINISHED:
            raise ValueError(f"a delivery finishes posted, silent or failed, not {status!r}")
        at = time.time() if at is None else at
        values: Dict[str, Any] = {"status": status, "finished_at": at, "error": error or ""}
        if message_ids is not None:
            values["message_ids"] = [str(message_id) for message_id in message_ids]
        with self.connect() as conn:
            self.update("deliveries", _encode("deliveries", values), {"route_id": route_id}, conn=conn)
            if status == POSTED:
                self.update("routes", {"delivered_at": at}, {"id": route_id}, conn=conn)

    def fail_stuck(self, now: float, older_than_s: float) -> List[int]:
        """Fails the deliveries running since before `now - older_than_s`, and returns their
        route ids. A stuck run may have posted already, so it is never run again. A handed
        row is left to its scheduled run, however long that takes."""
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
        many: a post that late would be stale. One guarded UPDATE, so it can't race a claim.
        A handed row was never approved: it isn't one of them."""
        return self.execute("UPDATE deliveries SET status = 'failed', finished_at = ?, error = ? "
                            "WHERE status = 'approved' AND deliver_at < ?", [now, EXPIRED, now - older_than_s])

    def pending_next(self, agent_id: str, *, since: float, today: str, limit: int) -> List[NextRoute]:
        """The live `next` routes to `agent_id` that no run has taken yet, oldest first, at most
        `limit`, each a NextRoute: what its next scheduled run is handed (core/proactive/delivery.py).

        Each is a `deliver` route of a live decision, with no deliveries row, whose signal was
        emitted since `since` and is still true on `today` (no `until`, or not past it), and
        whose window, if any, hasn't closed. `deliver_at` isn't read: resolve_times sets it for
        `next` routes in shadow too.
        """
        rows = self.query(
            "SELECT r.id AS route_id, r.to_agent, r.offer, r.note, r.window_start, r.window_end, s.kind, s.gist, "
            "s.payload, s.until, s.at AS signal_at "
            "FROM routes r JOIN signals s ON s.id = r.signal_id JOIN decisions d ON d.id = r.decision_id "
            "LEFT JOIN deliveries dl ON dl.route_id = r.id "
            "WHERE r.to_agent = ? AND r.verdict = 'deliver' AND r.urgency = 'next' AND d.mode = 'live' "
            "AND dl.route_id IS NULL AND s.at >= ? AND (s.until IS NULL OR s.until = '' OR s.until >= ?) "
            "AND (r.window_end IS NULL OR r.window_end = '' OR r.window_end >= ?) "
            "ORDER BY s.at ASC, r.id ASC LIMIT ?", [agent_id, since, today, today, limit])
        return [_next_route(r) for r in rows]

    def hand_over(self, route_ids: Sequence[int], *, to_agent: str, channel: str, now: float) -> List[int]:
        """Hands routes to `to_agent`'s scheduled run in `channel`: one `handed` deliveries row
        each, made once (INSERT OR IGNORE). Returns the ids this call handed over: a route
        another run took first isn't one of them."""
        handed = []
        with self.connect() as conn:
            for route_id in route_ids:
                row = {"route_id": route_id, "to_agent": to_agent, "channel": channel or "", "deliver_at": now,
                       "status": HANDED, "approved_at": now, "claimed_at": now}
                if self.insert("deliveries", row, or_="IGNORE", conn=conn) is not None:
                    handed.append(route_id)
        return handed

    def delivery(self, route_id: int) -> Optional[Dict[str, Any]]:
        return _decode("deliveries", self.select_one("deliveries", {"route_id": route_id}))

    # -- outcomes of live posts (core/proactive/outcomes.py) ---------------------

    def post_by_message(self, message_id: Any) -> Optional[Dict[str, Any]]:
        """The proactive post that includes Discord message `message_id`: its delivery's route,
        recipient, status and finish, with its route's decision and outcome. None for any other
        message, a handed row's included: it keeps no message ids."""
        rows = self.query(
            "SELECT d.route_id, d.to_agent, d.status, d.finished_at, r.decision_id, r.outcome FROM deliveries d "
            "JOIN routes r ON r.id = d.route_id WHERE instr(d.message_ids, ?) > 0 ORDER BY d.route_id LIMIT 1",
            [json.dumps(str(message_id))])  # '"123"': a whole id in the JSON list, never part of one
        return dict(rows[0]) if rows else None

    def unsettled_posts(self) -> List[Dict[str, Any]]:
        """The proactive posts with no outcome yet, oldest first: each delivery's route, recipient
        and when it posted. Only a `now` or `today` route is delivered as a proactive turn: a
        `next` route's row was handed to a scheduled run, whose post says nothing about the item."""
        rows = self.query(
            "SELECT d.route_id, d.to_agent, d.finished_at, r.outcome "
            "FROM deliveries d JOIN routes r ON r.id = d.route_id "
            "WHERE d.status = 'posted' AND r.outcome IS NULL AND r.urgency IN ('now', 'today') "
            "ORDER BY d.finished_at ASC, d.route_id ASC")
        return [dict(r) for r in rows]

    def first_user_turn(self, agent_id: str, *, after: float, until: float) -> Optional[Dict[str, Any]]:
        """Your first turn with `agent_id` that ended after `after`, and by `until`: a message or a
        spoken turn routed to it (caller main), not a schedule. None if there was none."""
        rows = self.query("SELECT * FROM turn_ledger WHERE agent_id = ? AND caller = 'main' AND tap != 'scheduled' "
                          "AND at > ? AND at <= ? ORDER BY at ASC, id ASC LIMIT 1", [agent_id, after, until])
        return _decode("turn_ledger", rows[0]) if rows else None

    def set_outcome(self, route_id: int, outcome: str, *, at: Optional[float] = None,
                    replaces: Sequence[str] = ()) -> bool:
        """Sets what came of a route's post (engaged, ignored or declined): only while it has none,
        or one of `replaces` (❌ turns engaged into declined). True if this call changed it. Each
        change is one guarded UPDATE, so of two calls at once, or the same reaction offered by
        every bot runner, only one does.

        The change counts toward the route's kind of nudge, unless you labeled the route: a route
        counts once, and your label wins. The outcome it replaces is taken off the count.
        """
        if outcome not in OUTCOMES or any(old not in OUTCOMES for old in replaces):
            raise ValueError(f"an outcome is engaged, ignored or declined, not {outcome!r}")
        at = time.time() if at is None else at
        with self.connect() as conn:
            before = None
            changed = conn.execute("UPDATE routes SET outcome = ?, outcome_at = ? WHERE id = ? AND outcome IS NULL",
                                   [outcome, at, route_id]).rowcount
            for old in replaces:
                if changed:
                    break
                if old != outcome and conn.execute("UPDATE routes SET outcome = ?, outcome_at = ? WHERE id = ? "
                                                   "AND outcome = ?", [outcome, at, route_id, old]).rowcount:
                    changed, before = 1, old
            if not changed:
                return False
            row = conn.execute("SELECT kind_key, label FROM routes WHERE id = ?", [route_id]).fetchone()
            if row["label"] not in _COUNTED_AS:
                if before is not None:
                    self._count(conn, row["kind_key"], before, -1, at)
                self._count(conn, row["kind_key"], outcome, +1, at)
        return True

    # -- labels and acceptance -------------------------------------------------

    def label_routes(self, route_ids: Iterable[int], label: str, *, reason: Optional[str] = None,
                     note: Optional[str] = None, at: Optional[float] = None) -> List[Dict[str, Any]]:
        """Sets your label ("good" or "bad") on routes, and counts it toward each one's kind of
        nudge in place of what counted before: a label given earlier, or else the route's
        outcome. Returns the routes as they were."""
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
                elif row["outcome"] in OUTCOMES:  # its outcome counted until now
                    self._count(conn, row["kind_key"], row["outcome"], -1, at)
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
        labels, tokens, and each tap's delay (its first turn to the decision's start) and run time.
        For the posts' targets: each recipient's outcomes, when the first live decision was, and
        for each live `now` route, how long after its source turn ended the recipient's emoji went
        on your message (`acks`), and its post landed (`posts`); and the posts of a fact one agent
        already had posted within DUPLICATE_S (`duplicates`: the target is none)."""
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
        outcomes: Dict[str, Dict[str, int]] = {}
        for row in self.query("SELECT r.to_agent, r.outcome, count(*) FROM routes r "
                              "JOIN decisions d ON d.id = r.decision_id WHERE d.at >= ? AND r.outcome IS NOT NULL "
                              "GROUP BY r.to_agent, r.outcome", [since]):
            outcomes.setdefault(row[0], {})[row[1]] = row[2]
        timings = self.query(
            "SELECT dl.status, dl.finished_at, dl.acked_at, (SELECT max(t.at) FROM turn_ledger t "
            "WHERE t.decision_id = r.decision_id AND t.agent_id = s.source_agent) AS turn_at "
            "FROM deliveries dl JOIN routes r ON r.id = dl.route_id JOIN signals s ON s.id = r.signal_id "
            "JOIN decisions d ON d.id = r.decision_id WHERE d.at >= ? AND r.urgency = 'now'", [since])
        # A post of a fact that one agent already had posted within DUPLICATE_S: each pair counts once.
        duplicates = self.query(
            "SELECT count(*) FROM deliveries a JOIN routes ra ON ra.id = a.route_id "
            "JOIN signals sa ON sa.id = ra.signal_id JOIN decisions da ON da.id = ra.decision_id "
            "WHERE da.at >= ? AND a.status = 'posted' AND sa.dedupe_key != '' AND EXISTS ("
            "SELECT 1 FROM deliveries b JOIN routes rb ON rb.id = b.route_id JOIN signals sb ON sb.id = rb.signal_id "
            "WHERE b.status = 'posted' AND b.route_id != a.route_id AND rb.to_agent = ra.to_agent "
            "AND sb.dedupe_key = sa.dedupe_key AND a.finished_at - b.finished_at < ? "
            "AND (b.finished_at < a.finished_at OR (b.finished_at = a.finished_at AND b.route_id < a.route_id)))",
            [since, DUPLICATE_S])[0][0]
        return {
            "decisions": decisions[0] or 0,
            "tokens_in": decisions[1] or 0,
            "tokens_out": decisions[2] or 0,
            "first_at": decisions[3],
            "verdicts": verdicts,
            "labels": labels,
            "taps": taps,
            "missed": self.query("SELECT count(*) FROM missed_routes WHERE at >= ?", [since])[0][0],
            "outcomes": outcomes,
            "first_live_at": self.query("SELECT min(at) FROM decisions WHERE at >= ? AND mode = 'live'", [since])[0][0],
            "acks": [r["acked_at"] - r["turn_at"] for r in timings
                     if r["acked_at"] is not None and r["turn_at"] is not None],
            "posts": [r["finished_at"] - r["turn_at"] for r in timings
                      if r["status"] == POSTED and r["finished_at"] is not None and r["turn_at"] is not None],
            "duplicates": duplicates,
        }
