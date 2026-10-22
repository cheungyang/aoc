"""GateLog: the voiceprint gate's records in memory.db, and your marks on them."""
import sqlite3

import pytest

from core.voice.listening.gate_log import Effect, GateLog, GateRecord, Label, Mode


def record(at=1000.0, verdict="NOT_YOU", score=0.3, **kw):
    base = dict(at=at, mode=Mode.SHADOW, model="m.onnx", duration_ms=1800, verdict=verdict, score=score,
                decided_after_ms=1000, scores=[(1000, score)], score_full=score, compute_ms=12,
                voiced_share=0.8, over_reply=False, effect=Effect.TURN, user_id="42", transcript="hello there")
    base.update(kw)
    return GateRecord(**base)


def test_a_record_round_trips():
    log = GateLog()
    record_id = log.add(record(over_reply=True, effect=Effect.BARGE_IN))
    stored = log.get(record_id)
    assert stored.id == record_id
    assert (stored.mode, stored.effect, stored.over_reply, stored.label) == (Mode.SHADOW, Effect.BARGE_IN, True, None)
    assert stored.scores == [(1000, 0.3)]
    assert log.get(record_id + 1) is None


def test_the_schema_holds_only_known_categories():
    with pytest.raises(sqlite3.IntegrityError):
        GateLog().add(record(verdict="MAYBE"))


def test_audio_is_attached_later():
    log = GateLog()
    record_id = log.add(record())
    log.set_audio(record_id, "/clips/1.wav")
    assert log.get(record_id).audio_path == "/clips/1.wav"


def test_review_takes_unmarked_unposted_doubtful_ones_since_a_time_oldest_first():
    log = GateLog()
    rejected = log.add(record(at=2000, verdict="NOT_YOU", score=0.2))
    undecided = log.add(record(at=1500, verdict="UNDECIDED", score=None))
    thin = log.add(record(at=1800, verdict="YOU", score=0.65))
    log.add(record(at=1900, verdict="YOU", score=0.9))                  # confidently you
    log.add(record(at=100, verdict="NOT_YOU"))                          # too old
    posted = log.add(record(at=2100, verdict="NOT_YOU"))
    log.set_review_message(posted, 555)
    marked = log.add(record(at=2200, verdict="NOT_YOU"))
    log.set_review_message(marked, 556)
    log.label_by_message(556, Label.NOT_YOU)

    assert [r.id for r in log.to_review(since=1000, borderline_below=0.7)] == [undecided, thin, rejected]
    assert len(log.to_review(since=1000, borderline_below=0.7, limit=2)) == 2
    assert len(log.to_review(since=1000, borderline_below=2.0)) == 4, "below 2 takes every unmarked one"


def test_a_reaction_on_a_review_message_labels_its_utterance():
    log = GateLog()
    record_id = log.add(record())
    log.set_review_message(record_id, 777)
    assert log.label_by_message(777, Label.YOU, now=5.0) is True
    stored = log.get(record_id)
    assert (stored.label, stored.labeled_at, stored.review_message_id) == (Label.YOU, 5.0, "777")
    assert log.label_by_message(778, Label.YOU) is False, "not a review message"


def test_summary_counts_verdicts_and_marks_since_a_time():
    log = GateLog()
    for verdict, ms in (("YOU", 10), ("YOU", 30), ("NOT_YOU", 20), ("UNDECIDED", 40)):
        log.add(record(at=2000, verdict=verdict, compute_ms=ms))
    log.add(record(at=10, verdict="YOU"))
    first = log.to_review(since=1000, borderline_below=0.0)[0]
    log.set_review_message(first.id, 9)
    log.label_by_message(9, Label.YOU)

    summary = log.summary(since=1000)
    assert summary["verdicts"] == {"YOU": 2, "NOT_YOU": 1, "UNDECIDED": 1}
    assert summary["labels"] == {"YOU": 1}
    assert (summary["total"], summary["median_compute_ms"]) == (4, 30)
    assert GateLog().summary(since=10_000) == {"verdicts": {}, "labels": {}, "total": 0, "median_compute_ms": None}
