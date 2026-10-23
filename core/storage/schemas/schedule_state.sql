-- Per-schedule state (core/storage/schedule_state.py): when each schedule last
-- succeeded, ran and was skipped; and which scan problems were already posted
-- each day (the local date, YYYY-MM-DD), so a restart doesn't post them again.
CREATE TABLE IF NOT EXISTS schedule_state (
    schedule_id TEXT PRIMARY KEY,
    last_success_at REAL,
    last_run_at REAL,
    last_skip_at REAL,
    last_skip_reason TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_notices (
    schedule_id TEXT NOT NULL,
    issue TEXT NOT NULL,
    day TEXT NOT NULL,
    posted_at REAL NOT NULL,
    PRIMARY KEY (schedule_id, issue, day)
);
