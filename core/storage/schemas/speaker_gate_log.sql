-- The voiceprint gate's records (core/storage/speaker_gate_log.py): one row
-- per utterance it scored, with your review mark. Every category column holds
-- an enum's value, checked here.
CREATE TABLE IF NOT EXISTS speaker_gate_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    at                REAL NOT NULL,              -- when the utterance ended (epoch seconds)
    mode              TEXT NOT NULL CHECK (mode IN ('SHADOW', 'ENFORCE')),
    model             TEXT NOT NULL,              -- the speaker model's file name
    user_id           TEXT,                       -- the Discord account the audio came from
    duration_ms       INTEGER NOT NULL,
    verdict           TEXT NOT NULL CHECK (verdict IN ('YOU', 'NOT_YOU', 'UNDECIDED')),
    score             REAL,                       -- similarity to your voiceprint when decided, else the last one
    decided_after_ms  INTEGER,                    -- the audio it took to decide; NULL if it never did
    scores            TEXT,                       -- JSON [[audio ms, score], ...], one per step
    score_full        REAL,                       -- the similarity over the whole utterance
    compute_ms        INTEGER,                    -- time spent scoring
    voiced_share      REAL,                       -- share of voiced 30 ms frames: near 0 for a whisper
    over_reply        INTEGER NOT NULL DEFAULT 0, -- it began while the agent was speaking
    effect            TEXT CHECK (effect IN ('TURN', 'MERGE', 'BARGE_IN', 'OVERLAP')),  -- what it did
    transcript        TEXT,
    audio_path        TEXT,                       -- kept for would-be rejections and borderline ones
    review_message_id TEXT,                       -- the `!gate review` message about it
    label             TEXT CHECK (label IN ('YOU', 'NOT_YOU')),  -- your mark: ✅ me, ❌ not me
    labeled_at        REAL
);
CREATE INDEX IF NOT EXISTS speaker_gate_log_at ON speaker_gate_log (at);
CREATE INDEX IF NOT EXISTS speaker_gate_log_review ON speaker_gate_log (review_message_id);
