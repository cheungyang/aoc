-- Agent jobs (core/storage/jobs.py): one row per job, from queued to done.
-- JobManager (core/runtime/foundation/jobs.py) caches them and owns the statuses;
-- the table doesn't check them.
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    started REAL NOT NULL,
    updated REAL NOT NULL,
    status TEXT NOT NULL,
    prompt TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated);
