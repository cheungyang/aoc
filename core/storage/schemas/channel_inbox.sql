-- The channel inbox (core/storage/channel_inbox.py): at most one row per
-- text channel, the newest agent post there that you may not have finished with.
-- Every category column holds an enum's value (core/inbox/model.py), checked here.
CREATE TABLE IF NOT EXISTS channel_inbox (
    channel      TEXT PRIMARY KEY,           -- the text channel's name: the voice focus key
    agent_id     TEXT NOT NULL,              -- who answers there
    posted_at    REAL NOT NULL,              -- the newest agent post; skipped 24 h later
    source       TEXT NOT NULL CHECK (source IN ('SCHEDULE', 'REPLY', 'LATE_REPLY')),
    schedule_id  TEXT,                       -- for SCHEDULE rows
    label        TEXT,                       -- how the digest names it
    speech       TEXT,                       -- the speech version; NULL until written
    question     TEXT,                       -- its marked <question>, replayed while AWAITING_REPLY
    excerpt      TEXT,                       -- the post itself, for the speech writer
    priority     TEXT NOT NULL DEFAULT 'DEFAULT' CHECK (priority IN ('LOW', 'DEFAULT', 'HIGHEST')),
    status       TEXT NOT NULL DEFAULT 'UNREAD'
                 CHECK (status IN ('UNREAD', 'AWAITING_REPLY', 'DONE', 'EXPIRED')),
    status_at    REAL,
    status_via   TEXT CHECK (status_via IN ('VOICE', 'TYPED', 'REACTION'))
);
