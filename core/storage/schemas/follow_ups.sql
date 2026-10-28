-- Follow-ups (core/storage/follow_ups.py): an agent's own open loops.
-- A goal, a check, a cadence and an end: one row each, opened only through the
-- follow_up tool (tools/follow_up.py), and closed by it, by `!followups cancel`, or
-- when `until` passes. The schedule runner's checks (core/proactive/follow_ups.py)
-- keep the counters and timing below, and team radio's lines keep radio_state.
CREATE TABLE IF NOT EXISTS follow_ups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,                   -- whose loop it is: of the agents, only it closes it
    goal TEXT NOT NULL,                       -- what the user wants, in the agent's words
    check_spec TEXT NOT NULL DEFAULT '{}',    -- JSON: {"type": "model", "what": ...}, or one of the agent's
                                              -- scans with its args: {"type": "<scan>", ...}
    check_key TEXT NOT NULL,                  -- the agent and its check, normalised: two equal keys are the same check
    every_s INTEGER NOT NULL,                 -- the cadence the agent asked for, in seconds
    interval_s INTEGER NOT NULL,              -- the cadence now: every_s at open, then the checks adapt it
    until TEXT NOT NULL,                      -- YYYY-MM-DD, on your wall clock: its last day
    close_when TEXT NOT NULL DEFAULT '',      -- what would end it early, in words; '' if nothing
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    opened_at REAL NOT NULL,
    closed_at REAL,
    close_reason TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',         -- where it was opened, and where what it finds will post
    session_id TEXT NOT NULL DEFAULT '',      -- the session it was opened in
    next_check_at REAL NOT NULL,              -- opened_at + every_s at first
    checks INTEGER NOT NULL DEFAULT 0,        -- checks run
    empty_checks INTEGER NOT NULL DEFAULT 0,  -- checks in a row that found nothing new
    last_checked_at REAL,                     -- when the last check ran
    last_fingerprint TEXT NOT NULL DEFAULT '',-- what the last check found, so only something new posts
    last_found_at REAL,                       -- when a check last found something new
    claimed_at REAL,                          -- when a tick took its due check, claimed once
    finds INTEGER NOT NULL DEFAULT 0,         -- checks that found something new
    last_report TEXT NOT NULL DEFAULT '',     -- what the last find posted, clipped
    closed_by TEXT NOT NULL DEFAULT '',       -- who closed it: agent, you, until or switchboard; '' while open
    radio_state TEXT NOT NULL DEFAULT ''      -- the last team radio line posted for it: '', open or closed
);
CREATE INDEX IF NOT EXISTS idx_follow_ups_due ON follow_ups(status, next_check_at);
CREATE INDEX IF NOT EXISTS idx_follow_ups_agent ON follow_ups(agent_id, status);
-- At most one open follow-up per check: a second open with the same check fails
-- here, atomically, and the store raises DuplicateFollowUp. A closed one doesn't count.
CREATE UNIQUE INDEX IF NOT EXISTS idx_follow_ups_open_check ON follow_ups(check_key) WHERE status = 'open';
