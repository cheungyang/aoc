"""The voiceprint gate's records: one row per utterance it scored, and your marks.

Lives in `memory.db` (schema: core/storage/schemas/speaker_gate_log.sql).
Shadow mode writes a row for every utterance; `!gate review` posts the ones
worth checking -- would-be rejections, undecided and borderline ones -- and
your ✅ (me) or ❌ (not me) on each becomes its label. The labels are what the
thresholds are set from.
"""
import json
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from core.storage.base import Store
from core.config.settings import Config


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


def _values(record: GateRecord) -> Dict[str, Any]:
    # The enums are stored by their values (core/storage/util.py `bind`).
    values = {column: getattr(record, column) for column in _COLUMNS}
    values.update(scores=json.dumps([list(s) for s in record.scores]), over_reply=int(record.over_reply))
    return values


class GateLog(Store):
    schema = ("speaker_gate_log",)

    def default_path(self) -> str:
        return Config().memory_db_path

    def add(self, record: GateRecord) -> int:
        return self.insert("speaker_gate_log", _values(record))

    def get(self, record_id: int) -> Optional[GateRecord]:
        row = self.select_one("speaker_gate_log", {"id": record_id})
        return _record(row) if row else None

    def set_audio(self, record_id: int, path: Optional[str]) -> None:
        self.update("speaker_gate_log", {"audio_path": path}, {"id": record_id})

    def to_review(self, since: float, borderline_below: float, limit: int = 10) -> List[GateRecord]:
        """Unmarked, not-yet-posted utterances worth checking since `since`: every
        one the gate wouldn't call yours, and yours scored below `borderline_below`."""
        rows = self.query(
            "SELECT * FROM speaker_gate_log WHERE at >= ? AND label IS NULL AND review_message_id IS NULL "
            "AND (verdict != 'YOU' OR score < ?) ORDER BY at ASC LIMIT ?",
            (since, borderline_below, limit),
        )
        return [_record(r) for r in rows]

    def set_review_message(self, record_id: int, message_id: Any) -> None:
        self.update("speaker_gate_log", {"review_message_id": str(message_id)}, {"id": record_id})

    def label_by_message(self, message_id: Any, label: Label, now: Optional[float] = None) -> bool:
        """Your ✅ or ❌ on a review message. True if the message was one."""
        changed = self.update("speaker_gate_log",
                              {"label": label, "labeled_at": time.time() if now is None else now},
                              {"review_message_id": str(message_id)})
        return changed > 0

    def summary(self, since: float) -> Dict[str, Any]:
        """Counts since `since`: by verdict, by label, and the median scoring time."""
        with self.connect() as conn:
            verdicts = dict(self.query(
                "SELECT verdict, COUNT(*) FROM speaker_gate_log WHERE at >= ? GROUP BY verdict", (since,), conn=conn))
            labels = dict(self.query(
                "SELECT label, COUNT(*) FROM speaker_gate_log WHERE at >= ? AND label IS NOT NULL GROUP BY label",
                (since,), conn=conn))
            times = [r[0] for r in self.query(
                "SELECT compute_ms FROM speaker_gate_log WHERE at >= ? AND compute_ms IS NOT NULL ORDER BY compute_ms",
                (since,), conn=conn)]
        return {"verdicts": verdicts, "labels": labels, "total": sum(verdicts.values()),
                "median_compute_ms": times[len(times) // 2] if times else None}
