"""The voiceprint gate's records: one row per utterance it scored, and your marks.

Lives in `memory.db` (schema: core/db/speaker_gate.sql). Shadow mode writes a
row for every utterance; `!gate review` posts the ones worth checking -- would-be
rejections, undecided and borderline ones -- and your ✅ (me) or ❌ (not me) on
each becomes its label. The labels are what the thresholds are set from.
"""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from core.db import apply_schema

SCHEMA = "speaker_gate"


class Mode(str, Enum):
    SHADOW = "SHADOW"
    ENFORCE = "ENFORCE"


class Label(str, Enum):
    """Your mark on a reviewed utterance."""
    YOU = "YOU"
    NOT_YOU = "NOT_YOU"


class Effect(str, Enum):
    """What an utterance did, which `enforce` would block if it isn't you."""
    TURN = "TURN"          # it started a turn
    MERGE = "MERGE"        # it joined words still waiting for an answer
    BARGE_IN = "BARGE_IN"  # its words stopped the reply
    OVERLAP = "OVERLAP"    # it ended over the reply; its words decided what happened


def default_db_path() -> str:
    # Looked up at call time, so anything that repoints the session store
    # (the test suite does) repoints this too.
    from core.knowledge.memory import sqlite_session_store
    return sqlite_session_store.DEFAULT_DB_PATH


@dataclass
class GateRecord:
    at: float
    mode: Mode
    model: str
    duration_ms: int
    verdict: str                                   # a speaker_gate.Verdict value
    score: Optional[float] = None
    decided_after_ms: Optional[int] = None
    scores: List[Tuple[int, float]] = field(default_factory=list)
    score_full: Optional[float] = None
    compute_ms: Optional[int] = None
    voiced_share: Optional[float] = None
    over_reply: bool = False
    effect: Optional[Effect] = None
    user_id: Optional[str] = None
    transcript: Optional[str] = None
    audio_path: Optional[str] = None
    review_message_id: Optional[str] = None
    label: Optional[Label] = None
    labeled_at: Optional[float] = None
    id: Optional[int] = None


_COLUMNS = ("at", "mode", "model", "user_id", "duration_ms", "verdict", "score", "decided_after_ms", "scores",
            "score_full", "compute_ms", "voiced_share", "over_reply", "effect", "transcript", "audio_path",
            "review_message_id", "label", "labeled_at")


def _record(row: sqlite3.Row) -> GateRecord:
    return GateRecord(
        id=row["id"], at=row["at"], mode=Mode(row["mode"]), model=row["model"], user_id=row["user_id"],
        duration_ms=row["duration_ms"], verdict=row["verdict"], score=row["score"],
        decided_after_ms=row["decided_after_ms"], scores=[tuple(s) for s in json.loads(row["scores"] or "[]")],
        score_full=row["score_full"], compute_ms=row["compute_ms"], voiced_share=row["voiced_share"],
        over_reply=bool(row["over_reply"]), effect=Effect(row["effect"]) if row["effect"] else None,
        transcript=row["transcript"], audio_path=row["audio_path"],
        review_message_id=row["review_message_id"], label=Label(row["label"]) if row["label"] else None,
        labeled_at=row["labeled_at"],
    )


class GateLog:
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path or default_db_path()

    @contextmanager
    def _connect(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            apply_schema(conn, SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add(self, record: GateRecord) -> int:
        values = (record.at, record.mode.value, record.model, record.user_id, record.duration_ms, record.verdict,
                  record.score, record.decided_after_ms, json.dumps([list(s) for s in record.scores]),
                  record.score_full, record.compute_ms, record.voiced_share, int(record.over_reply),
                  record.effect.value if record.effect else None,
                  record.transcript, record.audio_path, record.review_message_id,
                  record.label.value if record.label else None, record.labeled_at)
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO speaker_gate_log ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' for _ in _COLUMNS)})",
                values,
            )
            return cursor.lastrowid

    def get(self, record_id: int) -> Optional[GateRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM speaker_gate_log WHERE id = ?", (record_id,)).fetchone()
        return _record(row) if row else None

    def set_audio(self, record_id: int, path: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE speaker_gate_log SET audio_path = ? WHERE id = ?", (path, record_id))

    def to_review(self, since: float, borderline_below: float, limit: int = 10) -> List[GateRecord]:
        """Unmarked, not-yet-posted utterances worth checking since `since`: every
        one the gate wouldn't call yours, and yours scored below `borderline_below`."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM speaker_gate_log WHERE at >= ? AND label IS NULL AND review_message_id IS NULL "
                "AND (verdict != 'YOU' OR score < ?) ORDER BY at ASC LIMIT ?",
                (since, borderline_below, limit),
            ).fetchall()
        return [_record(r) for r in rows]

    def set_review_message(self, record_id: int, message_id: Any) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE speaker_gate_log SET review_message_id = ? WHERE id = ?",
                         (str(message_id), record_id))

    def label_by_message(self, message_id: Any, label: Label, now: Optional[float] = None) -> bool:
        """Your ✅ or ❌ on a review message. True if the message was one."""
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE speaker_gate_log SET label = ?, labeled_at = ? WHERE review_message_id = ?",
                (label.value, time.time() if now is None else now, str(message_id)),
            ).rowcount
        return changed > 0

    def summary(self, since: float) -> Dict[str, Any]:
        """Counts since `since`: by verdict, by label, and the median scoring time."""
        with self._connect() as conn:
            verdicts = dict(conn.execute(
                "SELECT verdict, COUNT(*) FROM speaker_gate_log WHERE at >= ? GROUP BY verdict", (since,)).fetchall())
            labels = dict(conn.execute(
                "SELECT label, COUNT(*) FROM speaker_gate_log WHERE at >= ? AND label IS NOT NULL GROUP BY label",
                (since,)).fetchall())
            times = [r[0] for r in conn.execute(
                "SELECT compute_ms FROM speaker_gate_log WHERE at >= ? AND compute_ms IS NOT NULL ORDER BY compute_ms",
                (since,)).fetchall()]
        return {"verdicts": verdicts, "labels": labels, "total": sum(verdicts.values()),
                "median_compute_ms": times[len(times) // 2] if times else None}
